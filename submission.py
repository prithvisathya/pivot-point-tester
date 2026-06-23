"""
Curate quality call recordings for challenge submission.

Run all scenarios into recordings/, then pick the best 10 with --curate-submission.
"""

import logging
import os
import re
import shutil
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

RECORDINGS_DIR = "recordings"
SUBMISSION_DIR = "submission_recordings"
AUDIO_MP3_FILENAME = "call.mp3"
TRANSCRIPT_FILENAME = "transcript.txt"
SUBMISSION_TARGET_COUNT = 10

# Reference set for diversity bonus during curation (not a run filter).
SUBMISSION_SCENARIO_IDS = [1, 2, 5, 6, 7, 8, 9, 12, 14, 22]

MIN_AUDIO_BYTES = 10_000
MIN_SPEAKER_TURNS = 4
MIN_DURATION_SECONDS = 60

_CALL_FOLDER_PATTERN = re.compile(r"^call_(\d{2})")
_SCENARIO_ID_HEADER_PATTERN = re.compile(r"^Scenario ID:\s*(\d+)", re.MULTILINE)
_SCENARIO_ID_PATTERN = re.compile(r"scenario_(\d+)", re.IGNORECASE)
_TIMESTAMP_PATTERN = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\]")
_SPEAKER_LINE_PATTERN = re.compile(r"\[(AGENT|PATIENT BOT)\]:")


@dataclass
class CallCandidate:
    folder: str
    folder_name: str
    scenario_id: int
    patient_name: str
    duration_seconds: int
    speaker_turns: int
    audio_bytes: int
    findings_count: int
    score: float
    quality_reason: str


def ensure_submission_dir() -> str:
    os.makedirs(SUBMISSION_DIR, exist_ok=True)
    return SUBMISSION_DIR


def _read_transcript(call_folder: str) -> str:
    transcript_path = os.path.join(call_folder, TRANSCRIPT_FILENAME)
    with open(transcript_path, encoding="utf-8") as f:
        return f.read()


def _parse_duration_seconds(transcript_text: str) -> int:
    latest = 0
    for match in _TIMESTAMP_PATTERN.finditer(transcript_text):
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        total = hours * 3600 + minutes * 60 + seconds
        latest = max(latest, total)
    return latest


def _parse_scenario_id(transcript_text: str) -> int:
    match = _SCENARIO_ID_HEADER_PATTERN.search(transcript_text)
    if not match:
        return 0
    return int(match.group(1))


def _parse_patient_name(transcript_text: str) -> str:
    match = re.search(r"^Patient Name:\s*(.+)$", transcript_text, re.MULTILINE)
    if not match:
        return "unknown"
    return match.group(1).strip()


def assess_call_quality(call_folder: str) -> tuple[bool, str]:
    """Return whether a call folder meets the minimum submission quality bar."""
    transcript_path = os.path.join(call_folder, TRANSCRIPT_FILENAME)
    audio_path = os.path.join(call_folder, AUDIO_MP3_FILENAME)

    if not os.path.isfile(transcript_path):
        return False, "missing transcript.txt"

    if not os.path.isfile(audio_path):
        return False, "missing call.mp3"

    if os.path.getsize(audio_path) < MIN_AUDIO_BYTES:
        return False, "audio file too small"

    transcript_text = _read_transcript(call_folder)

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


def score_call(call_folder: str, findings_count: int = 0) -> Optional[CallCandidate]:
    """Score a call folder; return None if it fails the quality bar."""
    is_quality, reason = assess_call_quality(call_folder)
    if not is_quality:
        return None

    transcript_text = _read_transcript(call_folder)
    audio_path = os.path.join(call_folder, AUDIO_MP3_FILENAME)
    duration_seconds = _parse_duration_seconds(transcript_text)
    speaker_turns = len(_SPEAKER_LINE_PATTERN.findall(transcript_text))
    audio_bytes = os.path.getsize(audio_path)
    scenario_id = _parse_scenario_id(transcript_text)

    # Longer, fuller conversations rank higher; findings are a plus for this challenge.
    score = (
        duration_seconds
        + speaker_turns * 5
        + (audio_bytes / 1024)
        + findings_count * 25
    )
    if scenario_id in SUBMISSION_SCENARIO_IDS:
        score += 15

    return CallCandidate(
        folder=call_folder,
        folder_name=os.path.basename(call_folder),
        scenario_id=scenario_id,
        patient_name=_parse_patient_name(transcript_text),
        duration_seconds=duration_seconds,
        speaker_turns=speaker_turns,
        audio_bytes=audio_bytes,
        findings_count=findings_count,
        score=score,
        quality_reason=reason,
    )


def scan_recording_folders(recordings_dir: str = RECORDINGS_DIR) -> list[str]:
    """Return sorted call folder paths under recordings/."""
    if not os.path.isdir(recordings_dir):
        return []

    folders: list[tuple[int, str]] = []
    for name in os.listdir(recordings_dir):
        folder = os.path.join(recordings_dir, name)
        if not os.path.isdir(folder):
            continue
        match = _CALL_FOLDER_PATTERN.match(name)
        if match:
            folders.append((int(match.group(1)), folder))

    folders.sort(key=lambda item: item[0])
    return [folder for _, folder in folders]


