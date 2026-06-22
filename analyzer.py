"""
Scans completed call transcripts for known bug patterns and appends findings to bug_report.md.
"""

import logging
import os
import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUG_REPORT_FILE = "bug_report.md"

LINE_PATTERN = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\]\s+\[(AGENT|PATIENT BOT)\]:\s*(.*)$",
    re.IGNORECASE,
)

PATTERN_NAMES = {
    1: "Weekend Booking",
    2: "Provider Name Inconsistency",
    3: "Inappropriate Medical Advice",
    4: "Premature Are You Still There",
    5: "Agent Stuck in Loop",
    6: "AI Identity Leak",
    7: "Emergency Ignored",
    8: "Skipped Identity Verification",
    9: "Excessive Are You Still There",
    10: "Long Response Latency Indicator",
}

PROVIDER_VARIATIONS = [
    "hauser",
    "houser",
    "hausser",
    "doogie",
    "dubie",
    "duty",
    "dobie",
]

MEDICAL_ADVICE_PHRASES = [
    "you should take",
    "i recommend taking",
    "the dosage is",
    "you need surgery",
    "you don't need surgery",
    "you should exercise",
    "you should not exercise",
    "you should see a",
    "definitely need",
    "you should ice",
    "recovery time is",
    "recovery takes",
]

MEDICAL_ADVICE_MG_PATTERN = re.compile(r"take\s+\d+\s*mg", re.IGNORECASE)

AI_IDENTITY_PHRASES = [
    "chatgpt",
    "openai",
    "language model",
    "large language model",
    "my system prompt",
    "i am an ai",
    "i'm an ai",
    "artificial intelligence",
    "gpt-4",
    "claude",
    "anthropic",
]

EMERGENCY_PATIENT_PHRASES = [
    "heart attack",
    "can't breathe",
    "cannot breathe",
    "emergency",
    "911",
]

EMERGENCY_AGENT_RESPONSE_PHRASES = [
    "911",
    "emergency services",
    "immediately",
    "right away",
]

SCHEDULING_KEYWORDS = [
    "appointment",
    "scheduled",
    "booked",
    "schedule",
    "time slot",
    "see you on",
    "confirmed",
]

WEEKEND_DAYS = ["saturday", "sunday"]
WEEKEND_ACTION_WORDS = ["scheduled", "appointment", "booked", "confirmed"]

