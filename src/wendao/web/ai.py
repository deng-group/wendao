"""Who answers students' questions: the teacher's AI or the student's own key.

The teacher chooses with `[student] ai` in wendao.toml:

- "teacher": use the teacher's model and key (kept on the server; students never see it),
  with an optional daily limit per student.
- "student": every student uses their own API key, entered in the app. The key is sent with
  each question and used only for that question; it is never stored on the server.
- "either":  the teacher's AI by default; students may add their own key instead.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit

from wendao.rag.providers import PROVIDER_NAMES, default_model_name, default_provider_name, provider_from_name

AI_MODES = ("teacher", "student", "either")


class AiUnavailable(Exception):
    """The question cannot be answered with the current AI settings; the message tells the student why."""

    code = "AiUnavailable"


def check_public_endpoint(url: str) -> str:
    """Refuse a student-supplied AI address that would make this server reach a private machine (SSRF).

    The address must use HTTPS and resolve only to public internet addresses: no localhost,
    private networks (10.x, 192.168.x, ...), link-local (cloud metadata), or other reserved ranges.

    Returns the checked IP address; the provider connects only to it, so the name can't be
    re-pointed at a private machine between this check and the request (DNS rebinding).
    """
    # Check the raw text first: urlsplit silently drops line breaks and tabs, which could otherwise
    # smuggle extra settings past this check into the request (for example into curl's config).
    if any(ord(char) < 32 or ord(char) == 127 for char in url) or " " in url.strip():
        raise AiUnavailable("Your AI server address contains spaces, line breaks, or other hidden characters.")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise AiUnavailable("Your AI server address must start with https://.")
    try:
        port = parts.port or 443
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)}
    except (socket.gaierror, ValueError) as exc:
        raise AiUnavailable(f"Can't find the AI server {parts.hostname}.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise AiUnavailable(
                f"The AI server address {parts.hostname} points to a private or local network, which this course "
                "website can't reach. Use a public https:// address."
            )
    return sorted(addresses)[0].split("%")[0]


class LoginRequired(AiUnavailable):
    """The course AI needs the student to sign in with their email first."""

    code = "LoginRequired"


@dataclass
class DailyLimit:
    """Questions per student per day on the teacher's key (0 = no limit). Kept in memory."""

    per_day: int = 0
    _counts: dict[str, tuple[date, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def take(self, student: str) -> None:
        if self.per_day <= 0:
            return
        today = date.today()
        with self._lock:
            day, used = self._counts.get(student, (today, 0))
            if day != today:
                used = 0
            if used >= self.per_day:
                raise AiUnavailable(
                    f"You've reached today's limit of {self.per_day} questions on the course AI. "
                    "Try again tomorrow, or add your own AI key in Settings if your course allows it."
                )
            self._counts[student] = (today, used + 1)


class SharedDailyLimit:
    """The per-address limit (no roster), stored in usage.db so all server workers share one count."""

    def __init__(self, store, per_day: int):
        self.store = store
        self.per_day = per_day

    def take(self, student: str) -> None:
        if self.per_day <= 0:
            return
        from wendao.web.accounts import SignInError

        try:
            self.store.take(f"address:{student}", self.per_day)
        except SignInError as exc:
            raise AiUnavailable(str(exc)) from exc


@dataclass
class AiPolicy:
    mode: str = "teacher"
    limit: DailyLimit = field(default_factory=DailyLimit)
    # Roster sign-in on this server (see accounts.py), or None.
    accounts: object | None = None

    @classmethod
    def from_settings(cls, mode: str = "teacher", questions_per_day: int = 0, accounts=None, limit=None) -> AiPolicy:
        if mode not in AI_MODES:
            raise ValueError(f"[student] ai must be one of {', '.join(AI_MODES)}, not `{mode}`.")
        return cls(mode=mode, limit=limit or DailyLimit(int(questions_per_day or 0)), accounts=accounts)

    @property
    def login_required(self) -> bool:
        """Students must sign in to use the course AI (their own key needs no sign-in)."""
        return self.mode != "student" and bool(self.accounts)

    @property
    def allows_student_keys(self) -> bool:
        return self.mode in {"student", "either"}

    def teacher_ai_available(self) -> bool:
        return self.mode != "student" and default_provider_name() != "dry_run"

    def describe(self) -> dict:
        """What the app UI needs to know (never includes keys)."""
        teacher = self.teacher_ai_available()
        provider = default_provider_name() if teacher else None
        return {
            "mode": self.mode,
            "teacher_ai": teacher,
            "student_keys": self.allows_student_keys,
            "needs_student_key": self.mode == "student" or (self.mode == "either" and not teacher),
            "login_required": self.login_required,
            "provider": provider,
            "model": default_model_name(provider) if provider else None,
        }

    def choose(self, payload: dict, student: str, token: str = ""):
        """The provider for this question, or raise AiUnavailable.

        `student` is the client's address (for the limit when there is no roster); `token` is the
        sign-in token, if the student signed in.
        """
        own = payload.get("ai") or {}
        for field_name in ("provider", "model", "api_key", "base_url"):
            value = str(own.get(field_name) or "")
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise AiUnavailable(f"Your AI {field_name.replace('_', ' ')} contains a line break or other hidden character.")
        if own.get("api_key") or (own.get("provider") == "openai" and own.get("base_url")):
            if not self.allows_student_keys:
                raise AiUnavailable("This course uses the course AI; personal AI keys are turned off.")
            name = str(own.get("provider") or "")
            if name not in PROVIDER_NAMES or name == "dry_run":
                raise AiUnavailable("Choose an AI provider in Settings: Anthropic, OpenAI-compatible, or Gemini.")
            base_url = str(own.get("base_url") or "").strip()
            pinned_ip = None
            if base_url:  # a student's own server must be public, so nobody can use this server to reach private machines
                if name == "gemini":
                    raise AiUnavailable("Gemini uses Google's own address; leave the server address empty.")
                pinned_ip = check_public_endpoint(base_url)
            return provider_from_name(
                name,
                model=str(own.get("model") or "") or None,
                api_key=str(own.get("api_key") or "") or "none",
                base_url=base_url or None,
                pinned_ip=pinned_ip,
            )
        if self.mode == "student":
            raise AiUnavailable("This course asks you to use your own AI key. Add it in Settings to ask questions.")
        name = default_provider_name()
        if name == "dry_run":
            hint = " or add your own AI key in Settings" if self.allows_student_keys else ""
            raise AiUnavailable(f"The course AI is not set up yet. Ask your teacher{hint}.")
        if self.accounts is not None:
            from wendao.web.accounts import SignInError

            enrolled = self.accounts.student_for(token)
            if enrolled is None:
                raise LoginRequired("Sign in with your email to use the course AI.")
            try:
                self.accounts.take_question(enrolled)
            except SignInError as exc:
                raise AiUnavailable(str(exc)) from exc
        else:
            self.limit.take(student)
        return provider_from_name(name, model=default_model_name(name))


def bearer_token(req) -> str:
    """The sign-in token the app sends as `Authorization: Bearer <token>`."""
    header = req.headers.get("Authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def student_id(req) -> str:
    """Identify a student for the daily limit: their IP address, also behind a proxy on this machine.

    The headers are trusted only from this machine: Nginx sets X-Real-IP, and a Cloudflare Tunnel
    (cloudflared) sets CF-Connecting-IP.
    """
    if req.remote_addr in {"127.0.0.1", "::1"}:
        forwarded = (req.headers.get("CF-Connecting-IP") or req.headers.get("X-Real-IP")
                     or req.headers.get("X-Forwarded-For", "").split(",")[0].strip())
        if forwarded:
            return forwarded
    return req.remote_addr or "unknown"