def _findings_count_for_folder(call_folder: str, results_by_folder: dict) -> int:
    folder_name = os.path.basename(call_folder)
    return results_by_folder.get(folder_name, 0)


def rank_call_candidates(
    recordings_dir: str = RECORDINGS_DIR,
    findings_by_folder: Optional[dict[str, int]] = None,
) -> list[CallCandidate]:
    """
    Score all recordings and keep the best candidate per scenario ID.
    """
    findings_by_folder = findings_by_folder or {}
    best_by_scenario: dict[int, CallCandidate] = {}

    for folder in scan_recording_folders(recordings_dir):
        findings_count = _findings_count_for_folder(folder, findings_by_folder)
        candidate = score_call(folder, findings_count=findings_count)
        if candidate is None:
            continue

        existing = best_by_scenario.get(candidate.scenario_id)
        if existing is None or candidate.score > existing.score:
            best_by_scenario[candidate.scenario_id] = candidate

    ranked = sorted(best_by_scenario.values(), key=lambda c: c.score, reverse=True)
    return ranked


def _slugify_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower())
    return slug.strip("_") or "unknown"


def build_submission_folder_name(
    submission_number: int, scenario_id: int, patient_name: str
) -> str:
    slug = _slugify_name(patient_name)
    return f"submission_{submission_number:02d}_scenario_{scenario_id:02d}_{slug}"


def _clear_submission_dir() -> None:
    if not os.path.isdir(SUBMISSION_DIR):
        return
    for name in os.listdir(SUBMISSION_DIR):
        path = os.path.join(SUBMISSION_DIR, name)
        if os.path.isdir(path):
            shutil.rmtree(path)


def curate_submission(
    limit: int = SUBMISSION_TARGET_COUNT,
    recordings_dir: str = RECORDINGS_DIR,
    findings_by_folder: Optional[dict[str, int]] = None,
    dry_run: bool = False,
) -> list[CallCandidate]:
    """
    Pick the top N quality calls from recordings/ and copy into submission_recordings/.
    """
    ranked = rank_call_candidates(recordings_dir, findings_by_folder)
    selected = ranked[:limit]

    print()
    print(f"Found {len(ranked)} quality calls across scenarios in {recordings_dir}/")
    print(f"Selecting top {min(limit, len(selected))} for submission:")
    print()

    if not selected:
        print("No quality calls found. Run scenarios first with: python main.py --all")
        print()
        return []

    for index, candidate in enumerate(selected, start=1):
        duration = f"{candidate.duration_seconds // 60}m {candidate.duration_seconds % 60:02d}s"
        print(
            f"  {index:02d}. scenario {candidate.scenario_id:02d} "
            f"{candidate.patient_name:<20} "
            f"score={candidate.score:6.1f}  "
            f"duration={duration:<8} "
            f"turns={candidate.speaker_turns:<3} "
            f"findings={candidate.findings_count}  "
            f"from {candidate.folder_name}"
        )

    if dry_run:
        print()
        print("Dry run only — submission_recordings/ was not changed.")
        print()
        return selected

    _clear_submission_dir()
    ensure_submission_dir()

    for index, candidate in enumerate(selected, start=1):
        dest_name = build_submission_folder_name(
            index, candidate.scenario_id, candidate.patient_name
        )
        dest_folder = os.path.join(SUBMISSION_DIR, dest_name)
        shutil.copytree(candidate.folder, dest_folder)
        logger.info("Curated submission recording → %s", dest_folder)

    print()
    print(f"Wrote {len(selected)} calls to {SUBMISSION_DIR}/")
    print()
    return selected


def get_submitted_scenario_ids() -> set[int]:
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


def promote_call(call_folder: str, scenario: dict) -> Optional[str]:
    """Copy one quality call into submission_recordings/ (manual/single promote)."""
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

    existing_count = len(
        [name for name in os.listdir(ensure_submission_dir()) if os.path.isdir(os.path.join(SUBMISSION_DIR, name))]
    )
    dest_name = build_submission_folder_name(
        existing_count + 1, scenario_id, patient_name
    )
    dest_folder = os.path.join(SUBMISSION_DIR, dest_name)
    shutil.copytree(call_folder, dest_folder)
    logger.info("Promoted submission recording → %s", dest_folder)
    return dest_folder


def promote_existing_call(call_folder: str, scenario: dict) -> Optional[str]:
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
    submitted = get_submitted_scenario_ids()
    quality_count = len(rank_call_candidates())

    print()
    print("Submission progress (10 calls required):")
    print(f"  Curated in {SUBMISSION_DIR}/: {len(submitted)}")
    print(f"  Quality calls available in recordings/: {quality_count}")
    if submitted:
        print(f"  Submitted scenario IDs: {sorted(submitted)}")
    if len(submitted) < SUBMISSION_TARGET_COUNT:
        print(f"  Still needed: {SUBMISSION_TARGET_COUNT - len(submitted)}")
    print()
