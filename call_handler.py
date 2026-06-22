"""
FastAPI bridge between Twilio Media Streams and the OpenAI Realtime API.

Receives phone audio from Twilio, converts it for OpenAI, plays the simulated
patient's responses back to the call, and captures transcript/audio for analysis.
"""

import asyncio
import base64
import audioop
import json
import logging
import os
from typing import Any, Callable, Optional

import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response

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

# Scenario dict with keys like id, name, system_prompt, etc.
current_scenario: Optional[dict] = None

# Callback invoked when a call ends: fn(transcript_lines, audio_chunks, scenario)
on_call_complete: Optional[Callable[..., Any]] = None

# ---------------------------------------------------------------------------
# Audio constants
# ---------------------------------------------------------------------------

TWILIO_SAMPLE_RATE = 8000
OPENAI_SAMPLE_RATE = 24000
GREETING_DELAY_SECONDS = 2

OPENAI_REALTIME_URL = (
    "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"
)


# ---------------------------------------------------------------------------
# Stateful audio conversion (ratecv needs persistent state for streaming)
# ---------------------------------------------------------------------------


class AudioConverter:
    """Converts audio between Twilio mulaw 8 kHz and OpenAI PCM16 24 kHz."""

    def __init__(self) -> None:
        self._twilio_to_openai_state: Optional[tuple] = None
        self._openai_to_twilio_state: Optional[tuple] = None

    def twilio_to_openai(self, audio_b64: str) -> tuple[str, bytes]:
        """
        Twilio → OpenAI pipeline:
          base64 mulaw 8 kHz → PCM16 8 kHz → PCM16 24 kHz → base64
        Returns (base64_for_openai, raw_pcm16_24k_bytes).
        """
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
        """
        OpenAI → Twilio pipeline:
          base64 PCM16 24 kHz → PCM16 8 kHz → mulaw → base64
        Returns (base64_for_twilio, raw_pcm16_24k_bytes for recording).
        """
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
# OpenAI session configuration
# ---------------------------------------------------------------------------


def build_session_update(instructions: str) -> dict:
    """Build the session.update payload sent right after connecting to OpenAI."""
    return {
        "type": "session.update",
        "session": {
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 800,
            },
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": "whisper-1",
            },
            "voice": "alloy",
            "instructions": instructions,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
        },
    }


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Server started (uvicorn should bind to port 5050)")


@app.get("/")
async def root() -> HTMLResponse:
    """Simple health-check page."""
    return HTMLResponse("<h1>Pivot Point Tester</h1><p>Server is running.</p>")


