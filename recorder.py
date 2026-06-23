"""
Saves call audio and transcripts for each simulated patient test call.

Creates numbered folders under recordings/, writes timestamped transcripts,
and exports combined PCM16 audio as MP3 (with WAV fallback).
"""

import io
import logging
import os
import re
from datetime import datetime
from typing import Optional

from pydub import AudioSegment

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RECORDINGS_DIR = "recordings"
AUDIO_SAMPLE_RATE = 24000
AUDIO_SAMPLE_WIDTH = 2  # 16-bit PCM
AUDIO_CHANNELS = 1
TRANSCRIPT_FILENAME = "transcript.txt"
AUDIO_MP3_FILENAME = "call.mp3"
PATIENT_BOT_MP3_FILENAME = "patient_bot.mp3"
AUDIO_WAV_FILENAME = "call.wav"
CALL_SID_FILENAME = "call_sid.txt"

HEADER_RULE = "=" * 48


# ---------------------------------------------------------------------------
# Folder numbering helpers
# ---------------------------------------------------------------------------


def _ensure_recordings_dir() -> str:
    """Create recordings/ if missing and return its path."""
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    return RECORDINGS_DIR


def _next_call_number(recordings_dir: str) -> int:
    """
    Count existing call_XX folders and return the next number.

    Only matches base folders like call_01, call_02 (not call_01_retry).
    """
    if not os.path.isdir(recordings_dir):
        return 1

    highest = 0
    pattern = re.compile(r"^call_(\d{2})$")

    for name in os.listdir(recordings_dir):
        folder_path = os.path.join(recordings_dir, name)
        if not os.path.isdir(folder_path):
            continue
        match = pattern.match(name)
        if match:
            highest = max(highest, int(match.group(1)))

    return highest + 1


