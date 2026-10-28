import json
import os
import unittest
import wave
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

import workflow
from workflow import AppError


def make_wav(seconds=1, sample_rate=8000):
    buffer = BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * int(seconds * sample_rate))
    return buffer.getvalue()


def atom(kind, payload):
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def make_m4a(seconds=31):
    ftyp = atom(b"ftyp", b"isom\x00\x00\x02\x00isom")
    mvhd = bytes(12) + (1000).to_bytes(4, "big") + (seconds * 1000).to_bytes(4, "big") + bytes(80)
    return ftyp + atom(b"moov", atom(b"mvhd", mvhd))


def make_flac(seconds=31, sample_rate=44100):
    payload = bytearray(34)
    packed = (sample_rate << 44) | (sample_rate * seconds)
    payload[10:18] = packed.to_bytes(8, "big")
    return b"fLaC" + b"\x80\x00\x00\x22" + bytes(payload)


def make_ogg(seconds=31):
    payload = b"OpusHead" + bytes(11)
    header = (
        b"OggS\x00\x02"
        + (48000 * seconds).to_bytes(8, "little")
        + b"\x01\x00\x00\x00"  # serial
        + b"\x00\x00\x00\x00"  # page sequence
        + b"\x00\x00\x00\x00"  # checksum (not needed for duration parsing)
        + b"\x01"                # one lacing value
        + bytes([len(payload)])
    )
    return header + payload


def make_aac(frames):
    # A minimal ADTS frame at 44.1 kHz with a 7-byte header.
    return (b"\xff\xf1\x50\x80\x00\xe0\xfc") * frames


def make_mp3(frames):
    # MPEG-1 Layer III, 128 kbit/s, 44.1 kHz: 417 bytes per frame.
    frame = b"\xff\xfb\x90\x00" + bytes(413)
    return frame * frames


class FakeResponse:
    def __init__(self, body, content_type="application/json"):
        self.body = body
        self.headers = {"Content-Type": content_type}

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class AudioValidationTests(unittest.TestCase):
    def test_accepts_supported_wav_and_verifies_duration(self):
        info = workflow.validate_audio_upload("call.wav", make_wav(30))
        self.assertEqual(info["format"], "wav")
        self.assertEqual(info["duration_seconds"], 30)

    def test_rejects_empty_bad_extension_and_mismatched_content(self):
        for filename, payload in (("empty.wav", b""), ("clip.exe", b"x"), ("wrong.wav", b"ID3" + bytes(20))):
            with self.subTest(filename=filename), self.assertRaises(AppError):
                workflow.validate_audio_upload(filename, payload)

    def test_rejects_audio_over_current_prisma_rest_limit_for_all_formats(self):
        clips = [
            ("long.wav", make_wav(30.1)),
            ("long.mp3", make_mp3(1151)),
            ("long.aac", make_aac(1293)),
            ("long.flac", make_flac(31)),
            ("long.ogg", make_ogg(31)),
            ("long.m4a", make_m4a(31)),
        ]
        for filename, payload in clips:
            with self.subTest(filename=filename), self.assertRaisesRegex(AppError, "up to 30 seconds"):
                workflow.validate_audio_upload(filename, payload)

    def test_rejects_unreadable_audio_duration_instead_of_guessing(self):
        with self.assertRaisesRegex(AppError, "no readable audio frames"):
            workflow.validate_audio_upload("empty-frames.mp3", b"ID3\x04\x00\x00\x00\x00\x00\x00")


