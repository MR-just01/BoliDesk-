"""Deterministic case workflow and verified Gnani REST adapters for BoliDesk."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import mimetypes
import os
import re
import socket
import struct
import threading
import uuid
import wave
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_BASE = "https://api.vachana.ai"
CASE_SCHEMA_VERSION = "bolidesk.case.v1"
MAX_AUDIO_BYTES = 24 * 1024 * 1024
MAX_TTS_CHARS = 5000
MAX_AUDIO_SECONDS = 30  # Current Gnani Prisma REST docs specify 30 seconds.
API_TIMEOUT_SECONDS = 60
SUPPORTED_LANGUAGES = {
    "bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "pa-IN", "ta-IN", "te-IN"
}
TTS_VOICES = {
    "bn-IN": "Kirra", "en-IN": "Kaveri", "gu-IN": "Falak", "hi-IN": "Bhavna",
    "kn-IN": "Saanvi", "ml-IN": "Reshma", "mr-IN": "Ishaan", "pa-IN": "Zayan",
    "ta-IN": "Trisha", "te-IN": "Suhana",
}

FIELD_DEFINITIONS = {
    "title": {"label": "Case title", "kind": "input", "placeholder": "Short, neutral title"},
    "reported_problem": {"label": "Customer-reported problem", "kind": "textarea", "placeholder": "What the customer said", "hint": "Reported from the transcript; not independently verified."},
    "requested_outcome": {"label": "Requested outcome", "kind": "textarea", "placeholder": "Only fill if the customer explicitly asked for an outcome"},
    "actual_outcome": {"label": "Customer-reported outcome (unverified)", "kind": "textarea", "placeholder": "Leave blank unless the caller explicitly reports an outcome", "hint": "This is the caller's statement, not a verified business record."},
    "payment_date": {"label": "Payment date", "kind": "input", "placeholder": "Date stated by the customer"},
    "transaction_reference": {"label": "Transaction reference", "kind": "input", "placeholder": "Reference stated by the customer"},
    "amount": {"label": "Amount", "kind": "input", "placeholder": "Amount stated by the customer"},
    "order_reference": {"label": "Order reference", "kind": "input", "placeholder": "Order reference stated by the customer"},
    "affected_service": {"label": "Affected account or service", "kind": "input", "placeholder": "Service or account area; never request a password or OTP"},
    "product_name": {"label": "Product", "kind": "input", "placeholder": "Product stated by the customer"},
    "proposed_queue": {"label": "Proposed support queue", "kind": "input", "placeholder": "Choose a queue for an agent to review"},
    "next_action": {"label": "Suggested next action", "kind": "textarea", "placeholder": "A reviewable action for the support agent"},
}

CATEGORY_CONFIG = {
    "payments": {
        "label": "Payments & billing", "queue": "Billing & payments", "action": "Review the customer-reported payment and confirm the next step.",
        "terms": ("refund", "refunded", "charge", "charged", "payment", "paid", "billing", "bill", "emi", "duplicate", "रिफंड", "भुगतान", "पैसे वापस", "चार्ज", "काटा"),
        "visible_fields": ("title", "reported_problem", "requested_outcome", "actual_outcome", "payment_date", "transaction_reference", "amount", "proposed_queue", "next_action"),
        "required_groups": (
            {"id": "title", "label": "Case title", "fields": ("title",)},
            {"id": "reported_problem", "label": "Customer-reported problem", "fields": ("reported_problem",)},
            {"id": "requested_outcome", "label": "Requested outcome", "fields": ("requested_outcome",)},
            {"id": "payment_locator", "label": "Payment date or transaction reference", "fields": ("payment_date", "transaction_reference")},
        ),
        "optional_fields": ("actual_outcome", "amount"),
    },
    "delivery": {
        "label": "Order & delivery", "queue": "Order support", "action": "Check the customer-reported delivery status and share a verified update.",
        "terms": ("delivery", "deliver", "delivered", "shipment", "parcel", "tracking", "delayed", "late", "डिलीवरी", "पार्सल", "विलंब"),
        "visible_fields": ("title", "reported_problem", "requested_outcome", "actual_outcome", "order_reference", "proposed_queue", "next_action"),
        "required_groups": (
            {"id": "title", "label": "Case title", "fields": ("title",)},
            {"id": "reported_problem", "label": "Customer-reported problem", "fields": ("reported_problem",)},
            {"id": "requested_outcome", "label": "Requested outcome", "fields": ("requested_outcome",)},
            {"id": "order_reference", "label": "Order reference", "fields": ("order_reference",)},
        ),
        "optional_fields": ("actual_outcome",),
    },
    "account_access": {
        "label": "Account access", "queue": "Account support", "action": "Review the reported access problem and use the approved recovery flow.",
        "terms": ("login", "log in", "logged in", "otp", "password", "locked out", "account access", "लॉगिन", "पासवर्ड"),
        "visible_fields": ("title", "reported_problem", "requested_outcome", "actual_outcome", "affected_service", "proposed_queue", "next_action"),
        "required_groups": (
            {"id": "title", "label": "Case title", "fields": ("title",)},
            {"id": "reported_problem", "label": "Customer-reported problem", "fields": ("reported_problem",)},
            {"id": "requested_outcome", "label": "Requested outcome", "fields": ("requested_outcome",)},
            {"id": "affected_service", "label": "Affected account or service", "fields": ("affected_service",)},
        ),
        "optional_fields": ("actual_outcome",),
    },
    "product_issue": {
        "label": "Product issue", "queue": "Product support", "action": "Confirm the product and reported fault before suggesting troubleshooting.",
        "terms": ("broken", "damaged", "defective", "not working", "stopped working", "faulty", "खराब"),
        "visible_fields": ("title", "reported_problem", "requested_outcome", "actual_outcome", "product_name", "proposed_queue", "next_action"),
        "required_groups": (
            {"id": "title", "label": "Case title", "fields": ("title",)},
            {"id": "reported_problem", "label": "Customer-reported problem", "fields": ("reported_problem",)},
            {"id": "requested_outcome", "label": "Requested outcome", "fields": ("requested_outcome",)},
            {"id": "product_name", "label": "Product", "fields": ("product_name",)},
        ),
        "optional_fields": ("actual_outcome",),
    },
    "general": {
        "label": "General support", "queue": "General support", "action": "Review the customer's request and select the most appropriate support queue.",
        "terms": (),
        "visible_fields": ("title", "reported_problem", "requested_outcome", "actual_outcome", "proposed_queue", "next_action"),
        "required_groups": (
            {"id": "title", "label": "Case title", "fields": ("title",)},
            {"id": "reported_problem", "label": "Customer-reported problem", "fields": ("reported_problem",)},
            {"id": "requested_outcome", "label": "Requested outcome", "fields": ("requested_outcome",)},
        ),
        "optional_fields": ("actual_outcome",),
    },
}

FOLLOW_UP_TEMPLATES = {
    "title": "What short, neutral title should identify this case?",
    "reported_problem": "What problem should this case record?",
    "requested_outcome": "What outcome is the customer asking for?",
    "payment_locator": "Can the customer provide a transaction reference or payment date?",
    "order_reference": "What order reference should the support agent use?",
    "affected_service": "Which account or service is affected? Do not request a password or OTP.",
    "product_name": "Which product is affected?",
}

_GNANI_REQUEST_LOCK = threading.Lock()


class AppError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def case_metadata() -> dict:
    categories = {}
    for key, config in CATEGORY_CONFIG.items():
        categories[key] = {
            "label": config["label"], "default_queue": config["queue"], "default_action": config["action"],
            "visible_fields": list(config["visible_fields"]),
            "required_groups": [dict(group, fields=list(group["fields"])) for group in config["required_groups"]],
            "optional_fields": list(config["optional_fields"]),
        }
    return {"categories": categories, "fields": copy.deepcopy(FIELD_DEFINITIONS), "languages": sorted(SUPPORTED_LANGUAGES), "max_audio_seconds": MAX_AUDIO_SECONDS}


def _audio_format(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.casefold()
    signatures = {
        ".wav": data.startswith((b"RIFF", b"RF64")) and len(data) >= 12 and data[8:12] == b"WAVE",
        ".mp3": data.startswith(b"ID3") or _find_mpeg_frame(data, 0) is not None,
        ".ogg": data.startswith(b"OggS"),
        ".flac": data.startswith(b"fLaC"),
        ".aac": _is_adts(data),
        ".m4a": len(data) >= 12 and data[4:8] == b"ftyp",
    }
    if ext not in signatures:
        raise AppError("Use a WAV, MP3, OGG, FLAC, AAC, or M4A audio file.")
    if not signatures[ext]:
        raise AppError(f"The file contents do not match the .{ext[1:]} extension.")
    return ext[1:]


def _find_mpeg_frame(data: bytes, start: int) -> tuple[int, float, int, int] | None:
    mpeg1_rates = {
        3: (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 0),
        2: (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0),
        1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0),
    }
    mpeg2_rates = {
        3: (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0),
        2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0),
        1: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0),
    }
    for pos in range(max(0, start), max(0, len(data) - 3)):
        h = int.from_bytes(data[pos:pos + 4], "big")
        version = (h >> 19) & 3
        layer = (h >> 17) & 3
        bitrate_index = (h >> 12) & 15
        sample_index = (h >> 10) & 3
        if (h >> 21) != 0x7FF or version == 1 or layer == 0 or bitrate_index in (0, 15) or sample_index == 3:
            continue
        sample_rate = (44100, 48000, 32000)[sample_index]
        if version == 2:
            sample_rate //= 2
        elif version == 0:
            sample_rate //= 4
        rates = mpeg1_rates if version == 3 else mpeg2_rates
        bitrate = rates[layer][bitrate_index] * 1000
        padding = (h >> 9) & 1
        if layer == 3:
            frame_length = ((12 * bitrate // sample_rate) + padding) * 4
            samples = 384
        else:
            coefficient = 144 if version == 3 or layer == 2 else 72
            frame_length = coefficient * bitrate // sample_rate + padding
            samples = 1152 if version == 3 or layer == 2 else 576
        if frame_length >= 4 and pos + frame_length <= len(data):
            return pos, sample_rate, samples, frame_length
    return None


def _mp3_duration(data: bytes) -> float:
    offset = 0
    if data.startswith(b"ID3") and len(data) >= 10:
        size_bytes = data[6:10]
        tag_size = ((size_bytes[0] & 0x7F) << 21) | ((size_bytes[1] & 0x7F) << 14) | ((size_bytes[2] & 0x7F) << 7) | (size_bytes[3] & 0x7F)
        offset = 10 + tag_size + (10 if data[5] & 0x10 else 0)
    frames = samples = 0
    while offset + 4 <= len(data):
        frame = _find_mpeg_frame(data, offset)
        if frame is None:
            break
        pos, sample_rate, sample_count, length = frame
        if pos > offset + 4096:
            break
        if frames and sample_rate != samples_rate:
            break
        samples_rate = sample_rate
        frames += 1
        samples += sample_count
        offset = pos + length
    if not frames or not samples_rate:
        raise AppError("This MP3 has no readable audio frames; choose a valid audio file.")
    return samples / samples_rate


def _is_adts(data: bytes) -> bool:
    return len(data) >= 7 and data[0] == 0xFF and (data[1] & 0xF6) == 0xF0


def _aac_duration(data: bytes) -> float:
    sample_rates = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)
    offset = frames = samples = 0
    rate = None
    while offset + 7 <= len(data):
        if not _is_adts(data[offset:offset + 7]):
            offset += 1
            continue
        freq_index = (data[offset + 2] >> 2) & 15
        if freq_index >= len(sample_rates):
            raise AppError("This AAC file has an invalid sample-rate header.")
        frame_len = ((data[offset + 3] & 3) << 11) | (data[offset + 4] << 3) | (data[offset + 5] >> 5)
        if frame_len < 7 or offset + frame_len > len(data):
            raise AppError("This AAC file has an incomplete audio frame.")
        frame_rate = sample_rates[freq_index]
        if rate is not None and rate != frame_rate:
            raise AppError("This AAC file changes sample rate; duration cannot be verified safely.")
        rate = frame_rate
        frames += 1
        samples += 1024 * ((data[offset + 6] & 3) + 1)
        offset += frame_len
    if not frames or not rate:
        raise AppError("This AAC file has no readable audio frames; choose a valid audio file.")
    return samples / rate


def _flac_duration(data: bytes) -> float:
    if len(data) < 42 or (data[4] & 0x7F) != 0 or int.from_bytes(data[5:8], "big") != 34:
        raise AppError("This FLAC file is missing readable STREAMINFO duration metadata.")
    packed = int.from_bytes(data[18:26], "big")
    sample_rate = packed >> 44
    total_samples = packed & ((1 << 36) - 1)
    if not sample_rate or not total_samples:
        raise AppError("This FLAC file does not contain a verifiable duration.")
    return total_samples / sample_rate


def _ogg_duration(data: bytes) -> float:
    offset = 0
    last_granule = None
    codec_rate = None
    first_payload = bytearray()
    while offset + 27 <= len(data) and data[offset:offset + 4] == b"OggS":
        if data[offset + 4] != 0:
            raise AppError("This OGG file uses an unsupported page version.")
        segment_count = data[offset + 26]
        table_start = offset + 27
        table_end = table_start + segment_count
        if table_end > len(data):
            break
        lacing = data[table_start:table_end]
        payload_length = sum(lacing)
        page_end = table_end + payload_length
        if page_end > len(data):
            break
        granule = int.from_bytes(data[offset + 6:offset + 14], "little")
        if granule != 0xFFFFFFFFFFFFFFFF:
            last_granule = granule
        if len(first_payload) < 256:
            first_payload.extend(data[table_end:min(page_end, table_end + 256 - len(first_payload))])
            if b"OpusHead" in first_payload:
                codec_rate = 48000
            elif len(first_payload) >= 16 and first_payload[1:7] == b"vorbis":
                codec_rate = int.from_bytes(first_payload[12:16], "little")
        offset = page_end
    if codec_rate and last_granule and last_granule > 0:
        return last_granule / codec_rate
    raise AppError("This OGG file has no readable codec and duration metadata.")


def _m4a_duration(data: bytes) -> float:
    containers = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta", b"mvex"}

    def walk(start: int, end: int, depth: int = 0) -> float | None:
        if depth > 12:
            return None
        pos = start
        while pos + 8 <= end:
            size = int.from_bytes(data[pos:pos + 4], "big")
            kind = data[pos + 4:pos + 8]
            header = 8
            if size == 1:
                if pos + 16 > end:
                    break
                size = int.from_bytes(data[pos + 8:pos + 16], "big")
                header = 16
            elif size == 0:
                size = end - pos
            if size < header or pos + size > end:
                break
            content_start = pos + header
            content_end = pos + size
            if kind == b"mvhd" and content_start + 20 <= content_end:
                version = data[content_start]
                if version == 0 and content_start + 20 <= content_end:
                    timescale = int.from_bytes(data[content_start + 12:content_start + 16], "big")
                    duration = int.from_bytes(data[content_start + 16:content_start + 20], "big")
                elif version == 1 and content_start + 32 <= content_end:
                    timescale = int.from_bytes(data[content_start + 20:content_start + 24], "big")
                    duration = int.from_bytes(data[content_start + 24:content_start + 32], "big")
                else:
                    timescale = duration = 0
                if timescale and duration:
                    return duration / timescale
            if kind in containers:
                nested_start = content_start + (4 if kind == b"meta" else 0)
                found = walk(nested_start, content_end, depth + 1)
                if found is not None:
                    return found
            pos += size
        return None

    duration = walk(0, len(data))
    if duration is None:
        raise AppError("This M4A file has no readable movie-duration metadata.")
    return duration


def validate_audio_upload(filename: str, data: bytes) -> dict:
    if not filename:
        raise AppError("Choose an audio file to transcribe.")
    if not isinstance(data, bytes) or not data:
        raise AppError("The selected audio file is empty.")
    if len(data) > MAX_AUDIO_BYTES:
        raise AppError("Audio file is too large. Keep it under 24 MB.")
    audio_format = _audio_format(filename, data)
    try:
        if audio_format == "wav":
            with wave.open(BytesIO(data), "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                frames = wav_file.getnframes()
                if frame_rate <= 0 or wav_file.getnchannels() <= 0:
                    raise AppError("This WAV file has invalid audio metadata.")
                duration = frames / frame_rate
        elif audio_format == "mp3":
            duration = _mp3_duration(data)
        elif audio_format == "aac":
            duration = _aac_duration(data)
        elif audio_format == "flac":
            duration = _flac_duration(data)
        elif audio_format == "ogg":
            duration = _ogg_duration(data)
        else:
            duration = _m4a_duration(data)
    except (wave.Error, EOFError, struct.error, IndexError) as exc:
        raise AppError("The audio file is malformed or its duration cannot be verified.") from exc
    if duration <= 0:
        raise AppError("The audio file has no playable duration.")
    if duration > MAX_AUDIO_SECONDS:
        raise AppError(f"Prisma REST accepts clips up to {MAX_AUDIO_SECONDS} seconds. Trim this clip and try again.")
    media_type = mimetypes.guess_type(filename)[0] or {
        "wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "flac": "audio/flac", "aac": "audio/aac", "m4a": "audio/mp4",
    }[audio_format]
    return {"format": audio_format, "duration_seconds": duration, "content_type": media_type}


def _multipart_form(filename: str, audio: bytes, content_type: str, fields: dict[str, str]) -> tuple[str, bytes]:
    boundary = "bolidesk-" + uuid.uuid4().hex
    safe_name = Path(filename).name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    chunks = [
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio_file\"; filename=\"{safe_name}\"\r\nContent-Type: {content_type}\r\n\r\n").encode("utf-8"),
        audio,
        b"\r\n",
    ]
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


def parse_multipart(body: bytes, content_type: str) -> dict[str, tuple[str, bytes, str]]:
    envelope = b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body
    message = BytesParser(policy=policy.default).parsebytes(envelope)
    if not message.is_multipart():
        raise AppError("Expected a multipart audio upload.")
    fields = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            fields[name] = (part.get_filename() or "", part.get_payload(decode=True) or b"", part.get_content_type())
    return fields


def _api_key(explicit_key: str | None = None) -> str:
    key = (explicit_key or os.environ.get("GNANI_API_KEY", "")).strip()
    if not key:
        raise AppError("Gnani API key is not configured. Add GNANI_API_KEY to .env and restart BoliDesk.", 503)
    return key


def _upstream_json(request: Request) -> dict:
    if not _GNANI_REQUEST_LOCK.acquire(blocking=False):
        raise AppError("A Gnani request is already in progress. Wait for it to finish, then retry.", 429)
    try:
        with urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except HTTPError as exc:
        raise _gnani_http_error(exc) from None
    except (URLError, TimeoutError, OSError) as exc:
        if isinstance(exc, (TimeoutError, socket.timeout)) or (isinstance(exc, URLError) and isinstance(exc.reason, (TimeoutError, socket.timeout))):
            raise AppError("Gnani did not respond in time. Try again shortly.", 504) from None
        raise AppError("Could not reach Gnani AI. Check the connection and try again.", 502) from None
    finally:
        _GNANI_REQUEST_LOCK.release()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AppError("Gnani returned an unreadable response. Try again shortly.", 502) from None
    if not isinstance(payload, dict):
        raise AppError("Gnani returned an unexpected response. Try again shortly.", 502)
    return payload


def _gnani_http_error(exc: HTTPError) -> AppError:
    raw = exc.read(16_384).decode("utf-8", "replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    detail = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message", ""))
        elif isinstance(error, str):
            detail = error
        extra = payload.get("detail")
        if isinstance(extra, dict):
            detail = detail or str(extra.get("message", ""))
        detail = detail or str(payload.get("message", ""))
    detail = re.sub(r"\s+", " ", detail).strip()[:400]
    defaults = {
        400: "Gnani could not process this request. Check the file, language, and input.",
        401: "Gnani rejected the API key. Check GNANI_API_KEY in .env.",
        403: "Gnani denied this request. Check API access and available credits.",
        429: "Gnani rate limit reached. Wait a moment and retry.",
        503: "Gnani's speech service is temporarily unavailable. Try again shortly.",
        504: "Gnani did not respond in time. Try again shortly.",
    }
    return AppError(detail or defaults.get(exc.code, f"Gnani returned HTTP {exc.code}."), 502)


def transcribe_audio(filename: str, audio: bytes, language: str, api_key: str | None = None) -> dict:
    key = _api_key(api_key)
    if language not in SUPPORTED_LANGUAGES:
        raise AppError("Choose one of the supported Indian-language options.")
    info = validate_audio_upload(filename, audio)
    content_type, body = _multipart_form(filename, audio, info["content_type"], {"language_code": language, "format": "transcribe"})
    request = Request(API_BASE + "/stt/v3", data=body, headers={"X-API-Key-ID": key, "Content-Type": content_type, "Accept": "application/json"}, method="POST")
    payload = _upstream_json(request)
    transcript = payload.get("transcript")
    if payload.get("success") is not True or not isinstance(transcript, str) or not transcript.strip():
        error = payload.get("error")
        message = error.get("message", "") if isinstance(error, dict) else ""
        raise AppError(str(message)[:400] or "Gnani did not return a transcript. Try another clip.", 502)
    return {"transcript": transcript, "request_id": payload.get("request_id"), "model": payload.get("model", "gnani-prisma-v2.5"), "audio_duration_seconds": info["duration_seconds"]}


def synthesize_speech(text: str, language: str, api_key: str | None = None) -> dict:
    key = _api_key(api_key)
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        raise AppError("There is no transcript text to read aloud.")
    if len(text) > MAX_TTS_CHARS:
        raise AppError(f"Timbre playback is limited to {MAX_TTS_CHARS:,} characters at a time.")
    if language not in SUPPORTED_LANGUAGES:
        raise AppError("Choose one of the supported Indian-language options.")
    voice = TTS_VOICES[language]
    payload = {
        "text": text, "model": "timbre-v2.5", "voice": voice, "language": language, "speed": 1.0,
        "audio_config": {"encoding": "linear_pcm", "container": "wav", "num_channels": 1, "sample_rate": 48000, "sample_width": 2},
    }
    request = Request(API_BASE + "/api/v1/tts/inference", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"X-API-Key-ID": key, "Content-Type": "application/json", "Accept": "audio/wav"}, method="POST")
    if not _GNANI_REQUEST_LOCK.acquire(blocking=False):
        raise AppError("A Gnani request is already in progress. Wait for it to finish, then retry.", 429)
    try:
        try:
            with urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
                audio = response.read()
                content_type = response.headers.get("Content-Type", "audio/wav").split(";", 1)[0].strip().lower()
        except HTTPError as exc:
            raise _gnani_http_error(exc) from None
        except (URLError, TimeoutError, OSError) as exc:
            if isinstance(exc, (TimeoutError, socket.timeout)) or (isinstance(exc, URLError) and isinstance(exc.reason, (TimeoutError, socket.timeout))):
                raise AppError("Timbre did not respond in time. Try again shortly.", 504) from None
            raise AppError("Could not reach Gnani Timbre. Check the connection and try again.", 502) from None
    finally:
        _GNANI_REQUEST_LOCK.release()
    if not audio or not content_type.startswith("audio/"):
        raise AppError("Timbre returned an unexpected audio response. Playback is unavailable right now.", 502)
    return {"audio_base64": base64.b64encode(audio).decode("ascii"), "content_type": content_type, "model": "timbre-v2.5", "voice": voice}


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    found = []
    for term in terms:
        if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, flags=re.IGNORECASE):
            found.append(term)
    return found


def _extract_request(text: str) -> str:
    cues = ("please", "can you", "could you", "would you", "i want", "i need", "i'd like", "i would like", "tell me", "मुझे", "कृपया", "बताइए", "बताएं", "चाहिए")
    sentences = re.split(r"(?<=[.!?।])\s+", text.strip())
    explicit = [sentence.strip() for sentence in sentences if any(cue in sentence.casefold() for cue in cues)]
    return " ".join(explicit)


def _extract_reported_outcome(category: str, text: str) -> str:
    outcome_patterns = {
        "payments": (r"\brefund\b.{0,40}\b(?:issued|processed|completed|credited|received)\b", r"\b(?:received|got) the refund\b", r"\bamount was refunded\b"),
        "delivery": (r"\b(?:parcel|package|order)\b.{0,30}\b(?:arrived|delivered|received)\b", r"\bdelivery (?:was )?completed\b"),
        "account_access": (r"\b(?:can|am able to) log in\b", r"\baccess (?:was )?restored\b"),
        "product_issue": (r"\b(?:device|product|it)\b.{0,25}\b(?:works|working) (?:again|now)\b", r"\bissue (?:is )?resolved\b"),
        "general": (),
    }
    patterns = outcome_patterns.get(category, ())
    sentences = re.split(r"(?<=[.!?।])\s+", text.strip())
    for sentence in sentences:
        if re.search(r"\b(?:not|never|no|without|can't|cannot|didn't|did not|hasn't|has not|haven't|have not|isn't|is not|wasn't|was not)\b|नहीं|मत", sentence, re.IGNORECASE):
            continue
        if any(re.search(pattern, sentence, re.IGNORECASE) for pattern in patterns):
            return sentence.strip()
    return ""


def _extract_match(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return ""


def _derive_flags(transcript: str, source: str, candidates: list[str]) -> list[dict]:
    flags = []

    def add(flag_id: str, title: str, reason: str, evidence: list[str], field: str = "") -> None:
        flags.append({"id": flag_id, "title": title, "reason": reason, "evidence": evidence, "field": field, "status": "open"})

    if source == "prisma":
        add("transcript_review", "Compare transcript with audio", "The API response does not provide a verified transcription-confidence value. Review names, numbers, dates, negation, and any words that change the case before proceeding.", [], "transcript")
    if len(candidates) == 1:
        category_name = CATEGORY_CONFIG[candidates[0]]["label"]
        terms = _matched_terms(transcript, CATEGORY_CONFIG[candidates[0]]["terms"])
        reason = "A local keyword rule suggested " + category_name + " from the matched term(s). This is a routing hint, not a verified label."
        add("category_ambiguity", "Confirm issue category", reason, terms, "category")
    else:
        labels = [CATEGORY_CONFIG[key]["label"] for key in candidates]
        reason = "No supported category keyword matched; select a category manually." if not candidates else "Keywords matched more than one supported category: " + ", ".join(labels) + ". Select the best fit manually."
        add("category_ambiguity", "Confirm issue category", reason, [], "category")

    negation_pattern = re.compile(r"\b(?:not|no|never|without|cannot|can't|didn't|did not|doesn't|does not|hasn't|has not|haven't|have not|isn't|is not|wasn't|was not|won't|will not|don't|do not)\b|नहीं|मत", re.IGNORECASE)
    negations = list(dict.fromkeys(match.group(0) for match in negation_pattern.finditer(transcript)))
    if negations:
        add("negation", "Review negation wording", "Negation changes whether a problem is unresolved or already fixed. Compare the highlighted words with the audio; this is a review cue, not a claim of error.", negations, "reported_problem")

    date_value = _extract_match(transcript, (
        r"\b\d{4}-\d{1,2}-\d{1,2}\b", r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,?\s+\d{4})?\b",
    ))
    if date_value:
        add("date_confirmation", "Confirm reported date", "This date was copied from the transcript, not checked against a payment or business record.", [date_value], "payment_date")
    amount_value = _extract_match(transcript, (r"(?:₹|INR\s*|Rs\.?\s*)\s*\d[\d,]*(?:\.\d{1,2})?", r"\b\d[\d,]*(?:\.\d{1,2})?\s*(?:rupees|INR)\b"))
    if amount_value:
        add("amount_confirmation", "Confirm reported amount", "This amount is customer-reported and has not been checked against a payment record.", [amount_value], "amount")
    reference_value = _extract_match(transcript, (
        r"\b(?:transaction|txn|payment|order|reference|ref)\s*(?:id|number|no\.?)\s*(?:is\s*)?(?:[:#-]\s*)?[A-Z0-9][A-Z0-9-]{2,}\b",
        r"\b(?:transaction|order)\s*#\s*[A-Z0-9][A-Z0-9-]{2,}\b",
    ))
    if reference_value:
        add("reference_confirmation", "Confirm reported reference", "This identifier was copied from the transcript; BoliDesk has not looked it up in an external system.", [reference_value], "transaction_reference")

    contradictions = (
        ("refund_contradiction", "Check refund statements", (r"\b(?:refund|refunded)\b.{0,35}\b(?:issued|processed|completed|credited|received)\b", r"\b(?:not refunded|never refunded|hasn't been refunded|has not been refunded|haven't received the refund|have not received the refund|didn't receive the refund|did not receive the refund)\b")),
        ("delivery_contradiction", "Check delivery statements", (r"\b(?:delivered|delivery completed|parcel received)\b", r"\b(?:not delivered|never delivered|hasn't arrived|has not arrived|never arrived)\b")),
    )
    for flag_id, title, patterns in contradictions:
        matches = [match.group(0) for pattern in patterns if (match := re.search(pattern, transcript, re.IGNORECASE))]
        if len(matches) > 1:
            add(flag_id, title, "The transcript contains statements that may conflict. Review both excerpts with the audio; neither statement is treated as verified.", matches, "reported_problem")
    return flags


def _classify(transcript: str) -> tuple[str, list[str], dict[str, list[str]]]:
    evidence = {key: _matched_terms(transcript, config["terms"]) for key, config in CATEGORY_CONFIG.items() if key != "general"}
    candidates = [key for key, terms in evidence.items() if terms]
    category = candidates[0] if len(candidates) == 1 else "general"
    return category, candidates, evidence


def build_case(transcript: str, source: str = "prisma", original_transcript: str | None = None) -> dict:
    if not isinstance(transcript, str) or not transcript.strip():
        raise AppError("Add a transcript before creating a case draft.")
    transcript = transcript.strip()
    if len(transcript) > 12000:
        raise AppError("Transcript is too long. Keep it under 12,000 characters.")
    if source not in {"prisma", "synthetic", "manual"}:
        source = "manual"
    category, candidates, evidence = _classify(transcript)
    config = CATEGORY_CONFIG[category]
    request_text = _extract_request(transcript)
    reported_outcome = _extract_reported_outcome(category, transcript)
    payment_date = _extract_match(transcript, (
        r"\b\d{4}-\d{1,2}-\d{1,2}\b", r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,?\s+\d{4})?\b",
    ))
    transaction_reference = _extract_match(transcript, (
        r"\b(?:transaction|txn|payment|reference|ref)\s*(?:id|number|no\.?)\s*(?:is\s*)?(?:[:#-]\s*)?[A-Z0-9][A-Z0-9-]{2,}\b",
        r"\b(?:transaction|payment)\s*#\s*[A-Z0-9][A-Z0-9-]{2,}\b",
    ))
    order_reference = _extract_match(transcript, (
        r"\b(?:order)\s*(?:id|number|no\.?)\s*(?:is\s*)?(?:[:#-]\s*)?[A-Z0-9][A-Z0-9-]{2,}\b",
        r"\border\s*#\s*[A-Z0-9][A-Z0-9-]{2,}\b",
    ))
    amount = _extract_match(transcript, (r"(?:₹|INR\s*|Rs\.?\s*)\s*\d[\d,]*(?:\.\d{1,2})?", r"\b\d[\d,]*(?:\.\d{1,2})?\s*(?:rupees|INR)\b"))
    title = f"{config['label']} — customer report" if len(candidates) == 1 else "Customer support case — confirm category"
    fields = {
        "title": title,
        "reported_problem": transcript,
        "requested_outcome": request_text,
        "actual_outcome": reported_outcome,
        "payment_date": payment_date,
        "transaction_reference": transaction_reference,
        "amount": amount,
        "order_reference": order_reference,
        "affected_service": "",
        "product_name": "",
        "proposed_queue": config["queue"] if len(candidates) == 1 else "",
        "next_action": config["action"],
    }
    field_sources = {field: "local_rule" for field in ("title", "proposed_queue", "next_action")}
    field_sources.update({"reported_problem": "transcript", "actual_outcome": "transcript" if reported_outcome else "missing"})
    field_sources["requested_outcome"] = "transcript" if request_text else "missing"
    for field in ("payment_date", "transaction_reference", "amount", "order_reference"):
        field_sources[field] = "transcript" if fields[field] else "missing"
    field_sources["affected_service"] = "missing"
    field_sources["product_name"] = "missing"
    case = {
        "schema_version": CASE_SCHEMA_VERSION,
        "transcript": {
            "original": original_transcript if original_transcript is not None else transcript,
            "edited": transcript,
            "last_drafted_from": transcript,
            "source": source,
        },
        "category": category,
        "category_candidates": candidates,
        "category_evidence": evidence,
        "category_confirmed": False,
        "fields": fields,
        "field_sources": field_sources,
        "review": {
            "status": "draft", "approved_at": None, "approval_fingerprint": None,
            "routing_confirmed": False, "action_confirmed": False,
            "flags": _derive_flags(transcript, source, candidates),
            "follow_up_questions": [],
        },
    }
    validation = validate_case(case)
    case["review"]["follow_up_questions"] = validation["suggested_questions"]
    return case


def _present(fields: dict, field: str) -> bool:
    return bool(str(fields.get(field, "")).strip())


def validate_case(case: dict) -> dict:
    if not isinstance(case, dict) or case.get("schema_version") != CASE_SCHEMA_VERSION:
        raise AppError("This case draft is not in the supported BoliDesk format.")
    category = case.get("category")
    if category not in CATEGORY_CONFIG:
        raise AppError("Select a supported case category.")
    config = CATEGORY_CONFIG[category]
    fields = case.get("fields") if isinstance(case.get("fields"), dict) else {}
    review = case.get("review") if isinstance(case.get("review"), dict) else {}
    missing_fields = []
    review_steps = []
    for group in config["required_groups"]:
        group_fields = group["fields"]
        if not any(_present(fields, field) for field in group_fields):
            missing_fields.append({"id": group["id"], "label": group["label"], "fields": list(group_fields)})
    if not bool(case.get("category_confirmed")):
        review_steps.append({"id": "category_confirmation", "label": "Confirm the issue category", "fields": []})
    if not _present(fields, "proposed_queue") or not bool(review.get("routing_confirmed")):
        review_steps.append({"id": "routing_confirmation", "label": "Choose and confirm the proposed queue", "fields": ["proposed_queue"]})
    if not _present(fields, "next_action") or not bool(review.get("action_confirmed")):
        review_steps.append({"id": "action_confirmation", "label": "Review and confirm the next action", "fields": ["next_action"]})
    transcript = case.get("transcript") if isinstance(case.get("transcript"), dict) else {}
    transcript_stale = transcript.get("edited", "") != transcript.get("last_drafted_from", "")
    if transcript_stale:
        review_steps.append({"id": "refresh_case_draft", "label": "Refresh the case draft from the edited transcript", "fields": []})
    flags = review.get("flags") if isinstance(review.get("flags"), list) else []
    open_flags = [flag for flag in flags if isinstance(flag, dict) and flag.get("status", "open") not in {"confirmed", "dismissed"}]
    if open_flags:
        review_steps.append({"id": "review_flags", "label": f"Review {len(open_flags)} open evidence flag{'s' if len(open_flags) != 1 else ''}", "fields": []})
    optional_missing = [
        {"id": field, "label": FIELD_DEFINITIONS[field]["label"]}
        for field in config["optional_fields"] if not _present(fields, field)
    ]
    suggested_questions = []
    for item in missing_fields:
        template = FOLLOW_UP_TEMPLATES.get(item["id"])
        if template:
            suggested_questions.append({"id": f"question_{item['id']}", "field": item["id"], "text": template, "status": "open"})
    missing_required = missing_fields + review_steps
    return {
        "category": category,
        "missing_fields": missing_fields,
        "review_steps": review_steps,
        "missing_required": missing_required,
        "optional_missing": optional_missing,
        "open_flags": [flag.get("id") for flag in open_flags],
        "suggested_questions": suggested_questions,
        "can_approve": not missing_required,
        "is_approved": review.get("status") == "approved" and not missing_required,
        "transcript_stale": transcript_stale,
    }


def _case_fingerprint(case: dict) -> str:
    stable = copy.deepcopy(case)
    review = stable.get("review", {})
    if isinstance(review, dict):
        review.pop("status", None)
        review.pop("approved_at", None)
        review.pop("approval_fingerprint", None)
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def approve_case(case: dict) -> dict:
    approved = copy.deepcopy(case)
    validation = validate_case(approved)
    if not validation["can_approve"]:
        labels = "; ".join(item["label"] for item in validation["missing_required"])
        raise AppError("This case is not ready for approval. Resolve: " + labels, 409)
    from datetime import datetime, timezone
    review = approved["review"]
    review["status"] = "approved"
    review["approved_at"] = datetime.now(timezone.utc).isoformat()
    review["approval_fingerprint"] = _case_fingerprint(approved)
    return approved


def export_case(case: dict) -> dict:
    validation = validate_case(case)
    review = case.get("review", {})
    if review.get("status") != "approved":
        raise AppError("Approve the current case before exporting it.", 409)
    if not review.get("approval_fingerprint") or review["approval_fingerprint"] != _case_fingerprint(case):
        raise AppError("This case changed after approval. Review and approve it again before exporting.", 409)
    if not validation["can_approve"]:
        raise AppError("The approved case is no longer complete. Review it again before exporting.", 409)
    from datetime import datetime, timezone
    review_export = copy.deepcopy(review)
    review_export.pop("approval_fingerprint", None)
    return {
        "schema_version": CASE_SCHEMA_VERSION,
        "application": "BoliDesk",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "transcript": copy.deepcopy(case["transcript"]),
        "category": {"value": case["category"], "label": CATEGORY_CONFIG[case["category"]]["label"], "source": "human-confirmed", "candidates": list(case.get("category_candidates", [])), "evidence": copy.deepcopy(case.get("category_evidence", {}))},
        "fields": copy.deepcopy(case["fields"]),
        "field_sources": copy.deepcopy(case.get("field_sources", {})),
        "review": review_export,
        "validation": validation,
    }


def encode_tts_response(result: dict) -> dict:
    """Return JSON-safe audio metadata; the browser makes the playable Blob."""
    return {"audio_base64": base64.b64encode(result["audio"]).decode("ascii"), "content_type": result["content_type"], "model": "timbre-v2.5", "voice": result["voice"]}
