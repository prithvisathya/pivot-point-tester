#!/usr/bin/env python3
"""
Re-download Twilio full-call MP3s for folders that contain call_sid.txt.

Usage:
  python scripts/refresh_call_audio.py recordings/call_16
  python scripts/refresh_call_audio.py submission_recordings/submission_01_scenario_02_james_whitfield
"""

import argparse
import os
import shutil
import sys

from dotenv import load_dotenv
from twilio.rest import Client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from recorder import AUDIO_MP3_FILENAME, CALL_SID_FILENAME, PATIENT_BOT_MP3_FILENAME
from twilio_recording import download_twilio_call_recording

load_dotenv(os.path.join(ROOT, ".env"))


def refresh_folder(client: Client, folder: str) -> bool:
    sid_path = os.path.join(folder, CALL_SID_FILENAME)
    if not os.path.isfile(sid_path):
        print(f"SKIP {folder}: no {CALL_SID_FILENAME}")
        return False

    with open(sid_path, encoding="utf-8") as sid_file:
        call_sid = sid_file.read().strip()

    mp3_path = os.path.join(folder, AUDIO_MP3_FILENAME)
    backup_path = os.path.join(folder, PATIENT_BOT_MP3_FILENAME)
    if os.path.isfile(mp3_path) and not os.path.isfile(backup_path):
        shutil.copy2(mp3_path, backup_path)
        print(f"Backed up existing audio → {backup_path}")

    ok = download_twilio_call_recording(client, call_sid, mp3_path)
    if ok:
        print(f"OK {folder} → {AUDIO_MP3_FILENAME} (full two-party Twilio recording)")
    else:
        print(f"FAIL {folder}: Twilio recording not available for {call_sid}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh call.mp3 from Twilio")
    parser.add_argument("folders", nargs="+", help="Call folders with call_sid.txt")
    args = parser.parse_args()

    client = Client(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN"),
    )

    success = 0
    for folder in args.folders:
        if refresh_folder(client, folder):
            success += 1

    print(f"\nRefreshed {success}/{len(args.folders)} folders")


if __name__ == "__main__":
    main()
