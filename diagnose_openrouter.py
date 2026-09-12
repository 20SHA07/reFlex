"""Check connectivity, authentication and simple inference, then run the live demo.

Place beside agents.py and demo_agents.py, then run:
    py diagnose_openrouter.py

Uses the same OPENROUTER_API_KEY / OPENROUTER_MODEL environment variables.
Does not dump request payloads, request headers or full provider bodies. Selected error
messages may quote validation context; this launcher uses synthetic demo data.
The extra simple model check uses up to 32 output tokens and may incur a small
API charge. The existing agent demo's request parameters and approvals are unchanged.
"""
from __future__ import annotations

import json
import gzip
import io
import os
import platform
import re
import sys
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.request import Request, getproxies

import agents
import demo_agents


MAX_ERROR_BYTES = 65536


def redact(value: object, secret: str) -> str:
    text = str(value)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"xox[baprs]-[A-Za-z0-9-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)\b(https?://)[^/\s<>]*@", r"\1[REDACTED]@", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"',;]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)(\b(?:api[_-]?key|authorization|access_token)\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]", text,
    )
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    return text[:1600]


def _message(container: object) -> str | None:
    if not isinstance(container, dict):
        return None
    error = container.get("error", container)
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return None


class _PageText(HTMLParser):
    """Extract visible error-page text without scripts, styles or comments."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def error_details(body: bytes, secret: str, content_encoding: str = "") -> list[str]:
    """Select JSON fields or a short redacted excerpt from an error page."""
    if len(body) > MAX_ERROR_BYTES:
        return ["Error body exceeded the diagnostic limit; body omitted."]
    if not body:
        return ["Server returned an empty error body."]
    if content_encoding.lower().strip() == "gzip" or body.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                body = stream.read(MAX_ERROR_BYTES + 1)
        except (OSError, EOFError):
            return ["Server returned an unreadable gzip error body."]
        if len(body) > MAX_ERROR_BYTES:
            return ["Decompressed error exceeded the diagnostic limit; body omitted."]
    try:
        # Passing bytes lets json recognize UTF-8/UTF-16 BOMs too.
        data = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        text = body.decode("utf-8-sig", errors="replace")
        if "<html" in text.lower() or "<!doctype" in text.lower():
            parser = _PageText()
            parser.feed(text)
            text = " ".join(parser.parts)
        excerpt = redact(text, secret)[:600]
        return ["Server returned a non-JSON error.",
                "Error text excerpt: " + (excerpt or "(no readable text)")]
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return ["No structured error details were returned."]
    lines = []
    if isinstance(error.get("message"), str):
        lines.append("OpenRouter message: " + redact(error["message"], secret))
    metadata = error.get("metadata")
    if isinstance(metadata, dict):
        for field in ("provider_name", "error_type", "provider_code"):
            value = metadata.get(field)
            if isinstance(value, (str, int)):
                lines.append(field + ": " + redact(value, secret))
        # Some providers put a JSON-encoded error in metadata.raw. Extract only
        # its message, not echoed headers, prompts, completions or request data.
        raw = metadata.get("raw")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, RecursionError):
                raw = None
        message = _message(raw)
        if message:
            lines.append("Provider message: " + redact(message, secret))
    return lines or ["No structured error details were returned."]


def show_headers(headers, secret: str) -> None:
    if headers is None:
        return
    for name in ("Content-Type", "Content-Encoding", "Server"):
        value = headers.get(name)
        if value:
            print(f"[diagnostic] {name}: {redact(value, secret)}", flush=True)


def show_http_error(error: HTTPError, secret: str) -> None:
    print(f"[diagnostic] HTTP {error.code}", flush=True)
    show_headers(error.headers, secret)
    try:
        encoding = error.headers.get("Content-Encoding", "") if error.headers else ""
        details = error_details(error.read(MAX_ERROR_BYTES + 1), secret, encoding)
    except Exception:
        details = ["The server's error body could not be read."]
    for detail in details:
        print("[diagnostic] " + detail, flush=True)


def public_probe(urlopen, secret: str) -> bool:
    """Check the public API through the same network settings, without a key."""
    print("[diagnostic] Public API check: GET /api/v1/models (no API key)", flush=True)
    request = Request("https://openrouter.ai/api/v1/models",
                      headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            status = response.status
            print(f"[diagnostic] Public API status: {status}", flush=True)
            show_headers(response.headers, secret)
            encoding = response.headers.get("Content-Encoding", "").lower().strip()
            if encoding == "gzip":
                with gzip.GzipFile(fileobj=response) as stream:
                    prefix = stream.read(256)
            else:
                prefix = response.read(256)
            prefix = prefix.lstrip(b"\xef\xbb\xbf \r\n\t")
            is_json = "application/json" in response.headers.get("Content-Type", "").lower()
            if status != 200 or not is_json or not prefix.startswith((b"{", b"[")):
                print("[diagnostic] Public API did not return the expected JSON; live demo not started.", flush=True)
                return False
            return True
    except HTTPError as error:
        show_http_error(error, secret)
    except Exception as error:
        # Exception class only: proxy errors may contain URLs with credentials.
        print(f"[diagnostic] Public API connection failed ({type(error).__name__}).", flush=True)
    return False


def key_probe(urlopen, secret: str) -> bool:
    """Check authentication without printing key metadata or generating text."""
    print("[diagnostic] Key check: GET /api/v1/key (no model generation)", flush=True)
    request = Request("https://openrouter.ai/api/v1/key",
                      headers={"Authorization": f"Bearer {secret}",
                               "Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            print(f"[diagnostic] Key check status: {response.status}", flush=True)
            show_headers(response.headers, secret)
            encoding = response.headers.get("Content-Encoding", "").lower().strip()
            if encoding == "gzip":
                with gzip.GzipFile(fileobj=response) as stream:
                    body = stream.read(MAX_ERROR_BYTES + 1)
            else:
                body = response.read(MAX_ERROR_BYTES + 1)
            if len(body) > MAX_ERROR_BYTES:
                print("[diagnostic] Key check response exceeded the limit; details omitted.", flush=True)
                return False
            try:
                data = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                print("[diagnostic] Key check did not return valid JSON; details omitted.", flush=True)
                return False
            if (response.status != 200 or not isinstance(data, dict)
                    or data.get("error") is not None or not isinstance(data.get("data"), dict)):
                print("[diagnostic] Key check response was unexpected; details omitted.", flush=True)
                return False
            print("[diagnostic] Key accepted. This does not yet confirm model access or credits.", flush=True)
            if data["data"].get("is_management_key") is True:
                print("[diagnostic] This is a management key; use an inference API key for model calls.", flush=True)
                return False
            return True
    except HTTPError as error:
        show_http_error(error, secret)
    except Exception as error:
        print(f"[diagnostic] Key check connection failed ({type(error).__name__}).", flush=True)
    return False


def main() -> int:
    secret = os.environ.get("OPENROUTER_API_KEY", "").strip()
    real_urlopen = agents.urlopen
    request_number = 0
    phase_override = "minimal"

    print("DIAGNOSTIC v3: public API, key, simple model request, then agent demo.", flush=True)
    print(f"[diagnostic] Runtime: {platform.system()} / Python {platform.python_version()}", flush=True)
    try:
        client = agents.OpenRouterClient.from_env()
    except agents.AgentError as error:
        print("[diagnostic] " + str(error), flush=True)
        return 1
    if any(ord(char) < 33 or ord(char) > 126 or char in "\"'" for char in secret):
        print("[diagnostic] The key contains whitespace, quotes or non-ASCII characters. "
              "Re-enter only the API key in PowerShell; its value has not been printed.", flush=True)
        return 1
    proxy_types = sorted(key for key, value in getproxies().items()
                         if key in {"http", "https", "all"} and value)
    print("[diagnostic] Configured proxy types: " + (", ".join(proxy_types) or "none detected"), flush=True)
    if not public_probe(real_urlopen, secret):
        print("[diagnostic] Public API check failed before any authenticated model request.", flush=True)
        return 1
    if not key_probe(real_urlopen, secret):
        print("[diagnostic] Stopped at the key check; no model requests or agent tools were sent.", flush=True)
        return 1

    def traced_urlopen(request, *args, **kwargs):
        nonlocal request_number
        request_number += 1
        payload = json.loads(request.data.decode("utf-8"))
        names = [tool.get("function", {}).get("name") for tool in payload.get("tools", [])]
        phase = phase_override or ("report" if "propose_report" in names else
                                   "cleanup" if "propose_cleanup" in names else "explanation")
        history = payload.get("messages", [])
        tool_results = sum(message.get("role") == "tool" for message in history)
        model = redact(payload.get("model", "unknown"), secret)
        print(f"[diagnostic] Request {request_number}: phase={phase}; model={model}; "
              f"previous tool results={tool_results}", flush=True)
        try:
            return real_urlopen(request, *args, **kwargs)
        except HTTPError as error:
            show_http_error(error, secret)
            raise  # Preserve the original client's failure behavior.

    # This isolated terminal launcher observes HTTP errors only. The actual
    # client, payload construction, tool validation and workflow are unchanged.
    agents.urlopen = traced_urlopen
    try:
        print("[diagnostic] Simple model check: same model, no tools, up to 32 output tokens.", flush=True)
        try:
            minimal_client = agents.OpenRouterClient(client.api_key, client.model,
                                                    timeout=client.timeout, max_tokens=32)
            minimal_client.complete([{"role": "user", "content": "Reply with OK."}])
        except agents.AgentError as error:
            print("[diagnostic] Simple model check failed before any agent tools were sent.", flush=True)
            print("[diagnostic] " + str(error), flush=True)
            return 1
        print("[diagnostic] Simple model check passed. Starting the original agent demo.", flush=True)
        phase_override = None
        print("DIAGNOSTIC LIVE DEMO: real OpenRouter calls using synthetic demo data.", flush=True)
        return demo_agents.main(["--live"])
    finally:
        agents.urlopen = real_urlopen


if __name__ == "__main__":
    sys.exit(main())
