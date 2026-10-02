"""Language model providers: Anthropic, OpenAI-compatible, Gemini, and a dry run.

Each provider turns a prompt package from `PromptBuilder` into an answer, either all at
once (`generate`) or piece by piece (`stream`). Providers use only the standard library
and `curl`, so no vendor SDKs are needed. Settings come from environment variables,
which a workspace fills in from `.env` and the [model] section of `wendao.toml`.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol
from urllib import error, request
from urllib.parse import urlsplit


DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_TEMPERATURE = 0.2


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE pairs without overriding existing environment."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def temperature_setting() -> dict:
    """Return the request's temperature field, read from `LLM_TEMPERATURE`.

    Defaults to 0.2 for focused, evidence-based answers. Set `LLM_TEMPERATURE=none`
    to leave the field out, for models that only accept their default temperature
    (for example OpenAI reasoning models).
    """
    raw = os.environ.get("LLM_TEMPERATURE", "").strip()
    if not raw:
        return {"temperature": DEFAULT_TEMPERATURE}
    if raw.lower() == "none":
        return {}
    try:
        return {"temperature": float(raw)}
    except ValueError:
        raise RuntimeError(f"LLM_TEMPERATURE must be a number or `none`, not `{raw}`.") from None


@contextmanager
def _curl_config(write):
    """A private curl config file (owner-only) that curl can open on every OS, deleted afterwards.

    The key goes in this file rather than on the command line, where other local users could see it.
    The file is closed before curl reads it, because Windows won't let a second program open it otherwise.
    """
    fd, path = tempfile.mkstemp(prefix="wendao-curl-", suffix=".cfg")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            write(handle)
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class _NoRedirect(request.HTTPRedirectHandler):
    """Refuse redirects, so an endpoint given by a student can't bounce the server to another address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise error.HTTPError(req.full_url, code, f"Redirect to {newurl} refused", headers, fp)