def _allocate_call_folder(recordings_dir: str, call_number: int) -> str:
    """
    Return a unique folder path for this call.

    Uses call_XX by default; if that exists, appends _retry, _retry2, etc.
    """
    base_name = f"call_{call_number:02d}"
    candidate = os.path.join(recordings_dir, base_name)

    if not os.path.exists(candidate):
        return candidate

    retry = 1
    while True:
        if retry == 1:
            suffix = "_retry"
        else:
            suffix = f"_retry{retry}"
        candidate = os.path.join(recordings_dir, f"{base_name}{suffix}")
        if not os.path.exists(candidate):
            return candidate
        retry += 1


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as HH:MM:SS relative to call start."""
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


# ---------------------------------------------------------------------------
# CallRecorder
# ---------------------------------------------------------------------------


class CallRecorder:
    """Records transcript lines and audio chunks for a single test call."""

    def __init__(self, scenario: dict) -> None:
        """
        Set up a new recording session for the given scenario.

        Creates the call folder immediately and records the start time.
        """
        self.scenario = scenario
        self._transcript_lines: list[str] = []
        self._patient_bot_chunks: list[bytes] = []
        self._call_sid: Optional[str] = None
        self._start_time = datetime.now()
        self._call_start_monotonic = datetime.now()

        recordings_dir = _ensure_recordings_dir()
        call_number = _next_call_number(recordings_dir)
        self._call_folder = _allocate_call_folder(recordings_dir, call_number)
        os.makedirs(self._call_folder, exist_ok=True)

        scenario_id = scenario.get("id", "unknown")
        scenario_name = scenario.get("name", "unknown")
        logger.info(
            "Recording started for scenario %s (%s) → %s",
            scenario_id,
            scenario_name,
            self._call_folder,
        )

    def add_transcript_line(self, speaker: str, text: str) -> None:
        """
        Append a timestamped transcript line.

        speaker must be "AGENT" or "PATIENT BOT".
        Timestamps are relative to call start.
        """
        if speaker not in ("AGENT", "PATIENT BOT"):
            logger.warning("Unexpected speaker label %r — storing anyway", speaker)

        elapsed = (datetime.now() - self._call_start_monotonic).total_seconds()
        timestamp = _format_elapsed(elapsed)
        line = f"[{timestamp}] [{speaker}]: {text.strip()}"

        self._transcript_lines.append(line)
        print(line)

    def add_audio_chunk(self, audio_bytes: bytes) -> None:
        """Append patient-bot PCM16 audio (OpenAI output only)."""
        if audio_bytes:
            self._patient_bot_chunks.append(audio_bytes)

    def set_call_sid(self, call_sid: str) -> None:
        """Store Twilio call SID for recording download and transcript metadata."""
        self._call_sid = call_sid
        sid_path = os.path.join(self._call_folder, CALL_SID_FILENAME)
        with open(sid_path, "w", encoding="utf-8") as sid_file:
            sid_file.write(call_sid)

    def get_call_folder(self) -> str:
        """Return the path to this call's folder."""
        return self._call_folder

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_header(self, partial: bool) -> str:
        """Build the transcript header block."""
        scenario_id = self.scenario.get("id", "unknown")
        patient_name = self.scenario.get("name", "unknown")
        patient_dob = self.scenario.get("dob", "unknown")
        scenario_goal = self.scenario.get("goal", "unknown")
        call_date = self._start_time.strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            HEADER_RULE,
            "PIVOT POINT ORTHOPEDICS - AI AGENT TEST CALL",
            HEADER_RULE,
            f"Scenario ID: {scenario_id}",
            f"Patient Name: {patient_name}",
            f"Date of Birth: {patient_dob}",
            f"Goal: {scenario_goal}",
            f"Call Date: {call_date}",
            f"Recording: {AUDIO_MP3_FILENAME} (full two-party call via Twilio)",
            f"Patient bot track: {PATIENT_BOT_MP3_FILENAME} (simulated patient audio only)",
        ]

        if self._call_sid:
            lines.append(f"Twilio Call SID: {self._call_sid}")

        if partial:
            lines.append("Status: PARTIAL SAVE - call ended unexpectedly")

        lines.append(HEADER_RULE)
        return "\n".join(lines)

    def _build_transcript_body(self) -> str:
        """Join transcript lines with a blank line between each speaker turn."""
        if not self._transcript_lines:
            return "(No transcript captured)"

        return "\n\n".join(self._transcript_lines)

    def _save_transcript(self, partial: bool) -> None:
        """Write transcript.txt to the call folder."""
        transcript_path = os.path.join(self._call_folder, TRANSCRIPT_FILENAME)
        content = self._build_header(partial) + "\n\n" + self._build_transcript_body()

        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.write("\n")

        logger.info("Transcript saved → %s", transcript_path)

    def _save_patient_bot_audio(self) -> str:
        """Save patient-bot-only audio from the OpenAI stream."""
        if not self._patient_bot_chunks:
            logger.warning("No patient-bot audio chunks for %s", self._call_folder)
            return ""

        combined = b"".join(self._patient_bot_chunks)
        audio = AudioSegment.from_raw(
            io.BytesIO(combined),
            sample_width=AUDIO_SAMPLE_WIDTH,
            frame_rate=AUDIO_SAMPLE_RATE,
            channels=AUDIO_CHANNELS,
        )

        mp3_path = os.path.join(self._call_folder, PATIENT_BOT_MP3_FILENAME)
        try:
            audio.export(mp3_path, format="mp3")
            logger.info("Patient-bot audio saved → %s", mp3_path)
            return PATIENT_BOT_MP3_FILENAME
        except Exception as exc:
            logger.warning("Patient-bot MP3 export failed: %s", exc)
            return ""

    def _save_audio(self, full_call_mp3_path: Optional[str] = None) -> str:
        """
        Save call audio files.

        Prefers Twilio's full-call MP3 (both parties). Falls back to patient-bot
        stream audio if the Twilio download is unavailable.
        """
        self._save_patient_bot_audio()

        if full_call_mp3_path and os.path.isfile(full_call_mp3_path):
            return AUDIO_MP3_FILENAME

        if not self._patient_bot_chunks:
            return ""

        combined = b"".join(self._patient_bot_chunks)
        audio = AudioSegment.from_raw(
            io.BytesIO(combined),
            sample_width=AUDIO_SAMPLE_WIDTH,
            frame_rate=AUDIO_SAMPLE_RATE,
            channels=AUDIO_CHANNELS,
        )

        mp3_path = os.path.join(self._call_folder, AUDIO_MP3_FILENAME)
        try:
            audio.export(mp3_path, format="mp3")
            logger.warning(
                "Twilio recording unavailable — %s contains patient-bot audio only",
                AUDIO_MP3_FILENAME,
            )
            return AUDIO_MP3_FILENAME
        except Exception as exc:
            logger.warning("MP3 export failed (%s) — falling back to WAV", exc)

        wav_path = os.path.join(self._call_folder, AUDIO_WAV_FILENAME)
        try:
            audio.export(wav_path, format="wav")
            logger.info("Audio saved as WAV → %s", wav_path)
            return AUDIO_WAV_FILENAME
        except Exception as exc:
            logger.error("WAV export also failed: %s", exc, exc_info=True)
            return ""

    def _write_outputs(self, partial: bool, full_call_mp3_path: Optional[str] = None) -> str:
        """Write transcript and audio; never crash if audio saving fails."""
        label = "partial" if partial else "complete"
        logger.info("Saving %s recording for %s", label, self._call_folder)

        try:
            self._save_transcript(partial)
        except Exception as exc:
            logger.error("Failed to save transcript: %s", exc, exc_info=True)

        try:
            audio_file = self._save_audio(full_call_mp3_path)
            if audio_file:
                logger.info("Recording format used: %s", audio_file)
        except Exception as exc:
            logger.error("Failed to save audio: %s", exc, exc_info=True)

        logger.info("Recording saved successfully → %s", self._call_folder)
        return self._call_folder

    def save(self, full_call_mp3_path: Optional[str] = None) -> str:
        """Write transcript.txt and call.mp3 (or call.wav fallback)."""
        return self._write_outputs(partial=False, full_call_mp3_path=full_call_mp3_path)

    def save_partial(self, full_call_mp3_path: Optional[str] = None) -> str:
        """Same as save(), but marks the transcript as a partial/unexpected save."""
        return self._write_outputs(partial=True, full_call_mp3_path=full_call_mp3_path)
