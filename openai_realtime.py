"""
OpenAI Realtime API connection helpers (GA endpoint).

Uses raw websockets (not the OpenAI Python SDK) for bidirectional audio streaming.
Handshake flow:
  connect -> wait session.created -> send session.update -> wait session.updated
"""

import asyncio
import json
import logging
from typing import Any

import websockets

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection settings
# ---------------------------------------------------------------------------

OPENAI_REALTIME_MODEL = "gpt-realtime-2"

OPENAI_REALTIME_URL = (
    f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
)

HANDSHAKE_TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------------
# OpenAI server event types
# ---------------------------------------------------------------------------

EVENT_SESSION_CREATED = "session.created"
EVENT_SESSION_UPDATED = "session.updated"
EVENT_RESPONSE_AUDIO_DELTA = "response.audio.delta"
EVENT_RESPONSE_AUDIO_TRANSCRIPT_DELTA = "response.audio_transcript.delta"
EVENT_RESPONSE_AUDIO_TRANSCRIPT_DONE = "response.audio_transcript.done"
EVENT_INPUT_AUDIO_TRANSCRIPTION_COMPLETED = (
    "conversation.item.input_audio_transcription.completed"
)
EVENT_SPEECH_STARTED = "input_audio_buffer.speech_started"
EVENT_SPEECH_STOPPED = "input_audio_buffer.speech_stopped"
EVENT_ERROR = "error"

# ---------------------------------------------------------------------------
# OpenAI client event types
# ---------------------------------------------------------------------------

EVENT_SESSION_UPDATE = "session.update"
EVENT_INPUT_AUDIO_APPEND = "input_audio_buffer.append"


class RealtimeHandshakeError(Exception):
    """Raised when the OpenAI Realtime session handshake fails."""


def build_connection_headers(api_key: str) -> dict[str, str]:
    """Return auth headers for the GA Realtime WebSocket connection."""
    return {
        "Authorization": f"Bearer {api_key}",
    }


def build_session_update(instructions: str) -> dict[str, Any]:
    """
    Build session.update payload for the GA Realtime API.

    Sent after session.created is received during handshake.
    """
    return {
        "type": EVENT_SESSION_UPDATE,
        "session": {
            "modalities": ["text", "audio"],
            "instructions": instructions,
            "voice": "alloy",
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": "whisper-1",
            },
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 800,
            },
            "temperature": 0.8,
            "max_response_output_tokens": 1024,
        },
    }


def build_input_audio_append(audio_b64: str) -> dict[str, str]:
    """Build input_audio_buffer.append client event."""
    return {
        "type": EVENT_INPUT_AUDIO_APPEND,
        "audio": audio_b64,
    }


async def connect(api_key: str):
    """
    Open a raw WebSocket connection to the OpenAI Realtime GA API.

    Uses the websockets library with extra_headers (not the OpenAI SDK).
    No OpenAI-Beta header — beta Realtime API is no longer supported.
    """
    logger.info("Connecting to OpenAI Realtime: %s", OPENAI_REALTIME_URL)
    headers = build_connection_headers(api_key)

    try:
        ws = await websockets.connect(
            OPENAI_REALTIME_URL,
            extra_headers=headers,
        )
    except TypeError:
        # Newer websockets releases renamed extra_headers -> additional_headers.
        ws = await websockets.connect(
            OPENAI_REALTIME_URL,
            additional_headers=headers,
        )

    logger.info("OpenAI Realtime WebSocket connected")
    return ws


async def _wait_for_event(ws, expected_type: str) -> dict[str, Any]:
    """Read messages until the expected event type is received."""
    deadline = asyncio.get_running_loop().time() + HANDSHAKE_TIMEOUT_SECONDS

    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise RealtimeHandshakeError(
                f"Timed out waiting for OpenAI event: {expected_type}"
            )

        raw_message = await asyncio.wait_for(ws.recv(), timeout=remaining)
        data = json.loads(raw_message)
        event_type = data.get("type", "")

        if event_type == EVENT_ERROR:
            error_detail = data.get("error", data)
            raise RealtimeHandshakeError(f"OpenAI Realtime error: {error_detail}")

        if event_type == expected_type:
            return data

        logger.debug(
            "Ignoring OpenAI event during handshake: %s",
            event_type,
        )


async def initialize_session(ws, instructions: str) -> None:
    """
    Complete the Realtime session handshake.

    1. Wait for session.created (do NOT send session.update before this)
    2. Send session.update with scenario instructions
    3. Wait for session.updated confirmation
    """
    logger.info("Waiting for session.created from OpenAI...")
    await _wait_for_event(ws, EVENT_SESSION_CREATED)
    logger.info("Received session.created")

    session_update = build_session_update(instructions)
    await ws.send(json.dumps(session_update))
    logger.info("Sent session.update to OpenAI Realtime")

    logger.info("Waiting for session.updated from OpenAI...")
    await _wait_for_event(ws, EVENT_SESSION_UPDATED)
    logger.info("Received session.updated — session is ready for audio")
