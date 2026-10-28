# BoliDesk — multilingual voice to case

BoliDesk helps a customer support agent turn a short regional-language call into an editable transcript and a practical case brief. It is a small local web application that connects to Gnani's speech APIs while keeping the first routing suggestions transparent and reviewable.

## The problem

Support teams often receive voice messages in several Indian languages. Manually replaying and summarizing every short call slows triage and makes follow-up inconsistent. BoliDesk transcribes a clip, highlights a likely support queue, and offers a short checklist of next steps. The support agent reviews the transcript and confirms the case before using it.

## Who uses it

- Customer support agents working a regional-language queue
- Team leads who need a quick, readable call handoff
- Small support teams evaluating voice workflows before connecting a CRM

## Gnani AI models and APIs

- **Gnani Prisma v2.5 Speech-to-Text REST API** (`POST https://api.vachana.ai/stt/v3`) transcribes supported Indian-language audio clips. Current Gnani REST documentation limits this endpoint to 30 seconds; BoliDesk checks duration locally before sending a clip.
- **Gnani Timbre v2.5 Text-to-Speech REST API** (`POST https://api.vachana.ai/api/v1/tts/inference`) can read the transcript aloud in its selected language. Timbre is an optional beta capability for this demo.
- Case classification and suggested actions use transparent local keyword rules. The app does not claim to use Evon, a generative model, sentiment analysis, intent detection, or transcription confidence.

API keys stay on the local server in `.env`; the browser never receives the key. Audio submitted for transcription and text submitted for optional speech playback are transmitted to Gnani for processing. BoliDesk does not write uploaded audio or transcript content to disk; the draft remains in the browser tab's memory until refresh/close or explicit JSON export. Exports may contain customer-reported text and should be handled according to your organization's privacy rules. Use synthetic data in public demos.

## Run locally

Requires Python 3.10+; there are no third-party packages to install.

1. Copy `.env.example` to `.env` and set `GNANI_API_KEY` from your Gnani API dashboard.
2. Start the app from this folder:

   ```powershell
   python server.py
   ```

3. Open `http://127.0.0.1:8000`.

The synthetic example works without an API key and makes no Gnani API calls. Live transcription and spoken playback require a configured, active Gnani API key. Audio clips must use WAV, MP3, OGG, FLAC, AAC, or M4A and be no longer than 30 seconds. The server checks the file signature and duration for every supported container; files with unreadable duration metadata are rejected instead of guessed. BoliDesk supports the ten Prisma v2.5 REST language codes shown in the language selector.

## Demo walkthrough

1. Click **Load sample call** to show the synthetic duplicate-payment example. It uses local case rules only and consumes no credits.
2. Show the original transcript beside the editable version, and explain that customer claims remain marked as reported rather than verified.
3. Review the suggested category, fill a missing payment date or transaction reference, and inspect the negation/date/evidence flags.
4. Edit or dismiss a follow-up question. Confirm the category, queue, and next action; mark each evidence cue reviewed or dismiss it.
5. Approve the complete case and export the JSON. With API access configured, also demonstrate an under-30-second synthetic clip through Prisma and explicitly start Timbre playback.

## Programme project description

**Project title:** BoliDesk — Multilingual Voice to Case

**Problem:** Support teams spend time replaying regional-language voice calls and manually turning them into actionable, consistently routed cases. BoliDesk converts a short call into an editable transcript and a reviewable support-case draft, identifies missing category-specific details, flags wording that needs confirmation, and requires explicit human approval before export.

**Models/APIs:** Gnani Prisma v2.5 Speech-to-Text REST API for transcription; Gnani Timbre v2.5 Text-to-Speech REST API for user-initiated transcript playback. Deterministic local rules organize a small support taxonomy. No Evon or other LLM integration is used.

**Users:** Customer support agents and team leads handling multilingual customer voice notes or short calls.

**Contribution (solo draft):** The participant builds the web interface and API integration, implements the rule-based triage and export flow, prepares synthetic demo data, and records the demo. If this is a team entry, replace this sentence with each member's actual contribution before submitting.

## Case review behavior

- Supported local categories are payments/billing, delivery, account access, product issue, and general support. Ambiguous or unmatched text defaults to general support and requires agent confirmation.
- Each category defines required fields, optional fields, a proposed queue, and a deterministic next-action template. A required information gap creates a linked follow-up question; the agent may edit or dismiss it. Nothing is sent to a customer.
- Case fields distinguish transcript text, local-rule suggestions, and human edits. Customer statements remain allegations/reports; no transaction, shipment, account, or product system is queried.
- Review cues are conservative: transcript-vs-audio review (the STT response has no verified confidence field), explicit dates/amounts/labelled references, negation, and a small set of contradictory status phrases. Each cue explains its basis and can be marked reviewed or dismissed. These heuristics do not establish that a transcript or business fact is wrong.
- Approval is blocked while required fields, category/queue/action confirmations, stale transcript state, or evidence cues remain open. An approval fingerprint prevents export of a draft edited after approval; such edits require re-approval.
- JSON export uses the current `bolidesk.case.v1` structure and includes original/edited transcript, field provenance, review status, optional gaps, follow-up questions, and flag dispositions. It never includes the API key or internal error details.

## Tests

The project uses Python's standard-library `unittest`; no packages need installing:

```powershell
python -m unittest discover -s tests -v
python -m py_compile server.py workflow.py tests/test_workflow.py
```

The suite mocks Gnani responses. It covers the documented 30-second limit across supported audio containers, signatures, malformed inputs, API-key absence, mocked success/errors/timeouts for Prisma, optional Timbre behavior, category/missing-field logic, uncertainty flags, transcript/case edits, approval, and export freshness. It does not contact Gnani or consume credits.

## Submission checklist

The programme page says the final submission closes at **11:59 PM IST on November 10, 2026**. It asks for a working demo/video, written explanation, Gnani AI models or APIs used, and nominated LinkedIn or X post links. Post the demo publicly before nominating its link. The official page currently says participants receive project/submission details through the form sent after registration; it does not describe a separate participant dashboard.

**Suggested public post (edit to match your actual demo):**

> I built BoliDesk, a multilingual voice-to-case workflow for support teams. It uses Gnani Prisma v2.5 to transcribe short Indian-language calls and Timbre v2.5 for optional transcript playback. It identifies missing case details and wording that needs review, then requires human approval before JSON export. Demo: [link] #GnaniAI #GreatIndianAIInternshipChallenge

Add Gnani AI's tag, publish the post on LinkedIn or X, then paste the live post link into the programme submission. Do not claim the public demo uses a live model call unless you record one with an active API key.
