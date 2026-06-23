"""
OpenAI Realtime API connection helpers.

Handles WebSocket URL, auth headers, and session.update payload for the
Realtime preview API used by call_handler.py.
"""

import json
import logging
from typing import Any

import websockets

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection settings
# ---------------------------------------------------------------------------

OPENAI_REALTIME_MODEL = "gpt-4o-realtime-preview-2024-12-17"

OPENAI_REALTIME_URL = (
    f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
)

# Server events we handle in call_handler.py
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

# Client events
EVENT_INPUT_AUDIO_APPEND = "input_audio_buffer.append"
EVENT_SESSION_UPDATE = "session.update"


def build_connection_headers(api_key: str) -> dict[str, str]:
    """Return auth headers required for the Realtime WebSocket."""
    return {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Beta": "realtime=v1",
    }


def build_session_update(instructions: str) -> dict[str, Any]:
    """
    Build the session.update payload sent immediately after connecting.

    Uses only the fields supported by the current Realtime preview shape.
    """
    return {
        "type": EVENT_SESSION_UPDATE,
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


def build_input_audio_append(audio_b64: str) -> dict[str, str]:
    """Build an input_audio_buffer.append client event."""
    return {
        "type": EVENT_INPUT_AUDIO_APPEND,
        "audio": audio_b64,
    }


async def connect(api_key: str):
    """Open a WebSocket connection to the OpenAI Realtime API."""
    logger.info("Connecting to OpenAI Realtime: %s", OPENAI_REALTIME_URL)
    ws = await websockets.connect(
        OPENAI_REALTIME_URL,
        additional_headers=build_connection_headers(api_key),
    )
    logger.info("OpenAI Realtime WebSocket connected")
    return ws


async def configure_session(ws, instructions: str) -> None:
    """Send session.update with the scenario system prompt."""
    payload = build_session_update(instructions)
    await ws.send(json.dumps(payload))
    logger.info("Sent session.update to OpenAI Realtime")