@app.post("/incoming-call")
async def incoming_call(request: Request) -> Response:
    """
    Twilio webhook: return TwiML that opens a Media Stream to our WebSocket.

    Twilio POSTs here when a call connects; we respond with XML telling Twilio
    to stream audio to /media-stream.
    """
    logger.info("Incoming call received from %s", request.client)

    ngrok_url = os.getenv("NGROK_URL", "").rstrip("/")
    if not ngrok_url:
        logger.error("NGROK_URL is not set in environment")
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response><Say>Server configuration error.</Say></Response>',
            media_type="application/xml",
            status_code=500,
        )

    # Twilio needs a wss:// URL for the media stream.
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
    """
    Bidirectional audio bridge between a Twilio call and OpenAI Realtime.

    Twilio sends mulaw 8 kHz audio; OpenAI expects PCM16 24 kHz and responds
    in the same format. We convert in both directions and capture transcript
    plus audio chunks for the recorder.
    """
    await websocket.accept()

    # Per-call state
    stream_sid: Optional[str] = None
    transcript_lines: list[str] = []
    audio_chunks: list[bytes] = []
    bot_transcript_buffer = ""
    scenario = current_scenario or {}
    converter = AudioConverter()

    # Block Twilio→OpenAI audio until the agent finishes its greeting.
    greeting_ready = asyncio.Event()

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

    try:
        # --- Connect to OpenAI Realtime -----------------------------------
        logger.info("Connecting to OpenAI Realtime API...")
        openai_ws = await websockets.connect(
            OPENAI_REALTIME_URL,
            additional_headers={
                "Authorization": f"Bearer {openai_api_key}",
                "OpenAI-Beta": "realtime=v1",
            },
        )
        logger.info("OpenAI connected")

        # Configure the session with the scenario's system prompt.
        await openai_ws.send(json.dumps(build_session_update(instructions)))

        # Start the 2-second greeting delay in the background.
        async def enable_audio_after_greeting() -> None:
            await asyncio.sleep(GREETING_DELAY_SECONDS)
            greeting_ready.set()
            logger.info(
                "Greeting delay complete (%ss) — forwarding audio to OpenAI",
                GREETING_DELAY_SECONDS,
            )

        greeting_task = asyncio.create_task(enable_audio_after_greeting())

        # --- Twilio → OpenAI loop -----------------------------------------
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

                    # Wait for greeting delay before sending agent audio to OpenAI.
                    await greeting_ready.wait()

                    openai_audio_b64, pcm_bytes = converter.twilio_to_openai(payload)
                    audio_chunks.append(pcm_bytes)

                    await openai_ws.send(
                        json.dumps(
                            {
                                "type": "input_audio_buffer.append",
                                "audio": openai_audio_b64,
                            }
                        )
                    )

                elif event == "stop":
                    logger.info("Media stream stop event received")
                    break

                elif event == "mark":
                    mark_name = data.get("mark", {}).get("name", "")
                    logger.debug("Mark event acknowledged: %s", mark_name)

                else:
                    logger.debug("Unhandled Twilio event: %s", event)

        # --- OpenAI → Twilio loop -----------------------------------------
        async def handle_openai_messages() -> None:
            nonlocal bot_transcript_buffer

            async for raw_message in openai_ws:
                data = json.loads(raw_message)
                event_type = data.get("type", "")

                if event_type == "session.updated":
                    logger.info("Session updated and ready")

                elif event_type == "response.audio.delta":
                    delta_b64 = data.get("delta", "")
                    if not delta_b64 or not stream_sid:
                        continue

                    twilio_audio_b64, pcm_bytes = converter.openai_to_twilio(
                        delta_b64
                    )
                    audio_chunks.append(pcm_bytes)

                    await websocket.send_text(
                        json.dumps(
                            {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": twilio_audio_b64},
                            }
                        )
                    )

                elif event_type == "response.audio_transcript.delta":
                    bot_transcript_buffer += data.get("delta", "")

                elif event_type == "response.audio_transcript.done":
                    transcript_text = data.get("transcript", bot_transcript_buffer)
                    bot_transcript_buffer = ""
                    line = f"[PATIENT BOT] {transcript_text}"
                    transcript_lines.append(line)
                    logger.info("Bot utterance completed: %s", transcript_text)

                elif (
                    event_type
                    == "conversation.item.input_audio_transcription.completed"
                ):
                    transcript_text = data.get("transcript", "")
                    line = f"[AGENT] {transcript_text}"
                    transcript_lines.append(line)
                    logger.info("Agent utterance transcribed: %s", transcript_text)

                elif event_type == "input_audio_buffer.speech_started":
                    logger.info("Agent started speaking")

                elif event_type == "input_audio_buffer.speech_stopped":
                    logger.info("Agent stopped speaking")

                elif event_type == "error":
                    logger.error(
                        "OpenAI Realtime error: %s",
                        data.get("error", data),
                    )

                else:
                    logger.debug("Unhandled OpenAI event: %s", event_type)

        # Run both directions concurrently; end when either side disconnects.
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

        greeting_task.cancel()
        try:
            await greeting_task
        except asyncio.CancelledError:
            pass

    except WebSocketDisconnect:
        logger.info("Twilio WebSocket disconnected")

    except websockets.exceptions.ConnectionClosed as exc:
        logger.error("OpenAI WebSocket disconnected unexpectedly: %s", exc)

    except Exception as exc:
        logger.error("Media stream error: %s", exc, exc_info=True)

    finally:
        # Close OpenAI connection if still open.
        if openai_ws is not None:
            try:
                await openai_ws.close()
            except Exception:
                pass

        logger.info("Call ended — captured %d transcript lines", len(transcript_lines))

        if on_call_complete is not None:
            try:
                on_call_complete(
                    transcript_lines=transcript_lines,
                    audio_chunks=audio_chunks,
                    scenario=scenario,
                )
            except Exception as exc:
                logger.error("on_call_complete callback failed: %s", exc, exc_info=True)
