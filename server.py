"""BoliDesk: a small, dependency-free web app for multilingual call review."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import uuid
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
API_BASE = "https://api.vachana.ai"
MAX_REQUEST_BYTES = 24 * 1024 * 1024
MAX_TTS_CHARS = 5000
SUPPORTED_LANGUAGES = {
    "bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "pa-IN", "ta-IN", "te-IN"
}
TTS_VOICES = {
    "bn-IN": "Kirra",
    "en-IN": "Kaveri",
    "gu-IN": "Falak",
    "hi-IN": "Bhavna",
    "kn-IN": "Saanvi",
    "ml-IN": "Reshma",
    "mr-IN": "Ishaan",
    "pa-IN": "Zayan",
    "ta-IN": "Trisha",
    "te-IN": "Suhana",
}
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a"}


def load_dotenv() -> None:
    """Load simple KEY=value entries from .env without overriding process variables."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("\"'")
        if name:
            os.environ.setdefault(name, value)


def analyze_transcript(transcript: str) -> dict:
    """Return transparent, local keyword-based routing hints (no external LLM call)."""
    text = " ".join(transcript.split())
    lower = text.casefold()
    topics = [
        ("Payments & billing", ("refund", "charged", "charge", "payment", "bill", "emi", "duplicate", "रिफंड", "पैसे वापस", "चार्ज", "काटा")),
        ("Order & delivery", ("delivery", "deliver", "shipment", "parcel", "order", "tracking", "late", "delayed", "डिलीवरी", "पार्सल", "ऑर्डर")),
        ("Account access", ("login", "log in", "otp", "password", "locked", "लॉगिन", "पासवर्ड")),
        ("Product issue", ("broken", "damaged", "defective", "not working", "stopped working", "खराब")),
    ]
    category = next((name for name, words in topics if any(word in lower for word in words)), "General support")

    urgent_terms = ("urgent", "immediately", "today", "can't wait", "cannot wait", "still not", "third time", "बार बार", "तुरंत")
    priority = "Review soon" if any(word in lower for word in urgent_terms) else "Standard"
    concern_terms = ("angry", "upset", "frustrated", "disappointed", "worried", "unacceptable", "nothing has changed", "not resolved", "परेशान", "नाराज़")
    tone = "Concern signals" if any(word in lower for word in concern_terms) else "No clear signal"

    action_map = {
        "Payments & billing": ["Route to the billing or payments queue", "Verify the relevant transaction in the ledger", "Confirm the outcome and expected timeline with the customer"],
        "Order & delivery": ["Route to the order support queue", "Check the latest fulfilment and carrier events", "Share a verified delivery update or next step"],
        "Account access": ["Route to the account support queue", "Check the access or verification failure", "Offer the approved recovery flow"],
        "Product issue": ["Route to the product support queue", "Confirm the product and reported fault", "Share the approved troubleshooting or replacement path"],
        "General support": ["Review the transcript and confirm the customer's main request", "Assign the case to the closest support queue", "Record the agreed next step and follow-up owner"],
    }
    sentence = re.split(r"(?<=[.!?।])\s+", text, maxsplit=1)[0] if text else "No transcript yet"
    summary = sentence[:190] + ("…" if len(sentence) > 190 else "")
    return {
        "category": category,
        "priority": priority,
        "tone": tone,
        "summary": summary,
        "actions": action_map[category],
        "method": "Local keyword rules",
        "review_note": "Suggested routing only. A person should confirm the transcript and signals before acting.",
    }


def parse_multipart(body: bytes, content_type: str) -> dict[str, tuple[str, bytes, str]]:
    envelope = (
        b"MIME-Version: 1.0\r\nContent-Type: "
        + content_type.encode("utf-8")
        + b"\r\n\r\n"
        + body
    )
    message = BytesParser(policy=policy.default).parsebytes(envelope)
    if not message.is_multipart():
        raise ValueError("Expected a multipart form upload.")
    fields: dict[str, tuple[str, bytes, str]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename() or ""
        value = part.get_payload(decode=True) or b""
        fields[name] = (filename, value, part.get_content_type())
    return fields


def make_multipart(file_name: str, file_bytes: bytes, content_type: str, fields: dict[str, str]) -> tuple[str, bytes]:
    boundary = "bolidesk-" + uuid.uuid4().hex
    safe_name = Path(file_name).name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    chunks: list[bytes] = []
    chunks.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio_file\"; filename=\"{safe_name}\"\r\n"
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
    )
    chunks.append(file_bytes)
    chunks.append(b"\r\n")
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


