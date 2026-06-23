"""
Download full two-party call recordings from Twilio.

Twilio's call recording captures both sides of the phone conversation.
Stream-captured audio in call_handler only includes the patient bot output.
"""

import logging
import os
import time

import httpx
from twilio.rest import Client

logger = logging.getLogger(__name__)

PATIENT_BOT_MP3_FILENAME = "patient_bot.mp3"
FULL_CALL_MP3_FILENAME = "call.mp3"


def download_twilio_call_recording(
    client: Client,
    call_sid: str,
    dest_mp3_path: str,
    *,
    max_wait_seconds: int = 90,
    poll_interval_seconds: int = 5,
) -> bool:
    """
    Poll Twilio until the call recording is ready, then download MP3.

    Returns True if dest_mp3_path was written.
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not account_sid or not auth_token:
        logger.error("Twilio credentials missing — cannot download recording")
        return False

    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        recordings = client.recordings.list(call_sid=call_sid, limit=5)
        for recording in recordings:
            if recording.status != "completed":
                logger.info(
                    "Twilio recording %s status=%s — waiting",
                    recording.sid,
                    recording.status,
                )
                continue

            recording_url = (
                f"https://api.twilio.com{recording.uri.replace('.json', '.mp3')}"
            )
            response = httpx.get(
                recording_url,
                auth=(account_sid, auth_token),
                timeout=60.0,
                follow_redirects=True,
            )
            response.raise_for_status()

            with open(dest_mp3_path, "wb") as mp3_file:
                mp3_file.write(response.content)

            logger.info(
                "Downloaded Twilio full-call recording → %s (%d bytes)",
                dest_mp3_path,
                len(response.content),
            )
            return True

        time.sleep(poll_interval_seconds)

    logger.warning(
        "Twilio recording not ready for call %s after %ss",
        call_sid,
        max_wait_seconds,
    )
    return False
