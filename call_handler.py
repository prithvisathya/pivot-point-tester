"""
FastAPI bridge between Twilio Media Streams and the OpenAI Realtime GA API.

Receives phone audio from Twilio, converts it for OpenAI, plays the simulated
patient's responses back to the call, and captures transcript/audio for analysis.
"""

import asyncio
import base64
import audioop
import json
import logging
import os
import threading
from typing import Any, Callable, Optional

import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response

from openai_realtime import (
    EVENT_ERROR,
    EVENT_INPUT_AUDIO_TRANSCRIPTION_COMPLETED,
    EVENT_RESPONSE_AUDIO_DELTA,
    EVENT_RESPONSE_AUDIO_TRANSCRIPT_DELTA,
    EVENT_RESPONSE_AUDIO_TRANSCRIPT_DONE,
    EVENT_SESSION_CREATED,
    EVENT_SESSION_UPDATED,
    EVENT_SPEECH_STARTED,
    EVENT_SPEECH_STOPPED,
    RealtimeHandshakeError,
    build_input_audio_append,
    connect as connect_openai,
    initialize_session,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App and globals (set by main.py before each call)
# ---------------------------------------------------------------------------

app = FastAPI()

current_scenario: Optional[dict] = None
current_recorder: Optional[Any] = None
call_complete_event: Optional[threading.Event] = None
on_call_complete: Optional[Callable[..., Any]] = None

# ---------------------------------------------------------------------------
# Audio constants
# ---------------------------------------------------------------------------

TWILIO_SAMPLE_RATE = 8000
OPENAI_SAMPLE_RATE = 24000
GREETING_DELAY_SECONDS = 2


# ---------------------------------------------------------------------------
# Stateful audio conversion
# ---------------------------------------------------------------------------


class AudioConverter:
    """Converts audio between Twilio mulaw 8 kHz and OpenAI PCM16 24 kHz."""

    def __init__(self) -> None:
        self._twilio_to_openai_state: Optional[tuple] = None
        self._openai_to_twilio_state: Optional[tuple] = None

    def twilio_to_openai(self, audio_b64: str) -> tuple[str, bytes]:
        mulaw_bytes = base64.b64decode(audio_b64)
        pcm16_8k = audioop.ulaw2lin(mulaw_bytes, 2)
        pcm16_24k, self._twilio_to_openai_state = audioop.ratecv(
            pcm16_8k,
            2,
            1,
            TWILIO_SAMPLE_RATE,
            OPENAI_SAMPLE_RATE,
            self._twilio_to_openai_state,
        )
        return base64.b64encode(pcm16_24k).decode("utf-8"), pcm16_24k

    def openai_to_twilio(self, audio_b64: str) -> tuple[str, bytes]:
        pcm16_24k = base64.b64decode(audio_b64)
        pcm16_8k, self._openai_to_twilio_state = audioop.ratecv(
            pcm16_24k,
            2,
            1,
            OPENAI_SAMPLE_RATE,
            TWILIO_SAMPLE_RATE,
            self._openai_to_twilio_state,
        )
        mulaw_bytes = audioop.lin2ulaw(pcm16_8k, 2)
        return base64.b64encode(mulaw_bytes).decode("utf-8"), pcm16_24k


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Server started (uvicorn should bind to port 5050)")


@app.get("/")
async def root() -> HTMLResponse:
    return HTMLResponse("<h1>Pivot Point Tester</h1><p>Server is running.</p>")


@app.post("/incoming-call")
async def incoming_call(request: Request) -> Response:
    logger.info("Incoming call received from %s", request.client)

    ngrok_url = os.getenv("NGROK_URL", "").rstrip("/")
    if not ngrok_url:
        logger.error("NGROK_URL is not set in environment")
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response><Say>Server configuration error.</Say></Response>',
            media_type="application/xml",
            status_code=500,
        )

    ws_url = ngrok_url.replace("https://", "wss://").replace("http://", "ws://")
    stream_url = f"{ws_url}/media-stream"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}" />
    </Connect>
