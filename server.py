"""BoliDesk: a small, dependency-free web app for multilingual call review."""

from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from workflow import (
    MAX_AUDIO_BYTES,
    AppError,
    approve_case,
    build_case,
    case_metadata,
    encode_tts_response,
    export_case,
    parse_multipart,
    synthesize_speech,
    transcribe_audio,
    validate_case,
)


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
MAX_REQUEST_BYTES = MAX_AUDIO_BYTES + 256 * 1024


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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self, max_bytes: int = 1024 * 1024) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise AppError("Invalid request length.") from exc
        if length <= 0:
            raise AppError("Request body is empty.")
        if length > max_bytes:
            raise AppError("Request is too large. Keep audio uploads under 24 MB.", 413)
        body = self.rfile.read(length)
        if len(body) != length:
            raise AppError("The request ended before the complete body arrived.")
        return body

    def read_json(self) -> dict:
        try:
            data = json.loads(self.read_body().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AppError("Request must contain valid JSON.") from None
        if not isinstance(data, dict):
            raise AppError("Request JSON must be an object.")
        return data

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self.send_json({"configured": bool(os.environ.get("GNANI_API_KEY", "").strip()), "app": "BoliDesk", "prisma": "Prisma v2.5", "timbre": "Timbre v2.5"})
            return
        if self.path == "/api/meta":
            self.send_json(case_metadata())
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
                data = self.read_json()
                case = build_case(data.get("transcript", ""), data.get("source", "manual"), data.get("original_transcript"))
                self.send_json({"case": case, "validation": validate_case(case)})
                return
            if self.path == "/api/validate":
                data = self.read_json()
                self.send_json({"validation": validate_case(data.get("case"))})
                return
            if self.path == "/api/approve":
                data = self.read_json()
                case = approve_case(data.get("case"))
                self.send_json({"case": case, "validation": validate_case(case)})
                return
            if self.path == "/api/export":
                data = self.read_json()
                self.send_json({"export": export_case(data.get("case"))})
                return
            if self.path == "/api/transcribe":
                self.transcribe()
                return
            if self.path == "/api/speak":
                self.speak()
                return
            self.send_json({"error": "Endpoint not found."}, 404)
        except AppError as exc:
            self.send_json({"error": exc.message}, exc.status)
        except BrokenPipeError:
            return
        except Exception as exc:
            # Request content and secrets are intentionally excluded from diagnostics.
            self.log_error("Request failed (%s)", type(exc).__name__)
            self.send_json({"error": "The request could not be completed. Check the input and try again."}, 500)

    def transcribe(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data;"):
            raise AppError("Upload audio using a multipart form.")
        fields = parse_multipart(self.read_body(MAX_REQUEST_BYTES), content_type)
        file_name, audio, detected_type = fields.get("audio_file", ("", b"", ""))
        language = fields.get("language_code", ("", b"", ""))[1].decode("utf-8", "replace").strip()
        result = transcribe_audio(file_name, audio, language)
        self.send_json(result)

    def speak(self) -> None:
        data = self.read_json()
        result = synthesize_speech(data.get("text", ""), data.get("language", "en-IN"))
        self.send_json(encode_tts_response(result))


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
