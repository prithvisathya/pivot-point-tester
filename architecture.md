# Architecture — Pivot Point Orthopedics AI Tester

## How the System Works

When a test scenario is triggered, main.py uses the Twilio REST API to 
place an outbound call to the Pivot Point Orthopedics test line 
(+1-805-439-8008). Twilio connects the live call audio to our local 
FastAPI server running on port 5050 via Twilio Media Streams, a 
WebSocket-based protocol that streams raw audio in real time. Our 
FastAPI server (call_handler.py) receives this audio as base64-encoded 
mulaw 8000hz chunks, converts it to PCM16 24000hz using Python's audioop 
library, and forwards it to the OpenAI Realtime API over a second 
WebSocket connection. OpenAI Realtime processes the incoming audio using 
server-side voice activity detection (VAD), generates a natural spoken 
response as the patient persona defined in the scenario system prompt, 
and streams the response audio back to our server. We convert that audio 
back to mulaw 8000hz and send it to Twilio, which plays it into the live 
phone call. Simultaneously, both sides of the conversation are captured: 
OpenAI provides transcriptions of the agent's speech via Whisper, and 
transcriptions of the bot's own speech via response audio transcript 
events. All transcript lines are passed to recorder.py which saves a 
timestamped transcript after the call ends. The full two-party phone 
recording is downloaded from Twilio (record=True on outbound calls) and 
saved as call.mp3; patient-bot-only audio from the media stream is kept 
as patient_bot.mp3. Once saved, analyzer.py scans the transcript for ten 
predefined bug patterns and appends any findings to bug_report.md.

After all 25 scenarios run, submission.py scores every quality call 
(duration, turn count, audio size, and bug findings) and copies only the 
top 10 into submission_recordings/ for the GitHub submission.

## Why These Design Choices

OpenAI Realtime API was chosen as the patient voice brain because it 
handles the full speech-to-speech loop with extremely low latency, 
produces natural-sounding conversational voice output, and includes 
built-in server-side VAD that manages turn-taking without manual 
audio buffering logic. This was critical because the evaluation 
criteria explicitly prioritize natural conversational voice quality 
above all else. Twilio was chosen for telephony because it is the 
industry standard for programmable voice calls, provides reliable 
outbound calling via a simple REST API, and its Media Streams feature 
makes it straightforward to intercept and pipe raw audio over WebSockets. 
FastAPI was chosen as the local server because its native async support 
makes it well-suited for handling two simultaneous WebSocket connections 
(one to Twilio, one to OpenAI) without blocking. The scenario-based 
persona approach was chosen over scripted call flows because it produces 
more realistic conversations: rather than following a rigid script, the 
OpenAI model receives a goal and personality description and improvises 
naturally in response to whatever the agent says. This surfaces bugs 
that scripted approaches would miss, particularly around context handling, 
interruption behavior, and edge case responses.
