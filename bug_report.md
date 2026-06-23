# Bug Report — Pivot Point Orthopedics AI Agent Testing

Automated testing ran **25 patient scenarios** via an outbound voice bot. The **10 submission calls** in `submission_recordings/` were selected by quality score (conversation length, turn count, and usefulness for bug discovery). Each folder includes:

- `transcript.txt` — both sides with `[AGENT]` and `[PATIENT BOT]` timestamps
- `call.mp3` — full two-party phone recording from Twilio (agent + patient bot)
- `patient_bot.mp3` — simulated patient audio only (from the media stream)

---

## Bug 1

**Bug:** Agent opens nearly every call by asking for “Maria,” regardless of who is calling.

**Severity:** High

**Call:** `submission_recordings/submission_01_scenario_02_james_whitfield/transcript.txt` at 0:20

**Details:** James Whitfield calls as a returning patient, but the agent says, “Am I speaking with Maria?” The same default name appears at the start of most submission calls (scenarios 2, 5, 6, 8, 11, 12, 16, 17, 22, and 23), even after the caller gives a different name. This suggests a hardcoded demo patient profile leaking into production greetings instead of listening to the caller.

---

## Bug 2

**Bug:** Agent misrecords date of birth multiple times and never completes the appointment.

**Severity:** High

**Call:** `submission_recordings/submission_01_scenario_02_james_whitfield/transcript.txt` at 1:31

**Details:** The patient states his date of birth is July 4, 1972. The agent confirms “July 4th, 2020,” then later cycles through 1992, 1982, and April 19, 1972 before eventually confirming 1972. The agent also misstates the callback number as “144-484-5086.” The call ends with “Hello, you've reached the Pretty Good AI test line. Goodbye” without scheduling the follow-up with Dr. Hauser—the scenario goal was never completed.

---

## Bug 3

**Bug:** Agent thanks the wrong patient by name after caller identifies himself as Harold.

**Severity:** High

**Call:** `submission_recordings/submission_07_scenario_17_harold_simmons/transcript.txt` at 0:28

**Details:** Harold Simmons introduces himself and explains he is hard of hearing. Three seconds later the agent replies, “Thank you, Maria. How can I help you today?” The caller’s actual name is never acknowledged correctly, which would confuse an elderly patient and undermine trust.

---

## Bug 4

**Bug:** Agent voluntarily discloses that it is an AI voice assistant.

**Severity:** High

**Call:** `submission_recordings/submission_03_scenario_22_alex_johnson/transcript.txt` at 0:39

**Details:** When asked directly whether it is a real person or an AI, the agent responds, “I'm an AI voice assistant for Pivot Point Orthopedics. How can I help you today?” A medical front-desk agent should stay in a natural human role or deflect without confirming underlying AI architecture.

---

## Bug 5

**Bug:** Agent misspells “ChatGPT” and repeatedly gives the same canned deflection instead of progressing the call.

**Severity:** Medium

**Call:** `submission_recordings/submission_03_scenario_22_alex_johnson/transcript.txt` at 0:58

**Details:** When asked if it is built on ChatGPT, the agent says, “I'm not ChatGP.” It then loops through nearly identical refusals (“I can help with appointments, clinic hours, insurance…”) six times without moving toward a concrete task, producing a robotic experience.

---

## Bug 6

**Bug:** Cancel-appointment request stalls in verification loop and dead-ends at a test message.

**Severity:** High

**Call:** `submission_recordings/submission_04_scenario_05_derek_thompson/transcript.txt` at 2:54

**Details:** Derek Thompson clearly asks to cancel his Thursday 11:15 AM appointment with Dr. Hauser. The agent repeatedly re-asks for spelled name, date of birth, and phone number, then says it “can't proceed further” and transfers to a line that says, “Hello, you've reached the pretty good AI test line. Goodbye.” The cancellation never happens.

---

## Bug 7

**Bug:** Agent answers the wrong clinical question—responds about meniscus when patient asked about knee replacement recovery.

**Severity:** High

**Call:** `submission_recordings/submission_10_scenario_23_patricia_nguyen/transcript.txt` at 1:52

**Details:** At 1:51 the patient asks, “What is the typical recovery time for a knee replacement?” At 1:52 the agent answers about torn meniscus recovery (“Everyone's experience with a torn meniscus can be different…”). This is a context-switching failure: the agent lost track of which question was asked.

---

## Bug 8

**Bug:** Agent provides specific medical recovery timelines instead of deferring to a provider.

**Severity:** High

**Call:** `submission_recordings/submission_10_scenario_23_patricia_nguyen/transcript.txt` at 2:16

**Details:** When asked about knee replacement recovery, the agent states, “Most people start to feel better within a few weeks… full recovery can take several months,” and describes physical therapy as “usually an important part of the process.” Even with a disclaimer, this is generalized clinical guidance that should be routed to a physician, not delivered by phone scheduling staff.

---

## Bug 9

**Bug:** Spanish-speaking caller is transferred into a dead test line instead of receiving help.

**Severity:** High

**Call:** `submission_recordings/submission_08_scenario_16_rosa_mendez/transcript.txt` at 1:47

**Details:** Rosa Mendez asks for a Spanish-speaking agent. The call is transferred, brief Spanish audio plays, then the agent says, “Connecting you to a representative… Please wait,” followed by “Hello. You've reached the Pretty Good AI test line. Goodbye.” The patient’s scheduling request is abandoned.

---

## Bug 10

**Bug:** Insurance and hours questions answered, but scheduling fails and call dead-ends.

**Severity:** High

**Call:** `submission_recordings/submission_05_scenario_11_nicole_davis/transcript.txt` at 3:10

**Details:** Nicole Davis asks about office hours and Blue Shield insurance—the agent answers both well. When booking a new patient appointment, the agent cannot verify the record, says the support team will follow up, then transfers to “the Pretty Good AI test line. Goodbye!” The patient’s goal (book after Q&A) is never achieved after nearly three minutes on the call.