class Handler(BaseHTTPRequestHandler):
    server_version = "BoliDesk/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Do not write transcripts, file names, API keys, or request bodies to logs.
        super().log_message(fmt, *args)

    def send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length.") from exc
        if length <= 0:
            raise ValueError("Request body is empty.")
        if length > MAX_REQUEST_BYTES:
            raise ValueError("Upload is too large. Keep audio files under 24 MB.")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("The request upload ended unexpectedly.")
        return body

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self.send_json({"configured": bool(os.environ.get("GNANI_API_KEY", "").strip()), "app": "BoliDesk", "prisma": "Prisma v2.5", "timbre": "Timbre v2.5"})
            return
        path_part = self.path.split("?", 1)[0]
        relative = "index.html" if path_part == "/" else path_part.lstrip("/")
        file_path = (PUBLIC / relative).resolve()
        try:
            file_path.relative_to(PUBLIC.resolve())
        except ValueError:
            self.send_error(404)
            return
        if not file_path.is_file():
            self.send_error(404)
            return
        content = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/analyze":
                data = json.loads(self.read_body().decode("utf-8"))
                transcript = str(data.get("transcript", "")).strip()
                if not transcript:
                    self.send_json({"error": "Add a transcript before creating a case brief."}, 400)
                    return
                if len(transcript) > 12000:
                    self.send_json({"error": "Transcript is too long. Keep it under 12,000 characters."}, 400)
                    return
                self.send_json({"analysis": analyze_transcript(transcript)})
                return
            if self.path == "/api/transcribe":
                self.transcribe()
                return
            if self.path == "/api/speak":
                self.speak()
                return
            self.send_json({"error": "Endpoint not found."}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except (URLError, TimeoutError) as exc:
            self.send_json({"error": f"Could not reach Gnani AI. Check your connection and try again. ({exc})"}, 502)
        except Exception as exc:
            self.send_json({"error": f"The request could not be completed: {exc}"}, 500)

    def api_key(self) -> str:
        key = os.environ.get("GNANI_API_KEY", "").strip()
        if not key:
            raise ValueError("Gnani API key not configured. Copy .env.example to .env and add your key, then restart BoliDesk.")
        return key

    def upstream_error(self, exc: HTTPError) -> None:
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
            detail = payload.get("error", {}).get("message") or payload.get("message") or ""
        except (json.JSONDecodeError, AttributeError):
            detail = ""
        default = {
            400: "Gnani could not process this request. Check the audio format, duration, and language.",
            401: "Gnani rejected the API key. Check GNANI_API_KEY in .env.",
            403: "Gnani denied this request. Check API access and available credits.",
            429: "Gnani rate limit reached. Wait a moment and retry.",
            503: "Gnani's speech service is temporarily unavailable. Try again shortly.",
        }.get(exc.code, f"Gnani returned HTTP {exc.code}.")
        self.send_json({"error": detail or default, "upstream_status": exc.code}, 502)

    def transcribe(self) -> None:
        key = self.api_key()
        fields = parse_multipart(self.read_body(), self.headers.get("Content-Type", ""))
        file_name, audio, detected_type = fields.get("audio_file", ("", b"", ""))
        language = fields.get("language_code", ("", b"", ""))[1].decode("utf-8", "replace").strip()
        extension = Path(file_name).suffix.casefold()
        if not file_name or not audio:
            raise ValueError("Choose an audio file to transcribe.")
        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError("Use a WAV, MP3, OGG, FLAC, AAC, or M4A audio file.")
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError("Choose one of the supported Indian language options.")
        content_type = detected_type if detected_type != "application/octet-stream" else (mimetypes.guess_type(file_name)[0] or "audio/wav")
        form_type, body = make_multipart(file_name, audio, content_type, {"language_code": language, "format": "transcribe"})
        request = Request(
            API_BASE + "/stt/v3",
            data=body,
            headers={"X-API-Key-ID": key, "Content-Type": form_type, "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            self.upstream_error(exc)
            return
        transcript = payload.get("transcript", "")
        if not payload.get("success", True) or not transcript:
            message = payload.get("error", {}).get("message", "No transcript was returned. Try another clip.")
            self.send_json({"error": message}, 502)
            return
        self.send_json({"transcript": transcript, "request_id": payload.get("request_id"), "model": payload.get("model", "Gnani Prisma v2.5")})

    def speak(self) -> None:
        key = self.api_key()
        data = json.loads(self.read_body().decode("utf-8"))
        text = str(data.get("text", "")).strip()
        language = str(data.get("language", "en-IN")).strip()
        if not text:
            raise ValueError("There is no transcript text to read aloud.")
        if len(text) > MAX_TTS_CHARS:
            raise ValueError("Timbre playback is limited to 5,000 characters at a time.")
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError("Choose a supported language before requesting speech.")
        payload = {
            "text": text,
            "model": "timbre-v2.5",
            "voice": TTS_VOICES[language],
            "language": language,
            "speed": 1.0,
            "audio_config": {"encoding": "linear_pcm", "container": "wav", "num_channels": 1, "sample_rate": 48000, "sample_width": 2},
        }
        request = Request(
            API_BASE + "/api/v1/tts/inference",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"X-API-Key-ID": key, "Content-Type": "application/json", "Accept": "audio/wav"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:
                audio = response.read()
                content_type = response.headers.get("Content-Type", "audio/wav")
        except HTTPError as exc:
            self.upstream_error(exc)
            return
        self.send_json({"audio_base64": base64.b64encode(audio).decode("ascii"), "content_type": content_type, "model": "Gnani Timbre v2.5", "voice": TTS_VOICES[language]})


def main() -> None:
    load_dotenv()
    host = os.environ.get("BOLIDESK_HOST", "127.0.0.1")
    port = int(os.environ.get("BOLIDESK_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"BoliDesk is ready at http://{host}:{port}")
    print("API key configured" if os.environ.get("GNANI_API_KEY", "").strip() else "API key missing: add GNANI_API_KEY to .env for live Prisma and Timbre calls")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping BoliDesk...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