</Response>"""

    logger.info("Returning TwiML with stream URL: %s", stream_url)
    return Response(content=twiml, media_type="application/xml")


# ---------------------------------------------------------------------------
# WebSocket: Twilio Media Stream ↔ OpenAI Realtime bridge
# ---------------------------------------------------------------------------


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket) -> None:
    await websocket.accept()

    stream_sid: Optional[str] = None
    transcript_lines: list[str] = []
    audio_chunks: list[bytes] = []
    bot_transcript_buffer = ""
    scenario = current_scenario or {}
    recorder = current_recorder
    converter = AudioConverter()
    call_errored = False

    # Audio forwarding is blocked until handshake + greeting delay complete.
    audio_forwarding_enabled = asyncio.Event()

    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_api_key:
        logger.error("OPENAI_API_KEY is not set")
        await websocket.close()
        return

    instructions = scenario.get(
        "system_prompt",
        "You are a patient calling a medical office. Stay in character.",
    )

    openai_ws = None
    greeting_task: Optional[asyncio.Task] = None

    try:
        # --- OpenAI handshake (session.created -> update -> updated) ------
        openai_ws = await connect_openai(openai_api_key)
        await initialize_session(openai_ws, instructions)

        async def enable_audio_after_greeting() -> None:
            """Let the office agent finish its greeting before we respond."""
            await asyncio.sleep(GREETING_DELAY_SECONDS)
            audio_forwarding_enabled.set()
            logger.info(
                "Greeting delay complete (%ss) — forwarding Twilio audio to OpenAI",
                GREETING_DELAY_SECONDS,
            )

        greeting_task = asyncio.create_task(enable_audio_after_greeting())

        # --- Twilio → OpenAI ------------------------------------------------
        async def handle_twilio_messages() -> None:
            nonlocal stream_sid

            async for raw_message in websocket.iter_text():
                data = json.loads(raw_message)
                event = data.get("event")

                if event == "start":
                    stream_sid = data.get("start", {}).get("streamSid") or data.get(
                        "streamSid"
                    )
                    logger.info("Media stream started — streamSid=%s", stream_sid)

                elif event == "media":
                    payload = data.get("media", {}).get("payload", "")
                    if not payload:
                        continue

                    await audio_forwarding_enabled.wait()

                    openai_audio_b64, pcm_bytes = converter.twilio_to_openai(payload)
                    audio_chunks.append(pcm_bytes)

                    await openai_ws.send(
                        json.dumps(build_input_audio_append(openai_audio_b64))
                    )

                elif event == "stop":
                    logger.info("Media stream stop event received")
                    break

                elif event == "mark":
                    mark_name = data.get("mark", {}).get("name", "")
                    logger.debug("Mark event acknowledged: %s", mark_name)

                else:
                    logger.debug("Unhandled Twilio event: %s", event)

        # --- OpenAI → Twilio ------------------------------------------------
        async def handle_openai_messages() -> None:
            nonlocal bot_transcript_buffer, call_errored

            async for raw_message in openai_ws:
                data = json.loads(raw_message)
                event_type = data.get("type", "")

                if event_type == EVENT_SESSION_CREATED:
                    logger.info("OpenAI session.created (post-handshake)")

                elif event_type == EVENT_SESSION_UPDATED:
                    logger.info("OpenAI session.updated (post-handshake)")

                elif event_type == EVENT_RESPONSE_AUDIO_DELTA:
                    delta_b64 = data.get("delta", "")
                    if not delta_b64 or not stream_sid:
                        continue

                    twilio_audio_b64, pcm_bytes = converter.openai_to_twilio(
                        delta_b64
                    )
                    audio_chunks.append(pcm_bytes)
                    if recorder is not None:
                        recorder.add_audio_chunk(pcm_bytes)

                    await websocket.send_text(
                        json.dumps(
                            {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": twilio_audio_b64},
                            }
                        )
                    )

                elif event_type == EVENT_RESPONSE_AUDIO_TRANSCRIPT_DELTA:
                    bot_transcript_buffer += data.get("delta", "")

                elif event_type == EVENT_RESPONSE_AUDIO_TRANSCRIPT_DONE:
                    transcript_text = data.get("transcript", bot_transcript_buffer)
                    bot_transcript_buffer = ""
                    line = f"[PATIENT BOT] {transcript_text}"
                    transcript_lines.append(line)
                    if recorder is not None:
                        recorder.add_transcript_line("PATIENT BOT", transcript_text)
                    logger.info("Bot utterance completed: %s", transcript_text)

                elif event_type == EVENT_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
                    transcript_text = data.get("transcript", "")
                    line = f"[AGENT] {transcript_text}"
                    transcript_lines.append(line)
                    if recorder is not None:
                        recorder.add_transcript_line("AGENT", transcript_text)
                    logger.info("Agent utterance transcribed: %s", transcript_text)

                elif event_type == EVENT_SPEECH_STARTED:
                    logger.info("Agent started speaking")

                elif event_type == EVENT_SPEECH_STOPPED:
                    logger.info("Agent stopped speaking")

                elif event_type == EVENT_ERROR:
                    call_errored = True
                    logger.error(
                        "OpenAI Realtime error: %s",
                        data.get("error", data),
                    )

                else:
                    logger.debug("Unhandled OpenAI event: %s", event_type)

        twilio_task = asyncio.create_task(handle_twilio_messages())
        openai_task = asyncio.create_task(handle_openai_messages())

        done, pending = await asyncio.wait(
            [twilio_task, openai_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except RealtimeHandshakeError as exc:
        call_errored = True
        logger.error("OpenAI Realtime handshake failed: %s", exc)

    except WebSocketDisconnect:
        logger.info("Twilio WebSocket disconnected")

    except websockets.exceptions.ConnectionClosed as exc:
        call_errored = True
        logger.error("OpenAI WebSocket disconnected unexpectedly: %s", exc)

    except Exception as exc:
        call_errored = True
        logger.error("Media stream error: %s", exc, exc_info=True)

    finally:
        if greeting_task is not None:
            greeting_task.cancel()
            try:
                await greeting_task
            except asyncio.CancelledError:
                pass

        if openai_ws is not None:
            try:
                await openai_ws.close()
            except Exception:
                pass

        logger.info("Call ended — captured %d transcript lines", len(transcript_lines))

        if recorder is not None:
            try:
                if call_errored:
                    recorder.save_partial()
                else:
                    recorder.save()
            except Exception as exc:
                logger.error("Recorder save failed: %s", exc, exc_info=True)

        if call_complete_event is not None:
            call_complete_event.set()

        if on_call_complete is not None:
            try:
                on_call_complete(
                    transcript_lines=transcript_lines,
                    audio_chunks=audio_chunks,
                    scenario=scenario,
                )
            except Exception as exc:
                logger.error("on_call_complete callback failed: %s", exc, exc_info=True)
