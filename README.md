# BoliDesk — multilingual voice to case

BoliDesk helps a customer support agent turn a short regional-language call into an editable transcript and a practical case brief. It is a small local web application that connects to Gnani's speech APIs while keeping the first routing suggestions transparent and reviewable.

## The problem

Support teams often receive voice messages in several Indian languages. Manually replaying and summarizing every short call slows triage and makes follow-up inconsistent. BoliDesk transcribes a clip, highlights a likely support queue, and offers a short checklist of next steps. The support agent reviews the transcript and confirms the case before using it.

## Who uses it

- Customer support agents working a regional-language queue
- Team leads who need a quick, readable call handoff
- Small support teams evaluating voice workflows before connecting a CRM

## Gnani AI models and APIs

- **Gnani Prisma v2.5 Speech-to-Text REST API** (`POST https://api.vachana.ai/stt/v3`) transcribes supported Indian-language audio clips up to 60 seconds.
- **Gnani Timbre v2.5 Text-to-Speech REST API** (`POST https://api.vachana.ai/api/v1/tts/inference`) can read the transcript aloud in its selected language. Timbre is an optional beta capability for this demo.
- Case classification and suggested actions use transparent local keyword rules. The app does not claim to use Evon or to have AI-generated sentiment. It uses no third-party LLM.

API keys stay on the local server in `.env`; the browser never receives the key. Calls are sent to Gnani for transcription/synthesis and this demo does not save uploaded audio or transcripts to disk. Do not use real customer identifiers or sensitive recordings in a public demo.

## Run locally

Requires Python 3.10+; there are no third-party packages to install.

1. Copy `.env.example` to `.env` and set `GNANI_API_KEY` from your Gnani API dashboard.
2. Start the app from this folder:

   ```powershell
   python server.py
   ```

3. Open `http://127.0.0.1:8000`.

The synthetic example works without an API key and uses no credits. Live transcription and spoken playback require a configured, active Gnani API key. Audio clips must use WAV, MP3, OGG, FLAC, AAC, or M4A and be no longer than 60 seconds. The application supports the Prisma v2.5 REST language codes shown in the language selector.

## Demo walkthrough

1. Click **Load sample call** to show the synthetic duplicate-payment example and the local case brief.
2. Explain that the sample is synthetic and its case routing comes from transparent rules.
3. If API access is configured, upload a short, synthetic Hindi or English clip and click **Transcribe call**. Show the Prisma transcript, edit it, and update the brief.
4. Click the speaker control to use Timbre to read the transcript aloud, then export the case brief JSON.
5. Point out that the agent confirms all suggested signals before routing.

## Programme project description

**Project title:** BoliDesk — Multilingual Voice to Case

**Problem:** Support teams spend time replaying regional-language voice calls and manually turning them into actionable, consistently routed cases. BoliDesk converts a short call into an editable transcript and a reviewable support brief.

**Models/APIs:** Gnani Prisma v2.5 Speech-to-Text REST API for transcription; Gnani Timbre v2.5 Text-to-Speech REST API for optional transcript playback. Local keyword rules organize the case. Evon is not used in the current build.

**Users:** Customer support agents and team leads handling multilingual customer voice notes or short calls.

**Contribution (solo draft):** The participant builds the web interface and API integration, implements the rule-based triage and export flow, prepares synthetic demo data, and records the demo. If this is a team entry, replace this sentence with each member's actual contribution before submitting.

## Submission checklist

The programme page says the final submission closes at **11:59 PM IST on November 10, 2026**. It asks for a working demo/video, written explanation, Gnani AI models or APIs used, and nominated LinkedIn or X post links. Post the demo publicly before nominating its link. The official page currently says participants receive project/submission details through the form sent after registration; it does not describe a separate participant dashboard.

**Suggested public post (edit to match your actual demo):**

> I built BoliDesk, a multilingual voice-to-case workflow for support teams. It uses Gnani Prisma v2.5 to transcribe short Indian-language calls and Timbre v2.5 to optionally read transcripts aloud. The case routing checklist is transparent, local, and always reviewed by a person. Demo: [link] #GnaniAI #GreatIndianAIInternshipChallenge

Add Gnani AI's tag, publish the post on LinkedIn or X, then paste the live post link into the programme submission. Do not claim the public demo uses a live model call unless you record one with an active API key.