_NO_REDIRECT_OPENER = request.build_opener(_NoRedirect)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to an already-checked IP address, while TLS still verifies the real hostname.

    Connecting to the address that was checked (instead of looking the name up again) stops a
    student's hostname from being re-pointed at a private machine after the check (DNS rebinding).
    """

    def __init__(self, host, pinned_ip: str, **kwargs):
        super().__init__(host, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self):
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPSHandler(request.HTTPSHandler):
    def __init__(self, pinned_ip: str):
        super().__init__()
        self.pinned_ip = pinned_ip

    def https_open(self, req):
        return self.do_open(lambda host, **kwargs: _PinnedHTTPSConnection(host, self.pinned_ip, **kwargs), req, context=self._context)


def _open(req: request.Request, label: str, timeout: int, follow_redirects: bool = True, pinned_ip: str | None = None):
    """Open an HTTP request and turn transport failures into readable errors."""
    try:
        if pinned_ip:
            if urlsplit(req.full_url).scheme != "https":
                raise RuntimeError(f"{label}: a checked AI server address must use https.")
            return request.build_opener(_NoRedirect, _PinnedHTTPSHandler(pinned_ip)).open(req, timeout=timeout)
        if not follow_redirects:
            return _NO_REDIRECT_OPENER.open(req, timeout=timeout)
        return request.urlopen(req, timeout=timeout)
    except error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise RuntimeError(
                f"{label} server tried to redirect to another address; redirects aren't allowed for your own AI server."
            ) from exc
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"{label} API error {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"{label} API unreachable: {exc.reason}") from exc


def _iter_sse_data(response) -> Iterator[dict]:
    """Yield the JSON payloads of the `data:` lines in a server-sent event stream."""
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        yield json.loads(data)


def _setting(provider, value: str | None, *env_names: str, default: str | None = None) -> str | None:
    """An explicit value wins; otherwise read the environment, unless the provider was given its own credentials."""
    if value:
        return value
    if getattr(provider, "use_env", True):
        for name in env_names:
            if os.environ.get(name):
                return os.environ[name]
    return default


class LLMProvider(Protocol):
    """Provider interface used by AnswerGenerator."""

    name: str

    def generate(self, prompt_package: dict) -> dict:
        """Generate an answer from a prompt package."""


@dataclass
class DryRunProvider:
    """Deterministic provider for local testing.

    It does not pretend to be an LLM. It returns a compact, policy-aware preview
    that lets us verify routing, evidence selection, citations, temporal context,
    and short-memory packaging.
    """

    name: str = "dry_run"

    def resolved_model(self) -> None:
        return None

    def generate(self, prompt_package: dict) -> dict:
        action = prompt_package["llm_action"]
        status = prompt_package["status"]
        evidence = prompt_package.get("evidence", [])
        citations = [item["chunk_id"] for item in evidence]

        if action == "generate_answer":
            lead = "Dry run answer preview: this question is ready for an LLM answer using the selected course evidence."
            if prompt_package["answer_policy"].get("requires_temporal_context"):
                lead += f" Temporal context required: {prompt_package['answer_policy']['temporal_context']}."
            if citations:
                lead += " Candidate citations: " + ", ".join(citations) + "."
        elif action == "ask_clarification":
            lead = "Dry run answer preview: ask the student to narrow the question before answering."
        elif action == "insufficient_evidence":
            lead = "Dry run answer preview: explain that the course evidence is insufficient for a verified answer."
        elif action == "refuse_or_fallback":
            lead = "Dry run answer preview: explain that the question appears outside the course scope."
        else:
            lead = f"Dry run answer preview: inspect status `{status}` before answering."

        return {
            "provider": self.name,
            "model": None,
            "answer": lead,
            "citations": citations,
            "raw_response": None,
        }

    def stream(self, prompt_package: dict) -> Iterator[str]:
        words = self.generate(prompt_package)["answer"].split(" ")
        for index, word in enumerate(words):
            yield word if index == 0 else f" {word}"


@dataclass
class OpenAICompatibleProvider:
    """OpenAI Chat Completions provider for OpenAI and any compatible endpoint.

    Works with any server that implements `POST {base_url}/chat/completions`,
    such as OpenAI, DeepSeek, OpenRouter, vLLM, Ollama, or LM Studio. Configure
    it with `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL`. The key is
    optional for custom base URLs, since local servers often need none.
    """

    model: str | None = None
    name: str = "openai"
    api_key: str | None = None
    base_url: str | None = None
    use_env: bool = True
    follow_redirects: bool = True
    # Set when a student's base_url was checked on a public server: connect only to that IP.
    pinned_ip: str | None = None

    def resolved_model(self) -> str:
        return _setting(self, self.model, "OPENAI_MODEL", default=DEFAULT_OPENAI_MODEL)

    def _request(self, prompt_package: dict, stream: bool) -> request.Request:
        base_url = _setting(self, self.base_url, "OPENAI_BASE_URL", default=DEFAULT_OPENAI_BASE_URL).rstrip("/")
        api_key = _setting(self, self.api_key, "OPENAI_API_KEY")
        if not api_key and base_url == DEFAULT_OPENAI_BASE_URL:
            raise RuntimeError("Set OPENAI_API_KEY (in your workspace's .env file) before using the OpenAI provider.")

        messages = prompt_package["messages"]
        payload = {
            "model": self.resolved_model(),
            **temperature_setting(),
            "messages": [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "user", "content": messages[1]["content"]},
            ],
        }
        if stream:
            payload["stream"] = True
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

    def generate(self, prompt_package: dict) -> dict:
        with _open(self._request(prompt_package, stream=False), "OpenAI-compatible", 90, self.follow_redirects, self.pinned_ip) as response:
            raw = json.loads(response.read().decode("utf-8"))
        return {
            "provider": self.name,
            "model": self.resolved_model(),
            "answer": self._extract_text(raw),
            "citations": [item["chunk_id"] for item in prompt_package.get("evidence", [])],
            "raw_response": raw,
        }

    def stream(self, prompt_package: dict) -> Iterator[str]:
        """Yield text deltas from an OpenAI-compatible SSE response."""
        with _open(self._request(prompt_package, stream=True), "OpenAI-compatible", 120, self.follow_redirects, self.pinned_ip) as response:
            for event_payload in _iter_sse_data(response):
                if event_payload.get("error"):
                    message = event_payload["error"].get("message", "Unknown streaming error")
                    raise RuntimeError(f"OpenAI-compatible API error: {message}")
                for choice in event_payload.get("choices", []):
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        yield text

    @staticmethod
    def _extract_text(raw: dict) -> str:
        parts = []
        for choice in raw.get("choices", []):
            text = (choice.get("message") or {}).get("content")
            if text:
                parts.append(text)
        if parts:
            return "\n".join(parts).strip()
        return json.dumps(raw, ensure_ascii=False)


@dataclass
class GeminiProvider:
    """Google Gemini REST provider using generateContent."""

    model: str | None = None
    name: str = "gemini"
    api_key: str | None = None
    use_env: bool = True
    follow_redirects: bool = True

    def resolved_model(self) -> str:
        return _setting(self, self.model, "GEMINI_MODEL", default=DEFAULT_GEMINI_MODEL)

    def _request(self, prompt_package: dict, stream: bool) -> request.Request:
        api_key = _setting(self, self.api_key, "GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("Set GEMINI_API_KEY (in your workspace's .env file) before using the Gemini provider.")

        messages = prompt_package["messages"]
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.resolved_model()}:{method}"
        payload = {
            "systemInstruction": {"parts": [{"text": messages[0]["content"]}]},
            "contents": [{"role": "user", "parts": [{"text": messages[1]["content"]}]}],
            "generationConfig": temperature_setting(),
        }
        return request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )

    def generate(self, prompt_package: dict) -> dict:
        with _open(self._request(prompt_package, stream=False), "Gemini", 60, self.follow_redirects) as response:
            raw = json.loads(response.read().decode("utf-8"))
        return {
            "provider": self.name,
            "model": self.resolved_model(),
            "answer": self._extract_text(raw),
            "citations": [item["chunk_id"] for item in prompt_package.get("evidence", [])],
            "raw_response": raw,
        }

    def stream(self, prompt_package: dict) -> Iterator[str]:
        """Yield text deltas from a Gemini SSE response."""
        with _open(self._request(prompt_package, stream=True), "Gemini", 120, self.follow_redirects) as response:
            for event_payload in _iter_sse_data(response):
                if event_payload.get("error"):
                    message = event_payload["error"].get("message", "Unknown streaming error")
                    raise RuntimeError(f"Gemini API error: {message}")
                for candidate in event_payload.get("candidates", []):
                    for part in candidate.get("content", {}).get("parts", []):
                        if part.get("text"):
                            yield part["text"]

    @staticmethod
    def _extract_text(raw: dict) -> str:
        parts = []
        for candidate in raw.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                text = part.get("text")
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts).strip()
        return json.dumps(raw, ensure_ascii=False)


@dataclass
class AnthropicProvider:
    """Anthropic-compatible Messages API provider."""

    model: str | None = None
    name: str = "anthropic"
    api_key: str | None = None
    base_url: str | None = None
    use_env: bool = True
    # Set when a student's base_url was checked on a public server: curl connects only to that IP.
    pinned_ip: str | None = None

    def resolved_model(self) -> str:
        return _setting(self, self.model, "ANTHROPIC_MODEL", default=DEFAULT_ANTHROPIC_MODEL)

    def _write_curl_config(self, config, base_url: str, token: str) -> None:
        escaped_token = token.replace("\\", "\\\\").replace('"', '\\"')
        config.write(f'url = "{base_url}/v1/messages"\n')
        self._write_pin(config, base_url)
        config.write('header = "content-type: application/json"\n')
        config.write('header = "anthropic-version: 2023-06-01"\n')
        config.write(f'header = "x-api-key: {escaped_token}"\n')
        config.write('request = "POST"\n')

    def _write_pin(self, config, base_url: str) -> None:
        if not self.pinned_ip:
            return
        parts = urlsplit(base_url)
        address = f"[{self.pinned_ip}]" if ":" in self.pinned_ip else self.pinned_ip
        config.write(f'resolve = "{parts.hostname}:{parts.port or 443}:{address}"\n')

    def _endpoint(self) -> tuple[str, str | None]:
        base_url = _setting(self, self.base_url, "ANTHROPIC_BASE_URL", default="https://api.anthropic.com").rstrip("/")
        return base_url, _setting(self, self.api_key, "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")

    def generate(self, prompt_package: dict) -> dict:
        base_url, token = self._endpoint()
        model = self.resolved_model()
        if not token:
            raise RuntimeError("Set ANTHROPIC_AUTH_TOKEN (in your workspace's .env file) before using the Anthropic provider.")

        messages = prompt_package["messages"]
        payload = {
            "model": model,
            "max_tokens": 2048,
            **temperature_setting(),
            "system": messages[0]["content"],
            "messages": [
                {
                    "role": "user",
                    "content": messages[1]["content"],
                }
            ],
        }
        # Keep credentials and request content out of the process command line,
        # where local process-inspection tools could otherwise reveal them.
        with _curl_config(lambda config: self._write_curl_config(config, base_url, token)) as config_path:
            completed = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--fail-with-body",
                    "--max-time",
                    "90",
                    "--config",
                    config_path,
                    "--data-binary",
                    "@-",
                ],
                input=json.dumps(payload),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        if completed.returncode != 0:
            body = (completed.stdout or completed.stderr).strip()
            raise RuntimeError(f"Anthropic API error via curl: {body}")
        raw = json.loads(completed.stdout)

        answer = self._extract_text(raw)
        return {
            "provider": self.name,
            "model": model,
            "answer": answer,
            "citations": [item["chunk_id"] for item in prompt_package.get("evidence", [])],
            "raw_response": raw,
        }

    def stream(self, prompt_package: dict) -> Iterator[str]:
        """Yield text deltas from an Anthropic-compatible SSE response."""
        base_url, token = self._endpoint()
        if not token:
            raise RuntimeError("Set ANTHROPIC_AUTH_TOKEN (in your workspace's .env file) before using the Anthropic provider.")

        messages = prompt_package["messages"]
        payload = {
            "model": self.resolved_model(),
            "max_tokens": 2048,
            **temperature_setting(),
            "stream": True,
            "system": messages[0]["content"],
            "messages": [{"role": "user", "content": messages[1]["content"]}],
        }

        process = None
        with _curl_config(lambda config: self._write_curl_config(config, base_url, token)) as config_path:
            process = subprocess.Popen(
                [
                    "curl",
                    "-sS",
                    "--no-buffer",
                    "--fail-with-body",
                    "--max-time",
                    "120",
                    "--config",
                    config_path,
                    "--data-binary",
                    "@-",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(json.dumps(payload))
            process.stdin.close()
            non_event_output = []
            try:
                for raw_line in process.stdout:
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        if line and not line.startswith("event:"):
                            non_event_output.append(line)
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event_payload = json.loads(data)
                    if event_payload.get("type") == "error":
                        message = event_payload.get("error", {}).get("message", "Unknown streaming error")
                        raise RuntimeError(f"Anthropic API error: {message}")
                    if event_payload.get("type") != "content_block_delta":
                        continue
                    delta = event_payload.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield delta["text"]
                return_code = process.wait()
                if return_code != 0:
                    assert process.stderr is not None
                    body = " ".join(non_event_output) or process.stderr.read().strip()
                    raise RuntimeError(f"Anthropic streaming API error via curl: {body}")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    @staticmethod
    def _extract_text(raw: dict) -> str:
        parts = []
        for item in raw.get("content", []):
            text = item.get("text")
            if text:
                parts.append(text)
        if parts:
            return "\n".join(parts).strip()
        return json.dumps(raw, ensure_ascii=False)


PROVIDER_NAMES = ("dry_run", "anthropic", "openai", "gemini")


def provider_from_name(
    name: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    pinned_ip: str | None = None,
) -> LLMProvider:
    """Build a provider. With `api_key`, it uses only the given credentials and ignores the environment."""
    if api_key:
        own = {"model": model or None, "api_key": api_key, "use_env": False}
        if name == "openai":
            return OpenAICompatibleProvider(base_url=base_url or None, follow_redirects=False, pinned_ip=pinned_ip, **own)
        if name == "anthropic":
            return AnthropicProvider(base_url=base_url or None, pinned_ip=pinned_ip, **own)
        if name == "gemini":
            return GeminiProvider(follow_redirects=False, **own)
        raise ValueError(f"Unknown provider: {name}")
    if name == "dry_run":
        return DryRunProvider()
    if name == "openai":
        return OpenAICompatibleProvider(model=model)
    if name == "gemini":
        return GeminiProvider(model=model)
    if name == "anthropic":
        return AnthropicProvider(model=model)
    raise ValueError(f"Unknown provider: {name}")


def default_provider_name() -> str:
    """Pick the provider from `LLM_PROVIDER`, else from whichever credentials are set."""
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        if explicit not in PROVIDER_NAMES:
            raise ValueError(f"Unknown LLM_PROVIDER `{explicit}`; expected one of {', '.join(PROVIDER_NAMES)}.")
        return explicit
    if os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL"):
        return "openai"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    return "dry_run"


def default_model_name(provider: str) -> str | None:
    resolve_model = getattr(provider_from_name(provider), "resolved_model", None)
    return resolve_model() if callable(resolve_model) else None


def check_connection() -> tuple[str, str | None]:
    """Send one tiny request to the configured provider. Returns (provider, model)."""
    provider_name = default_provider_name()
    if provider_name == "dry_run":
        raise RuntimeError(
            "No language model is configured. Add an API key to .env in your workspace, "
            "or set the [model] section in wendao.toml."
        )
    model = default_model_name(provider_name)
    provider = provider_from_name(provider_name, model=model)
    prompt_package = {
        "messages": [
            {"role": "system", "content": "This is a connectivity check."},
            {"role": "user", "content": "Reply with OK."},
        ],
        "evidence": [],
    }
    result = provider.generate(prompt_package)
    if not str(result.get("answer") or "").strip():
        raise RuntimeError("The model returned no text.")
    return provider_name, model
