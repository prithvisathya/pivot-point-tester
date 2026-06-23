#!/usr/bin/env python3
"""
Match local call folders to Twilio call SIDs by start time, then download
full two-party MP3 recordings from Twilio.
"""

import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from twilio.rest import Client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from recorder import AUDIO_MP3_FILENAME, CALL_SID_FILENAME, PATIENT_BOT_MP3_FILENAME
from twilio_recording import download_twilio_call_recording

load_dotenv(os.path.join(ROOT, ".env"))

CALL_DATE_PATTERN = re.compile(r"^Call Date:\s*(.+)$", re.MULTILINE)
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def parse_call_date(transcript_path: str) -> Optional[datetime]:
    with open(transcript_path, encoding="utf-8") as transcript_file:
        text = transcript_file.read()
    match = CALL_DATE_PATTERN.search(text)
    if not match:
        return None
    naive = datetime.strptime(match.group(1).strip(), "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=LOCAL_TZ)


def load_twilio_calls(client: Client) -> list:
    calls = client.calls.list(to=os.getenv("TARGET_PHONE_NUMBER", "+18054398008"), limit=60)
    loaded = []
    for call in calls:
        if not call.start_time:
            continue
        start = call.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        loaded.append(
            {
                "sid": call.sid,
                "start": start,
                "duration": int(call.duration or 0),
            }
        )
    return loaded


def best_match(local_start: datetime, twilio_calls: list) -> Optional[dict]:
    if not twilio_calls:
        return None
    best = min(twilio_calls, key=lambda item: abs((item["start"] - local_start).total_seconds()))
    delta = abs((best["start"] - local_start).total_seconds())
    if delta > 180:
        return None
    return best


def backfill_folder(client: Client, folder: str, twilio_calls: list) -> bool:
    transcript_path = os.path.join(folder, "transcript.txt")
    if not os.path.isfile(transcript_path):
        return False

    local_start = parse_call_date(transcript_path)
    if local_start is None:
        print(f"SKIP {folder}: no Call Date in transcript")
        return False

    match = best_match(local_start, twilio_calls)
    if match is None:
        print(f"SKIP {folder}: no Twilio call within 3 minutes of {local_start}")
        return False

    sid_path = os.path.join(folder, CALL_SID_FILENAME)
    with open(sid_path, "w", encoding="utf-8") as sid_file:
        sid_file.write(match["sid"])

    patient_bot_path = os.path.join(folder, PATIENT_BOT_MP3_FILENAME)
    call_mp3_path = os.path.join(folder, AUDIO_MP3_FILENAME)
    if os.path.isfile(call_mp3_path) and not os.path.isfile(patient_bot_path):
        os.rename(call_mp3_path, patient_bot_path)

    ok = download_twilio_call_recording(client, match["sid"], call_mp3_path, max_wait_seconds=15)
    if ok:
        print(
            f"OK {folder} ← {match['sid']} "
            f"(delta {abs((match['start'] - local_start).total_seconds()):.0f}s)"
        )
    else:
        print(f"FAIL {folder}: recording download failed for {match['sid']}")
    return ok


def iter_call_folders(*roots: str) -> list[str]:
    folders: list[str] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isdir(path) and os.path.isfile(os.path.join(path, "transcript.txt")):
                folders.append(path)
    return folders


def main() -> None:
    client = Client(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN"),
    )
    twilio_calls = load_twilio_calls(client)
    folders = iter_call_folders(
        os.path.join(ROOT, "submission_recordings"),
        os.path.join(ROOT, "recordings"),
    )

    success = 0
    for folder in folders:
        if backfill_folder(client, folder, twilio_calls):
            success += 1

    print(f"\nBackfilled {success}/{len(folders)} folders")


if __name__ == "__main__":
    main()
