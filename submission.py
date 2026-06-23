"""
Curate quality call recordings for challenge submission.

Successful calls are copied from recordings/ into submission_recordings/
when they meet minimum quality thresholds (audio, duration, both speakers).
"""

import logging
import os
import re
import shutil
from typing import Optional

logger = logging.getLogger(__name__)

SUBMISSION_DIR = "submission_recordings"
AUDIO_MP3_FILENAME = "call.mp3"
TRANSCRIPT_FILENAME = "transcript.txt"

# Ten diverse scenarios covering the challenge requirements.
SUBMISSION_SCENARIO_IDS = [1, 2, 5, 6, 7, 8, 9, 12, 14, 22]

MIN_AUDIO_BYTES = 10_000
MIN_SPEAKER_TURNS = 4
MIN_DURATION_SECONDS = 60

_SCENARIO_ID_PATTERN = re.compile(r"scenario_(\d+)", re.IGNORECASE)
_TIMESTAMP_PATTERN = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\]")
_SPEAKER_LINE_PATTERN = re.compile(r"\[(AGENT|PATIENT BOT)\]:")


def ensure_submission_dir() -> str:
    os.makedirs(SUBMISSION_DIR, exist_ok=True)
    return SUBMISSION_DIR


def _parse_duration_seconds(transcript_text: str) -> int:
    """Return the latest timestamp found in the transcript body."""
    latest = 0
    for match in _TIMESTAMP_PATTERN.finditer(transcript_text):
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        total = hours * 3600 + minutes * 60 + seconds
        latest = max(latest, total)
    return latest


def assess_call_quality(call_folder: str) -> tuple[bool, str]:
    """
    Decide whether a call folder is good enough for submission.

    Returns (is_quality, reason).
    """
    transcript_path = os.path.join(call_folder, TRANSCRIPT_FILENAME)
    audio_path = os.path.join(call_folder, AUDIO_MP3_FILENAME)

    if not os.path.isfile(transcript_path):
        return False, "missing transcript.txt"

    if not os.path.isfile(audio_path):
        return False, "missing call.mp3"

    if os.path.getsize(audio_path) < MIN_AUDIO_BYTES:
        return False, "audio file too small"

    with open(transcript_path, encoding="utf-8") as f:
        transcript_text = f.read()

    if "PARTIAL SAVE" in transcript_text:
        return False, "partial save"

    if "(No transcript captured)" in transcript_text:
        return False, "empty transcript"

    speaker_turns = len(_SPEAKER_LINE_PATTERN.findall(transcript_text))
    if speaker_turns < MIN_SPEAKER_TURNS:
        return False, f"only {speaker_turns} speaker turns"

    duration_seconds = _parse_duration_seconds(transcript_text)
    if duration_seconds < MIN_DURATION_SECONDS:
        return False, f"conversation too short ({duration_seconds}s)"

    return True, "ok"


def get_submitted_scenario_ids() -> set[int]:
    """Return scenario IDs already present in submission_recordings/."""
    if not os.path.isdir(SUBMISSION_DIR):
        return set()

    scenario_ids: set[int] = set()
    for name in os.listdir(SUBMISSION_DIR):
        folder = os.path.join(SUBMISSION_DIR, name)
        if not os.path.isdir(folder):
            continue
        match = _SCENARIO_ID_PATTERN.search(name)
        if match:
            scenario_ids.add(int(match.group(1)))
    return scenario_ids


def _slugify_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower())
    return slug.strip("_") or "unknown"


def _next_submission_number() -> int:
    ensure_submission_dir()
    highest = 0
    pattern = re.compile(r"^submission_(\d{2})")

    for name in os.listdir(SUBMISSION_DIR):
        match = pattern.match(name)
        if match:
            highest = max(highest, int(match.group(1)))

    return highest + 1


def build_submission_folder_name(
    submission_number: int, scenario_id: int, patient_name: str
) -> str:
    slug = _slugify_name(patient_name)
    return f"submission_{submission_number:02d}_scenario_{scenario_id:02d}_{slug}"


def promote_call(call_folder: str, scenario: dict) -> Optional[str]:
    """
    Copy a quality call into submission_recordings/.

    Returns the destination folder path, or None if the call did not qualify.
    """
    is_quality, reason = assess_call_quality(call_folder)
    if not is_quality:
        logger.info("Call not promoted (%s): %s", reason, call_folder)
        return None

    scenario_id = int(scenario.get("id", 0))
    patient_name = scenario.get("name", "unknown")
    submitted_ids = get_submitted_scenario_ids()
    if scenario_id in submitted_ids:
        logger.info(
            "Scenario %s already has a submission recording — skipping promote",
            scenario_id,
        )
        return None

    submission_number = _next_submission_number()
    dest_name = build_submission_folder_name(
        submission_number, scenario_id, patient_name
    )
    dest_folder = os.path.join(ensure_submission_dir(), dest_name)

    shutil.copytree(call_folder, dest_folder)
    logger.info("Promoted submission recording → %s", dest_folder)
    return dest_folder


def promote_existing_call(call_folder: str, scenario: dict) -> Optional[str]:
    """CLI/helper entry point for manually promoting a recordings/ folder."""
    dest = promote_call(call_folder, scenario)
    if dest:
        print(f"Promoted to {dest}")
    else:
        is_quality, reason = assess_call_quality(call_folder)
        if not is_quality:
            print(f"Not promoted: {reason}")
        else:
            print("Not promoted: scenario already submitted")
    return dest


def print_submission_status() -> None:
    """Print which submission scenarios are complete."""
    submitted = get_submitted_scenario_ids()
    missing = [sid for sid in SUBMISSION_SCENARIO_IDS if sid not in submitted]

    print()
    print("Submission progress (10 calls required):")
    print(f"  Complete: {len(submitted)}/{len(SUBMISSION_SCENARIO_IDS)}")
    if submitted:
        print(f"  Submitted scenario IDs: {sorted(submitted)}")
    if missing:
        print(f"  Still needed: {missing}")
    print(f"  Folder: {SUBMISSION_DIR}/")
    print()