class GnaniAdapterTests(unittest.TestCase):
    def test_missing_api_key_fails_without_network_call(self):
        with patch.dict(os.environ, {"GNANI_API_KEY": ""}), patch("workflow.urlopen") as opener:
            with self.assertRaisesRegex(AppError, "not configured") as caught:
                workflow.transcribe_audio("call.wav", make_wav(), "hi-IN")
        self.assertEqual(caught.exception.status, 503)
        opener.assert_not_called()

    def test_successful_transcription_uses_verified_endpoint_and_multipart(self):
        body = json.dumps({"success": True, "transcript": "नमस्ते", "request_id": "req-test", "model": "gnani-prisma-v2.5"}).encode()
        with patch("workflow.urlopen", return_value=FakeResponse(body)) as opener:
            result = workflow.transcribe_audio("sample.wav", make_wav(), "hi-IN", api_key="test-key")
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.vachana.ai/stt/v3")
        self.assertEqual(request.get_header("X-api-key-id"), "test-key")
        self.assertIn(b'name="audio_file"', request.data)
        self.assertIn(b"language_code", request.data)
        self.assertEqual(opener.call_args.kwargs["timeout"], workflow.API_TIMEOUT_SECONDS)
        self.assertEqual(result["transcript"], "नमस्ते")
        self.assertEqual(result["request_id"], "req-test")

    def test_malformed_or_failed_transcription_responses_are_reported(self):
        with patch("workflow.urlopen", return_value=FakeResponse(b"not-json")):
            with self.assertRaisesRegex(AppError, "unreadable response"):
                workflow.transcribe_audio("sample.wav", make_wav(), "hi-IN", api_key="test-key")
        with patch("workflow.urlopen", return_value=FakeResponse(json.dumps({"success": False, "error": {"message": "bad audio"}}).encode())):
            with self.assertRaisesRegex(AppError, "bad audio"):
                workflow.transcribe_audio("sample.wav", make_wav(), "hi-IN", api_key="test-key")

    def test_api_http_failure_and_timeout_are_safe(self):
        failure = HTTPError("https://api.vachana.ai/stt/v3", 403, "Forbidden", {}, BytesIO(b'{"error":{"message":"No credits"}}'))
        with patch("workflow.urlopen", side_effect=failure):
            with self.assertRaisesRegex(AppError, "No credits") as caught:
                workflow.transcribe_audio("sample.wav", make_wav(), "hi-IN", api_key="test-key")
        self.assertEqual(caught.exception.status, 502)
        with patch("workflow.urlopen", side_effect=TimeoutError()):
            with self.assertRaisesRegex(AppError, "did not respond in time") as caught:
                workflow.transcribe_audio("sample.wav", make_wav(), "hi-IN", api_key="test-key")
        self.assertEqual(caught.exception.status, 504)

    def test_timbre_success_and_optional_failure(self):
        audio = b"RIFF" + bytes(40)
        with patch("workflow.urlopen", return_value=FakeResponse(audio, "audio/wav")) as opener:
            result = workflow.synthesize_speech("नमस्ते", "hi-IN", api_key="test-key")
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.vachana.ai/api/v1/tts/inference")
        self.assertEqual(request.get_header("X-api-key-id"), "test-key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "timbre-v2.5")
        self.assertEqual(payload["voice"], "Bhavna")
        self.assertEqual(workflow.base64.b64decode(result["audio_base64"]), audio)

        failure = HTTPError("https://api.vachana.ai/api/v1/tts/inference", 503, "Unavailable", {}, BytesIO(b"{}"))
        with patch("workflow.urlopen", side_effect=failure):
            with self.assertRaisesRegex(AppError, "temporarily unavailable"):
                workflow.synthesize_speech("नमस्ते", "hi-IN", api_key="test-key")


class CaseWorkflowTests(unittest.TestCase):
    def test_classifies_supported_categories_deterministically(self):
        examples = {
            "I was charged twice. Please reverse the duplicate payment.": "payments",
            "The parcel is delayed. Please share an update.": "delivery",
            "I cannot log in to the app. Please help me recover access.": "account_access",
            "My device is broken. Please arrange a repair.": "product_issue",
        }
        for transcript, category in examples.items():
            with self.subTest(category=category):
                self.assertEqual(workflow.build_case(transcript, "manual")["category"], category)

    def test_unknown_and_ambiguous_categories_require_manual_confirmation(self):
        unknown = workflow.build_case("Hello, I have a question.", "manual")
        self.assertEqual(unknown["category"], "general")
        self.assertFalse(unknown["category_confirmed"])
        self.assertTrue(any(flag["id"] == "category_ambiguity" for flag in unknown["review"]["flags"]))
        ambiguous = workflow.build_case("My payment was delayed and I need a refund.", "manual")
        self.assertEqual(ambiguous["category"], "general")
        self.assertEqual(set(ambiguous["category_candidates"]), {"payments", "delivery"})

    def test_required_fields_and_questions_are_category_specific(self):
        case = workflow.build_case("I was charged twice. Please reverse the duplicate payment.", "manual")
        validation = workflow.validate_case(case)
        missing_ids = {item["id"] for item in validation["missing_fields"]}
        self.assertIn("payment_locator", missing_ids)
        self.assertNotIn("requested_outcome", missing_ids)
        self.assertEqual(validation["suggested_questions"][0]["field"], "payment_locator")
        self.assertIn("transaction reference or payment date", validation["suggested_questions"][0]["text"])

    def test_negation_and_contradiction_flags_are_explainable(self):
        case = workflow.build_case("I have not received the refund. The refund was already issued.", "manual")
        flags = {flag["id"]: flag for flag in case["review"]["flags"]}
        self.assertIn("negation", flags)
        self.assertIn("refund_contradiction", flags)
        self.assertTrue(flags["negation"]["evidence"])
        self.assertIn("not", " ".join(flags["negation"]["evidence"]).lower())
        self.assertEqual(case["fields"]["actual_outcome"], "The refund was already issued.")
        self.assertEqual(case["field_sources"]["actual_outcome"], "transcript")

    def test_transcript_edit_rebuilds_fields_and_preserves_original(self):
        original = "I need help with a service."
        edited = "I was charged twice on 2026-10-28. Please reverse the duplicate payment."
        case = workflow.build_case(edited, "prisma", original_transcript=original)
        self.assertEqual(case["transcript"]["original"], original)
        self.assertEqual(case["transcript"]["edited"], edited)
        self.assertEqual(case["fields"]["reported_problem"], edited)
        self.assertEqual(case["category"], "payments")
        self.assertEqual(case["fields"]["payment_date"], "2026-10-28")

    def test_missing_transcript_and_invalid_case_are_rejected(self):
        with self.assertRaisesRegex(AppError, "Add a transcript"):
            workflow.build_case("   ")
        with self.assertRaisesRegex(AppError, "supported BoliDesk format"):
            workflow.validate_case({})

    def test_synthetic_case_generation_has_no_api_call(self):
        with patch("workflow.urlopen") as opener:
            case = workflow.build_case("I was charged twice. Please refund me.", "synthetic")
        opener.assert_not_called()
        self.assertEqual(case["transcript"]["source"], "synthetic")

    def test_approval_is_required_and_edits_invalidate_export_fingerprint(self):
        case = workflow.build_case("I was charged twice on 2026-10-28. Please reverse the duplicate payment.", "manual")
        case["category_confirmed"] = True
        case["review"]["routing_confirmed"] = True
        case["review"]["action_confirmed"] = True
        for flag in case["review"]["flags"]:
            flag["status"] = "confirmed"
        case["fields"]["title"] = "Duplicate payment reported"
        case["field_sources"]["title"] = "human-edited"
        self.assertTrue(workflow.validate_case(case)["can_approve"])
        with self.assertRaisesRegex(AppError, "Approve the current case"):
            workflow.export_case(case)
        approved = workflow.approve_case(case)
        exported = workflow.export_case(approved)
        self.assertEqual(exported["review"]["status"], "approved")
        self.assertNotIn("approval_fingerprint", exported["review"])
        self.assertEqual(exported["fields"]["title"], "Duplicate payment reported")
        approved["fields"]["title"] = "Edited after approval"
        with self.assertRaisesRegex(AppError, "changed after approval"):
            workflow.export_case(approved)
        reapproved = workflow.approve_case(approved)
        self.assertEqual(workflow.export_case(reapproved)["fields"]["title"], "Edited after approval")

    def test_required_gaps_block_approval_but_optional_fields_do_not(self):
        case = workflow.build_case("I was charged twice on 2026-10-28. Please reverse the duplicate payment.", "manual")
        case["category_confirmed"] = True
        case["review"]["routing_confirmed"] = True
        case["review"]["action_confirmed"] = True
        for flag in case["review"]["flags"]:
            flag["status"] = "confirmed"
        case["fields"]["payment_date"] = ""
        with self.assertRaisesRegex(AppError, "not ready for approval"):
            workflow.approve_case(case)
        case["fields"]["payment_date"] = "2026-10-28"
        case["fields"]["amount"] = ""
        self.assertTrue(workflow.validate_case(case)["can_approve"])
        self.assertIn("amount", {item["id"] for item in workflow.validate_case(case)["optional_missing"]})


if __name__ == "__main__":
    unittest.main()
