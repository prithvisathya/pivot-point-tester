# Pivot Point Orthopedics — AI Voice Bot Tester
## Pretty Good AI Engineering Challenge Submission


#**Walkthrough:** [Watch on Loom](https://www.loom.com/share/b0604c1b242b40c488a7b0cf324644ec)

---

## What This Does
This project is an automated voice bot that calls Pivot Point Orthopedics' 
AI phone agent (+1-805-439-8008) and simulates realistic patient scenarios. 
It tests the agent for bugs, quality issues, and edge case failures. 
The bot records every call, transcribes both sides of the conversation, 
and automatically analyzes transcripts for known bug patterns.

---

## How It Works
1. You run a scenario from the command line
2. The bot calls the Pivot Point Orthopedics test line via Twilio
3. OpenAI Realtime API powers a realistic patient voice persona
4. The full conversation is recorded and transcribed
5. An analyzer scans the transcript for bugs automatically
6. Findings are appended to bug_report.md

---

## Prerequisites
Before you start you need all of these:

- Python 3.11 or higher
  Check with: `python3 --version`

- A Twilio account with a purchased US phone number
  Sign up at: https://twilio.com
  You need: Account SID, Auth Token, and a phone number

- An OpenAI API key with access to the Realtime API
  Get one at: https://platform.openai.com

- ngrok installed on your machine
  Download at: https://ngrok.com/download
  On Mac with Homebrew: `brew install ngrok`

---

## Installation

**Step 1: Clone the repository**
```bash
git clone https://github.com/prithvisathya/pivot-point-tester.git
cd pivot-point-tester
```

**Step 2: Install dependencies**
```bash
pip install -r requirements.txt
```

**Step 3: Set up your environment variables**
```bash
cp .env.example .env
```

Now open `.env` in any text editor and fill in all six values:
- `OPENAI_API_KEY`: Your OpenAI API key
- `TWILIO_ACCOUNT_SID`: Found on your Twilio dashboard homepage
- `TWILIO_AUTH_TOKEN`: Found on your Twilio dashboard homepage
- `TWILIO_PHONE_NUMBER`: Your purchased Twilio number in +1XXXXXXXXXX format
- `TARGET_PHONE_NUMBER`: Leave as +18054398008
- `NGROK_URL`: You will fill this in after step 4

---

## Running ngrok (Required Every Time)

ngrok exposes your local server to the internet so Twilio can reach it.
You must run this every time before making calls.

Open a new terminal window and run:
```bash
ngrok http 5050
```

You will see output like this:
```
Forwarding  https://abc123.ngrok-free.app -> http://localhost:5050
```

Copy the `https://` URL and paste it into your `.env` file as `NGROK_URL`.
Important: Use the https version, not http.
Important: The ngrok URL changes every time you restart ngrok.
           Update your `.env` file each time.

---

## Running the Bot

Make sure ngrok is running in a separate terminal first.

List all available scenarios:
```bash
python main.py --list
```

Run a single scenario:
```bash
python main.py --scenario 1
```

Run multiple specific scenarios:
```bash
python main.py --scenario 1 3 5
```

Run all 25 scenarios (auto-picks the best 10 for submission when finished):
```bash
python main.py --all
```

### How the top 10 submission calls are chosen

Every scenario run is saved under `recordings/`. After `--all` finishes,
`submission.py` ranks calls that pass a quality gate (complete MP3,
≥60 seconds, ≥4 speaker turns, full transcript). Scoring:

- **+1** per second of conversation
- **+5** per speaker turn
- **+1** per KB of audio
- **+25** per automated bug finding
- **+15** bonus for core scenario types (scheduling, cancel, refill, etc.)

The best call per scenario ID is kept, then the **top 10 scores** are
copied into `submission_recordings/`. Preview rankings without writing files:

```bash
python main.py --curate-dry-run
```

Manually re-curate from existing recordings/:

```bash
python main.py --curate-submission
```

Run only the 10 curated submission scenarios (auto-promotes each):
```bash
python main.py --submission
```

---

## Output Files

After each call you will find:

- `recordings/call_01/transcript.txt` — Full conversation transcript (local scratch)
- `recordings/call_01/call.mp3` — Audio recording of the call (local scratch)
- `submission_recordings/` — Curated calls that meet submission quality bar (committed to GitHub)
  - `call.mp3` — **Full two-party call** downloaded from Twilio after each run (agent + patient bot)
  - `patient_bot.mp3` — Simulated patient audio only (debug track from the media stream)
  - `transcript.txt` — Both sides with `[AGENT]` and `[PATIENT BOT]` timestamps
- `bug_report.md` — Auto-updated bug findings
- `test_run.log` — Full debug log of the run

Quality calls are saved under `recordings/` during test runs. After `--all`,
the best 10 are scored and copied into `submission_recordings/` (see
**How the top 10 submission calls are chosen** above). The challenge
requires **10 submission calls** in the repo.

Check progress:
```bash
python main.py --submission-status
```

Promote one call manually:
```bash
python main.py --promote recordings/call_14 --scenario 1
```

---

## Troubleshooting

**Problem:** "Missing required environment variables"  
**Fix:** Make sure you copied `.env.example` to `.env` and filled in all values

**Problem:** "NGROK_URL must start with https://"  
**Fix:** Start ngrok with: `ngrok http 5050`  
     Copy the https:// forwarding URL into your `.env`

**Problem:** Call connects but bot does not speak  
**Fix:** Check your `OPENAI_API_KEY` is valid and has Realtime API access  
     Check the `test_run.log` for error messages

**Problem:** "No such host" or Twilio error  
**Fix:** Verify your `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` are correct  
     Make sure your Twilio phone number is in +1XXXXXXXXXX format

**Problem:** Audio sounds distorted  
**Fix:** This is usually a sample rate conversion issue  
     Check `test_run.log` for audioop errors

---

## Project Structure

```
pivot-point-tester/
├── main.py              Entry point, triggers calls and manages scenarios
├── call_handler.py      FastAPI server, bridges Twilio and OpenAI audio
├── openai_realtime.py   OpenAI Realtime API client
├── scenarios.py         All 25 patient test personas
├── recorder.py          Saves audio recordings and transcripts
├── analyzer.py          Scans transcripts for bug patterns
├── submission.py        Promotes quality calls into submission_recordings/
├── bug_report.md        Running bug report updated after each call
├── submission_recordings/ Curated submission calls (MP3 + transcript)
├── .env.example         Template for required environment variables
├── requirements.txt     Python dependencies
└── recordings/          Local scratch recordings (gitignored)
```

---

## Cost Estimate
Each call costs approximately:
- Twilio outbound call: ~$0.014 per minute
- OpenAI Realtime API: ~$0.06 per minute of audio
- 25 calls at 2 minutes average = approximately $3-5 total