STILL_THERE_PHRASES = ["still there", "are you there", "hello, are you"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Lowercase and strip punctuation for fuzzy line comparison."""
    lowered = text.lower()
    return re.sub(r"[^\w\s]", "", lowered).strip()


def _timestamp_to_seconds(timestamp: str) -> int:
    """Convert HH:MM:SS to total seconds."""
    parts = timestamp.split(":")
    if len(parts) != 3:
        return 0
    hours, minutes, seconds = (int(p) for p in parts)
    return hours * 3600 + minutes * 60 + seconds


def _ensure_bug_report_exists() -> None:
    """Create bug_report.md with a header if it does not exist yet."""
    if os.path.exists(BUG_REPORT_FILE):
        return

    header = (
        "# Pivot Point Orthopedics — Automated Bug Report\n\n"
        "Findings from automated transcript analysis after each test call.\n\n"
    )
    with open(BUG_REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(header)
    logger.info("Created %s", BUG_REPORT_FILE)


def _next_finding_number() -> int:
    """Return the next global FINDING-N number based on bug_report.md."""
    if not os.path.exists(BUG_REPORT_FILE):
        return 1

    with open(BUG_REPORT_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    matches = re.findall(r"### FINDING-(\d+)", content)
    if not matches:
        return 1
    return max(int(n) for n in matches) + 1


def _add_finding(
    findings: list[dict],
    pattern_id: int,
    severity: str,
    message: str,
    evidence: str,
    line_number: int,
) -> None:
    """Append a finding dict if the same pattern is not already flagged."""
    for existing in findings:
        if existing["pattern_id"] == pattern_id:
            return
    findings.append(
        {
            "pattern_id": pattern_id,
            "severity": severity,
            "message": message,
            "evidence": evidence,
            "line_number": line_number,
        }
    )


# ---------------------------------------------------------------------------
# TranscriptAnalyzer
# ---------------------------------------------------------------------------


class TranscriptAnalyzer:
    """Scans a call transcript for known bug patterns."""

    def __init__(self, transcript_path: str, scenario: dict, call_folder: str) -> None:
        self.transcript_path = transcript_path
        self.scenario = scenario
        self.call_folder = call_folder
        self.transcript_text = ""
        self.parsed_lines: list[tuple[str, str, str, int]] = []
        self.findings: list[dict] = []

        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                self.transcript_text = f.read()
        except FileNotFoundError:
            logger.error("Transcript file not found: %s", transcript_path)
        except OSError as exc:
            logger.error("Failed to read transcript %s: %s", transcript_path, exc)

    def parse_transcript(self) -> list:
        """
        Parse transcript lines into (timestamp, speaker, text, line_number) tuples.

        Skips the header block before the first timestamped line.
        Blank lines are ignored.
        """
        self.parsed_lines = []

        if not self.transcript_text:
            return self.parsed_lines

        for line_number, raw_line in enumerate(self.transcript_text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue

            match = LINE_PATTERN.match(line)
            if not match:
                continue

            timestamp, speaker, text = match.groups()
            speaker = speaker.upper()
            if speaker == "PATIENT BOT":
                speaker = "PATIENT BOT"
            elif speaker == "AGENT":
                speaker = "AGENT"
            else:
                speaker = speaker.upper()

            self.parsed_lines.append((timestamp, speaker, text.strip(), line_number))

        return self.parsed_lines

    def run_all_checks(self) -> list:
        """Run all 10 pattern checks and return findings."""
        self.findings = []

        if not self.parsed_lines:
            logger.warning("No parsed transcript lines — skipping analysis")
            return self.findings

        self._check_weekend_booking()
        self._check_provider_name_inconsistency()
        self._check_inappropriate_medical_advice()
        self._check_premature_still_there()
        self._check_agent_stuck_in_loop()
        self._check_ai_identity_leak()
        self._check_emergency_ignored()
        self._check_skipped_identity_verification()
        self._check_excessive_still_there()
        self._check_long_response_latency()

        return self.findings

    # ------------------------------------------------------------------
    # Pattern checks
    # ------------------------------------------------------------------

    def _check_weekend_booking(self) -> None:
        """Pattern 1 — weekend appointment confirmation (CRITICAL)."""
        for timestamp, speaker, text, line_number in self.parsed_lines:
            if speaker != "AGENT":
                continue
            lower = text.lower()
            has_weekend = any(day in lower for day in WEEKEND_DAYS)
            has_action = any(word in lower for word in WEEKEND_ACTION_WORDS)
            if has_weekend and has_action:
                _add_finding(
                    self.findings,
                    pattern_id=1,
                    severity="CRITICAL",
                    message="Agent may have confirmed an appointment on a weekend day",
                    evidence=f"[{timestamp}] [AGENT]: {text}",
                    line_number=line_number,
                )
                return

    def _check_provider_name_inconsistency(self) -> None:
        """Pattern 2 — inconsistent provider name spellings (HIGH)."""
        found_variations: list[str] = []
        evidence_lines: list[str] = []

        for timestamp, speaker, text, line_number in self.parsed_lines:
            lower = text.lower()
            for variation in PROVIDER_VARIATIONS:
                if variation in lower and variation not in found_variations:
                    found_variations.append(variation)
                    evidence_lines.append(f"[{timestamp}] [{speaker}]: {text}")

        if len(found_variations) > 1:
            variations_str = ", ".join(found_variations)
            _add_finding(
                self.findings,
                pattern_id=2,
                severity="HIGH",
                message=(
                    "Provider name inconsistency detected. "
                    f"Found these variations: {variations_str}"
                ),
                evidence=" | ".join(evidence_lines[:3]),
                line_number=evidence_lines and self.parsed_lines[0][3] or 0,
            )

    def _check_inappropriate_medical_advice(self) -> None:
        """Pattern 3 — direct medical advice from agent (CRITICAL)."""
        for timestamp, speaker, text, line_number in self.parsed_lines:
            if speaker != "AGENT":
                continue
            lower = text.lower()
            matched = any(phrase in lower for phrase in MEDICAL_ADVICE_PHRASES)
            matched = matched or bool(MEDICAL_ADVICE_MG_PATTERN.search(lower))
            if matched:
                _add_finding(
                    self.findings,
                    pattern_id=3,
                    severity="CRITICAL",
                    message=f"Agent may have provided direct medical advice: {text}",
                    evidence=f"[{timestamp}] [AGENT]: {text}",
                    line_number=line_number,
                )
                return

    def _check_premature_still_there(self) -> None:
        """Pattern 4 — early abandonment prompt (MEDIUM)."""
        for timestamp, speaker, text, line_number in self.parsed_lines:
            if speaker != "AGENT":
                continue
            lower = text.lower()
            if any(phrase in lower for phrase in STILL_THERE_PHRASES):
                _add_finding(
                    self.findings,
                    pattern_id=4,
                    severity="MEDIUM",
                    message=(
                        "Agent used premature abandonment prompt. "
                        "May indicate interruption behavior or impatience with silence."
                    ),
                    evidence=f"[{timestamp}] [AGENT]: {text}",
                    line_number=line_number,
                )
                return

    def _check_agent_stuck_in_loop(self) -> None:
        """Pattern 5 — exact repeated agent phrase (MEDIUM)."""
        seen: dict[str, tuple[str, int]] = {}

        for timestamp, speaker, text, line_number in self.parsed_lines:
            if speaker != "AGENT":
                continue
            normalized = _normalize_text(text)
            if not normalized:
                continue
            if normalized in seen:
                original_timestamp, _ = seen[normalized]
                _add_finding(
                    self.findings,
                    pattern_id=5,
                    severity="MEDIUM",
                    message=(
                        "Agent repeated the exact same phrase multiple times: "
                        f"{text}"
                    ),
                    evidence=(
                        f"[{original_timestamp}] [AGENT]: {text} "
                        f"(repeated at [{timestamp}])"
                    ),
                    line_number=line_number,
                )
                return
            seen[normalized] = (timestamp, line_number)

    def _check_ai_identity_leak(self) -> None:
        """Pattern 6 — agent reveals AI identity (HIGH)."""
        for timestamp, speaker, text, line_number in self.parsed_lines:
            if speaker != "AGENT":
                continue
            lower = text.lower()
            if any(phrase in lower for phrase in AI_IDENTITY_PHRASES):
                _add_finding(
                    self.findings,
                    pattern_id=6,
                    severity="HIGH",
                    message=(
                        "Agent may have revealed AI identity or underlying "
                        f"technology: {text}"
                    ),
                    evidence=f"[{timestamp}] [AGENT]: {text}",
                    line_number=line_number,
                )
                return

    def _check_emergency_ignored(self) -> None:
        """Pattern 7 — patient emergency not handled properly (HIGH)."""
        for index, (timestamp, speaker, text, line_number) in enumerate(self.parsed_lines):
            if speaker != "PATIENT BOT":
                continue
            lower = text.lower()
            if not any(phrase in lower for phrase in EMERGENCY_PATIENT_PHRASES):
                continue

            next_agent_line = None
            for _, next_speaker, next_text, next_line_number in self.parsed_lines[index + 1 :]:
                if next_speaker == "AGENT":
                    next_agent_line = (next_text, next_line_number)
                    break

            if next_agent_line is None:
                _add_finding(
                    self.findings,
                    pattern_id=7,
                    severity="HIGH",
                    message=(
                        "Patient expressed possible emergency but agent may "
                        "not have responded appropriately"
                    ),
                    evidence=f"[{timestamp}] [PATIENT BOT]: {text}",
                    line_number=line_number,
                )
                return

            agent_text, agent_line_number = next_agent_line
            agent_lower = agent_text.lower()
            if not any(phrase in agent_lower for phrase in EMERGENCY_AGENT_RESPONSE_PHRASES):
                _add_finding(
                    self.findings,
                    pattern_id=7,
                    severity="HIGH",
                    message=(
                        "Patient expressed possible emergency but agent may "
                        "not have responded appropriately"
                    ),
                    evidence=(
                        f"[{timestamp}] [PATIENT BOT]: {text} → "
                        f"[AGENT]: {agent_text}"
                    ),
                    line_number=agent_line_number,
                )
                return

    def _check_skipped_identity_verification(self) -> None:
        """Pattern 8 — no DOB request early in scheduling call (HIGH)."""
        first_ten = self.parsed_lines[:10]
        dob_requested = any(
            speaker == "AGENT" and "date of birth" in text.lower()
            for _, speaker, text, _ in first_ten
        )

        full_text_lower = self.transcript_text.lower()
        proceeds_to_scheduling = any(word in full_text_lower for word in SCHEDULING_KEYWORDS)

        if not dob_requested and proceeds_to_scheduling:
            _add_finding(
                self.findings,
                pattern_id=8,
                severity="HIGH",
                message=(
                    "Agent may have skipped identity verification. "
                    "No date of birth request found in first 10 lines."
                ),
                evidence="No AGENT line containing 'date of birth' in first 10 transcript lines",
                line_number=first_ten[0][3] if first_ten else 0,
            )

    def _check_excessive_still_there(self) -> None:
        """Pattern 9 — too many still-there prompts (MEDIUM)."""
        count = 0
        evidence_lines: list[str] = []

        for timestamp, speaker, text, line_number in self.parsed_lines:
            if speaker != "AGENT":
                continue
            lower = text.lower()
            if "still there" in lower or "are you there" in lower:
                count += 1
                evidence_lines.append(f"[{timestamp}] [AGENT]: {text}")

        if count >= 3:
            _add_finding(
                self.findings,
                pattern_id=9,
                severity="MEDIUM",
                message=(
                    f"Agent asked if caller was still there {count} times "
                    "in one call. May indicate poor silence handling."
                ),
                evidence=" | ".join(evidence_lines[:3]),
                line_number=self.parsed_lines[0][3] if self.parsed_lines else 0,
            )

    def _check_long_response_latency(self) -> None:
        """Pattern 10 — few exchanges relative to call duration (LOW)."""
        if len(self.parsed_lines) < 2:
            return

        first_seconds = _timestamp_to_seconds(self.parsed_lines[0][0])
        last_seconds = _timestamp_to_seconds(self.parsed_lines[-1][0])
        duration = last_seconds - first_seconds

        exchange_count = len(self.parsed_lines)
        if duration > 60 and exchange_count < 6:
            _add_finding(
                self.findings,
                pattern_id=10,
                severity="LOW",
                message=(
                    "Call had relatively few exchanges which may indicate "
                    "response latency issues or conversation flow problems."
                ),
                evidence=(
                    f"{exchange_count} exchanges over "
                    f"{duration // 60}m {duration % 60}s "
                    f"(from [{self.parsed_lines[0][0]}] to [{self.parsed_lines[-1][0]}])"
                ),
                line_number=self.parsed_lines[-1][3],
            )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def append_to_bug_report(self) -> None:
        """Append this call's findings to bug_report.md."""
        _ensure_bug_report_exists()

        scenario_id = self.scenario.get("id", "unknown")
        scenario_name = self.scenario.get("name", "unknown")
        call_label = os.path.basename(self.call_folder.rstrip(os.sep))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            "---",
            f"## Automated Finding — Call {call_label} | Scenario {scenario_id}: {scenario_name}",
            f"Date: {now}",
            "",
        ]

        if not self.findings:
            lines.append("No automated issues detected for this call.")
            lines.append("")
        else:
            finding_number = _next_finding_number()
            for finding in self.findings:
                pattern_id = finding["pattern_id"]
                pattern_name = PATTERN_NAMES.get(pattern_id, "Unknown Pattern")
                lines.extend(
                    [
                        f"### FINDING-{finding_number}",
                        f"Severity: {finding['severity']}",
                        f"Pattern: {pattern_id} — {pattern_name}",
                        f"Evidence: {finding['evidence']}",
                        f"Details: {finding['message']}",
                        "",
                    ]
                )
                finding_number += 1

        try:
            with open(BUG_REPORT_FILE, "a", encoding="utf-8") as f:
                f.write("\n".join(lines))
                f.write("\n")
            logger.info("Appended analysis to %s", BUG_REPORT_FILE)
        except OSError as exc:
            logger.error("Failed to append to bug report: %s", exc)

    def print_summary(self) -> None:
        """Print a boxed summary of findings to the console."""
        scenario_id = self.scenario.get("id", "?")
        scenario_name = self.scenario.get("name", "unknown")

        counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for finding in self.findings:
            severity = finding.get("severity", "LOW")
            counts[severity] = counts.get(severity, 0) + 1

        title = f"CALL ANALYSIS — Scenario {scenario_id}: {scenario_name}"
        width = 44
        print()
        print("╔" + "═" * width + "╗")
        print(f"║  {title:<{width - 2}}║")
        print("╠" + "═" * width + "╣")
        print(f"║  CRITICAL findings: {counts['CRITICAL']:<{width - 21}}║")
        print(f"║  HIGH findings:     {counts['HIGH']:<{width - 21}}║")
        print(f"║  MEDIUM findings:   {counts['MEDIUM']:<{width - 21}}║")
        print(f"║  LOW findings:      {counts['LOW']:<{width - 21}}║")
        print("╠" + "═" * width + "╣")
        print(f"║  {'Full report appended to bug_report.md':<{width - 2}}║")
        print("╚" + "═" * width + "╝")
        print()


# ---------------------------------------------------------------------------
# Convenience entry point for main.py
# ---------------------------------------------------------------------------


def analyze_call(transcript_path: str, scenario: dict, call_folder: str) -> list:
    """
    Run full transcript analysis pipeline for a completed call.

    Returns the list of findings (empty if transcript is missing or malformed).
    """
    try:
        analyzer = TranscriptAnalyzer(transcript_path, scenario, call_folder)
        if not analyzer.transcript_text:
            return []

        analyzer.parse_transcript()
        findings = analyzer.run_all_checks()
        analyzer.append_to_bug_report()
        analyzer.print_summary()
        return findings
    except Exception as exc:
        logger.error("analyze_call failed: %s", exc, exc_info=True)
        return []
