"""
Pivot Point Orthopedics AI Agent Tester — main entry point.

Starts the FastAPI bridge server, triggers outbound Twilio test calls for each
scenario, saves recordings/transcripts, and runs automated transcript analysis.
"""

import argparse
import logging
import os
import sys
import threading
import time
from datetime import datetime

import uvicorn
from dotenv import load_dotenv
from twilio.rest import Client

import call_handler
from analyzer import analyze_call
from call_handler import app
from recorder import CallRecorder
from scenarios import SCENARIOS, get_scenario
from submission import (
    SUBMISSION_SCENARIO_IDS,
    curate_submission,
    print_submission_status,
    promote_call,
    promote_existing_call,
)

# ---------------------------------------------------------------------------
# Environment and logging
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("test_run.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

REQUIRED_ENV_VARS = [
    "OPENAI_API_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "TARGET_PHONE_NUMBER",
    "NGROK_URL",
]

BETWEEN_CALL_WAIT_SECONDS = 30
SERVER_READY_WAIT_SECONDS = 2
CALL_COMPLETE_WAIT_SECONDS = 45


# ---------------------------------------------------------------------------
# Pre-flight and display helpers
# ---------------------------------------------------------------------------


def run_preflight_checks(*, starting_server: bool = True) -> None:
    """Verify required environment variables before starting."""
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        print(f"ERROR: Missing required environment variables: {missing}")
        print("Copy .env.example to .env and fill in all values")
        sys.exit(1)

    if not os.getenv("NGROK_URL", "").startswith("https://"):
        print("ERROR: NGROK_URL must start with https://")
        print("Run: ngrok http 5050")
        print("Copy the https:// URL into your .env file")
        sys.exit(1)

    if starting_server:
        print("Pre-flight checks passed. Starting server...")


def print_startup_banner(scenario_ids: list[int]) -> None:
    """Print the startup banner with target and scenario info."""
    ngrok_url = os.getenv("NGROK_URL", "")
    target = os.getenv("TARGET_PHONE_NUMBER", "")
    ids_str = ", ".join(str(sid) for sid in scenario_ids)

    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║     PIVOT POINT ORTHOPEDICS — AI AGENT TESTER       ║")
    print("║     Pretty Good AI Engineering Challenge            ║")
    print("╠══════════════════════════════════════════════════════╣")
    print(f"║  Target:  {target:<43}║")
    print("║  Server:  http://localhost:5050                     ║")
    print(f"║  Webhook: {ngrok_url:<43}║")
    print("╠══════════════════════════════════════════════════════╣")
    print(f"║  Scenarios to run: {ids_str:<33}║")
    print(f"║  Total scenarios:  {len(scenario_ids):<33}║")
    print("╚══════════════════════════════════════════════════════╝")
    print()


def print_scenario_list() -> None:
    """Print all scenarios in a table and exit."""
    print()
    print("ID  | Patient Name        | Goal")
    print("----|---------------------|----------------------------------------")
    for scenario in SCENARIOS:
        goal = scenario.get("goal", "")
        if len(goal) > 40:
            goal = goal[:37] + "..."
        name = scenario.get("name", "")[:19]
        print(f"{scenario['id']:<3} | {name:<19} | {goal}")
    print()


def format_duration(seconds: float) -> str:
    """Format seconds as Xm Ys."""
    total = int(seconds)
    minutes = total // 60
    secs = total % 60
    return f"{minutes}m {secs:02d}s"


def truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if needed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


# ---------------------------------------------------------------------------
# Server and Twilio helpers
# ---------------------------------------------------------------------------


def start_server() -> None:
    """Run uvicorn in a background thread (daemon)."""
    uvicorn.run(app, host="0.0.0.0", port=5050, log_level="warning")


def wait_for_call_completion(client: Client, call_sid: str, timeout: int = 360) -> str:
    """Poll Twilio until the call reaches a terminal status."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        call = client.calls(call_sid).fetch()
        status = call.status
        if status in ["completed", "failed", "busy", "no-answer", "canceled"]:
            return status
        logging.info("Call status: %s, waiting...", status)
        time.sleep(5)
    return "timeout"


def trigger_twilio_call(client: Client) -> str:
    """Place an outbound call and return the call SID."""
    ngrok_url = os.getenv("NGROK_URL", "").rstrip("/")
    call = client.calls.create(
        to=os.getenv("TARGET_PHONE_NUMBER"),
        from_=os.getenv("TWILIO_PHONE_NUMBER"),
        url=f"{ngrok_url}/incoming-call",
        method="POST",
        record=True,
        time_limit=300,
    )
    logger.info("Twilio call initiated — SID: %s", call.sid)
    return call.sid


def wait_between_calls(seconds: int = BETWEEN_CALL_WAIT_SECONDS) -> None:
    """Count down between calls; Ctrl+C skips the remaining wait."""
    print(f"Waiting {seconds} seconds before next call... (press Ctrl+C to stop)")
    try:
        for remaining in range(seconds, 0, -1):
            print(f"  {remaining}s remaining...", end="\r", flush=True)
            time.sleep(1)
        print()
    except KeyboardInterrupt:
        print("\nSkipping wait.")


# ---------------------------------------------------------------------------
# Scenario selection
# ---------------------------------------------------------------------------


def parse_arguments() -> argparse.Namespace:
    """Parse CLI arguments for scenario selection."""
    parser = argparse.ArgumentParser(
        description="Pivot Point Orthopedics AI Agent Tester",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all scenarios and exit",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all 25 scenarios in order",
    )
    parser.add_argument(
        "--submission",
        action="store_true",
        help=(
            "Run the 10 curated submission scenarios "
            f"({', '.join(str(sid) for sid in SUBMISSION_SCENARIO_IDS)}), "
            "auto-promoting each quality call"
        ),
    )
    parser.add_argument(
        "--curate-submission",
        action="store_true",
        help="Pick the best 10 quality calls from recordings/ into submission_recordings/",
    )
    parser.add_argument(
        "--curate-dry-run",
        action="store_true",
        help="Show which calls would be curated without writing submission_recordings/",
    )
    parser.add_argument(
        "--promote",
        metavar="FOLDER",
        help="Promote an existing recordings/ folder into submission_recordings/",
    )
    parser.add_argument(
        "--submission-status",
        action="store_true",
        help="Show submission progress and exit",
    )
    parser.add_argument(
        "--scenario",
        nargs="+",
        type=int,
        metavar="ID",
        help="Run one or more scenario IDs (e.g. --scenario 1 3 5)",
    )
    return parser.parse_args()


def resolve_scenario_ids(args: argparse.Namespace) -> list[int]:
    """Determine which scenario IDs to run from CLI args."""
    if args.submission:
        from submission import get_submitted_scenario_ids

        submitted = get_submitted_scenario_ids()
        return [
            scenario_id
            for scenario_id in SUBMISSION_SCENARIO_IDS
            if scenario_id not in submitted
        ]

    if args.all:
        return [scenario["id"] for scenario in SCENARIOS]

    if args.scenario:
        ids = args.scenario
        for scenario_id in ids:
            try:
                get_scenario(scenario_id)
            except ValueError:
                print(f"ERROR: Unknown scenario ID: {scenario_id}")
                print("Use --list to see available scenarios")
                sys.exit(1)
        return ids

    print("ERROR: Specify --scenario ID [ID ...], --submission, --all, or --list")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Per-call orchestration
# ---------------------------------------------------------------------------


def run_single_scenario(client: Client, scenario: dict, *, auto_promote: bool = False) -> dict:
    """
    Run one scenario end-to-end.

    Returns a result dict for the summary table.
    """
    scenario_id = scenario["id"]
    scenario_name = scenario.get("name", "unknown")
    logger.info("Starting scenario %s: %s", scenario_id, scenario_name)
    print(f"\nStarting scenario {scenario_id}: {scenario_name}...")

    start_time = datetime.now()
    result = {
        "id": scenario_id,
        "name": scenario_name,
        "duration": "FAILED",
        "findings_count": 0,
        "findings_by_severity": {
            "CRITICAL": 0,
            "HIGH": 0,
            "MEDIUM": 0,
            "LOW": 0,
        },
        "folder": "—",
        "status": "failed",
    }

    recorder = None
    try:
        call_handler.current_scenario = scenario
        recorder = CallRecorder(scenario)
        call_handler.current_recorder = recorder
        call_handler.call_complete_event = threading.Event()

        call_sid = trigger_twilio_call(client)
        final_status = wait_for_call_completion(client, call_sid)

        if final_status != "completed":
            logger.warning(
                "Scenario %s call ended with status: %s", scenario_id, final_status
            )

        # Allow the WebSocket handler time to save recording/transcript.
        if call_handler.call_complete_event is not None:
            call_handler.call_complete_event.wait(timeout=CALL_COMPLETE_WAIT_SECONDS)

        elapsed = (datetime.now() - start_time).total_seconds()
        result["duration"] = format_duration(elapsed)
        result["folder"] = os.path.basename(recorder.get_call_folder())
        result["status"] = "ok" if final_status == "completed" else final_status

        transcript_path = os.path.join(recorder.get_call_folder(), "transcript.txt")
        findings = analyze_call(transcript_path, scenario, recorder.get_call_folder())

        result["findings_count"] = len(findings)
        for finding in findings:
            severity = finding.get("severity", "LOW")
            result["findings_by_severity"][severity] = (
                result["findings_by_severity"].get(severity, 0) + 1
            )

        issues_label = (
            f"{result['findings_count']} issue"
            if result["findings_count"] == 1
            else f"{result['findings_count']} issues"
        )
        print(
            f"Scenario {scenario_id} complete — {result['duration']}, "
            f"{issues_label}, saved to {result['folder']}"
        )

        promoted = None
        if auto_promote:
            promoted = promote_call(recorder.get_call_folder(), scenario)
        if promoted:
            result["submission_folder"] = os.path.basename(promoted)
            print(f"  → Promoted to submission: {result['submission_folder']}")

    except Exception as exc:
        logger.error(
            "Scenario %s failed: %s", scenario_id, exc, exc_info=True
        )
        print(f"ERROR: Scenario {scenario_id} failed — {exc}")
        if recorder is not None:
            try:
                recorder.save_partial()
                result["folder"] = os.path.basename(recorder.get_call_folder())
                transcript_path = os.path.join(
                    recorder.get_call_folder(), "transcript.txt"
                )
                if os.path.exists(transcript_path):
                    findings = analyze_call(
                        transcript_path, scenario, recorder.get_call_folder()
                    )
                    result["findings_count"] = len(findings)
                    for finding in findings:
                        severity = finding.get("severity", "LOW")
                        result["findings_by_severity"][severity] = (
                            result["findings_by_severity"].get(severity, 0) + 1
                        )
            except Exception as save_exc:
                logger.error("Partial save failed: %s", save_exc, exc_info=True)

    finally:
        call_handler.current_scenario = None
        call_handler.current_recorder = None
        call_handler.call_complete_event = None

    return result


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------


def print_summary_table(results: list[dict]) -> None:
    """Print the final results table and totals."""
    print()
    print("╔════╦══════════════════════╦══════════╦═══════════╦════════════╗")
    print("║ #  ║ Patient              ║ Duration ║ Findings  ║ Folder     ║")
    print("╠════╬══════════════════════╬══════════╬═══════════╬════════════╣")

    for row in results:
        findings_label = (
            f"{row['findings_count']} issue"
            if row["findings_count"] == 1
            else f"{row['findings_count']} issues"
        )
        if row["status"] != "ok" and row["duration"] == "FAILED":
            duration = "FAILED"
        else:
            duration = truncate(row["duration"], 8)

        print(
            f"║ {row['id']:<2} ║ {truncate(row['name'], 20):<20} ║ "
            f"{duration:<8} ║ {truncate(findings_label, 9):<9} ║ "
            f"{truncate(row['folder'], 10):<10} ║"
        )

    print("╚════╩══════════════════════╩══════════╩═══════════╩════════════╝")
    print()

    total_findings = sum(r["findings_count"] for r in results)
    severity_totals = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for row in results:
        for severity, count in row["findings_by_severity"].items():
            severity_totals[severity] = severity_totals.get(severity, 0) + count

    print(f"Total calls: {len(results)}")
    print(
        "Total findings: "
        f"{total_findings} "
        f"({severity_totals['CRITICAL']} CRITICAL, "
        f"{severity_totals['HIGH']} HIGH, "
        f"{severity_totals['MEDIUM']} MEDIUM, "
        f"{severity_totals['LOW']} LOW)"
    )
    print("Recordings saved to: recordings/")
    print("Bug report saved to: bug_report.md")
    print()
    print("All done! Check recordings/ and bug_report.md")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_arguments()

    if args.list:
        print_scenario_list()
        return

    if args.submission_status:
        print_submission_status()
        return

    if args.curate_submission or args.curate_dry_run:
        curate_submission(dry_run=args.curate_dry_run)
        print_submission_status()
        return

    if args.promote:
        if not args.scenario or len(args.scenario) != 1:
            print("ERROR: --promote requires exactly one --scenario ID")
            sys.exit(1)
        scenario = get_scenario(args.scenario[0])
        promote_existing_call(args.promote, scenario)
        print_submission_status()
        return

    run_preflight_checks()

    scenario_ids = resolve_scenario_ids(args)
    if not scenario_ids:
        print("All submission scenarios are already complete.")
        print_submission_status()
        return

    print_submission_status()
    print_startup_banner(scenario_ids)

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    time.sleep(SERVER_READY_WAIT_SECONDS)
    logger.info("FastAPI server started on port 5050")

    client = Client(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN"),
    )

    results: list[dict] = []
    auto_promote = bool(args.submission)
    for index, scenario_id in enumerate(scenario_ids):
        scenario = get_scenario(scenario_id)
        result = run_single_scenario(client, scenario, auto_promote=auto_promote)
        results.append(result)

        if index < len(scenario_ids) - 1:
            wait_between_calls()

    print_summary_table(results)

    if args.all:
        findings_by_folder = {
            row["folder"]: row["findings_count"]
            for row in results
            if row.get("folder") and row["folder"] != "—"
        }
        print("All scenarios complete — curating the best 10 calls for submission...")
        curate_submission(findings_by_folder=findings_by_folder)

    print_submission_status()


if __name__ == "__main__":
    main()
