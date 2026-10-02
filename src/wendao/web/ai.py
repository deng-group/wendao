"""Who answers students' questions: the teacher's AI, the student's own key, or the teacher's website.

The teacher chooses with `[student] ai` in wendao.toml:

- "teacher": use the teacher's model and key (kept on the server; students never see it),
  with an optional daily limit per student.
- "student": every student uses their own API key, entered in the app. The key is sent with
  each question and used only for that question; it is never stored on the server.
- "either":  the teacher's AI by default; students may add their own key instead.

In a course app opened on a student's laptop (`wendao open`), "the teacher's AI" means the
teacher's Wendao website (`[student] server`), so the key stays on the teacher's server.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Iterator
from urllib import error, request

from wendao.rag.providers import PROVIDER_NAMES, default_model_name, default_provider_name, provider_from_name

AI_MODES = ("teacher", "student", "either")


class AiUnavailable(Exception):
    """The question cannot be answered with the current AI settings; the message tells the student why."""

    code = "AiUnavailable"


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


@dataclass
class AiPolicy:
    mode: str = "teacher"
    server: str = ""
    limit: DailyLimit = field(default_factory=DailyLimit)
    # False in course apps on students' laptops: "the course AI" is then only the teacher's website.
    use_local_key: bool = True
    # Roster sign-in on this server (see accounts.py), or, in a course app, whether the teacher's website needs it.
    accounts: object | None = None
    server_needs_login: bool = False

    @classmethod
    def from_settings(
        cls,
        mode: str = "teacher",
        server: str = "",
        questions_per_day: int = 0,
        use_local_key: bool = True,
        accounts=None,
        server_needs_login: bool = False,
    ) -> AiPolicy:
        if mode not in AI_MODES:
            raise ValueError(f"[student] ai must be one of {', '.join(AI_MODES)}, not `{mode}`.")
        return cls(
            mode=mode,
            server=server.rstrip("/"),
            limit=DailyLimit(int(questions_per_day or 0)),
            use_local_key=use_local_key,
            accounts=accounts,
            server_needs_login=server_needs_login,
        )

    @property
    def login_required(self) -> bool:
        """Students must sign in to use the course AI (their own key needs no sign-in)."""
        if self.mode == "student":
            return False
        return bool(self.accounts) if not self.server else self.server_needs_login

    def local_key(self) -> str:
        """The provider configured on this machine for the course AI, or "dry_run" if none may be used."""
        return default_provider_name() if self.use_local_key else "dry_run"

    @property
    def allows_student_keys(self) -> bool:
        return self.mode in {"student", "either"}

    def teacher_ai_available(self) -> bool:
        return self.mode != "student" and (self.local_key() != "dry_run" or bool(self.server))

    def describe(self) -> dict:
        """What the app UI needs to know (never includes keys)."""
        teacher = self.teacher_ai_available()
        provider = self.local_key() if teacher and not self.server else None
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
        """Return ("provider", provider) or ("forward", server URL) for this question, or raise AiUnavailable.

        `student` is the client's address (for the limit when there is no roster); `token` is the
        sign-in token from the app, if the student signed in.
        """
        own = payload.get("ai") or {}
        if own.get("api_key") or (own.get("provider") == "openai" and own.get("base_url")):
            if not self.allows_student_keys:
                raise AiUnavailable("This course uses the course AI; personal AI keys are turned off.")
            name = str(own.get("provider") or "")
            if name not in PROVIDER_NAMES or name == "dry_run":
                raise AiUnavailable("Choose an AI provider in Settings: Anthropic, OpenAI-compatible, or Gemini.")
            provider = provider_from_name(
                name,
                model=str(own.get("model") or "") or None,
                api_key=str(own.get("api_key") or "") or "none",  # local OpenAI-compatible servers often need no key
                base_url=str(own.get("base_url") or "") or None,
            )
            return "provider", provider
        if self.mode == "student":
            raise AiUnavailable("This course asks you to use your own AI key. Add it in Settings to ask questions.")
        if self.server:
            return "forward", self.server
        name = self.local_key()
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
        return "provider", provider_from_name(name, model=default_model_name(name))


def bearer_token(req) -> str:
    """The sign-in token the app sends as `Authorization: Bearer <token>`."""
    header = req.headers.get("Authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def _headers(token: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def forward_login(server: str, email: str) -> dict:
    """Sign in on the teacher's Wendao website (from a course app); returns its JSON reply."""
    req = request.Request(f"{server}/api/login", data=json.dumps({"email": email}).encode("utf-8"), headers=_headers(), method="POST")
    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except ValueError:
            return {"ok": False, "message": f"The course website answered with an error ({exc.code})."}
    except error.URLError:
        return {"ok": False, "message": f"Can't reach the course website ({server}). Check your internet connection."}


def forward_stream(server: str, payload: dict, token: str = "") -> Iterator[str]:
    """Send a question to the teacher's Wendao website and pass its streamed answer through."""
    body = {key: payload[key] for key in ("query", "short_memory", "context_node_ids") if key in payload}
    req = request.Request(
        f"{server}/api/answer/stream",
        data=json.dumps(body).encode("utf-8"),
        headers=_headers(token),
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=180) as response:
            for line in response:
                text = line.decode("utf-8").strip()
                if text:
                    yield text + "\n"
    except error.HTTPError as exc:
        raise AiUnavailable(f"The course website answered with an error ({exc.code}). Try again later.") from exc
    except error.URLError as exc:
        raise AiUnavailable(f"Can't reach the course website ({server}). Check your internet connection.") from exc


def student_id(req) -> str:
    """Identify a student for the daily limit: their IP address, also behind a reverse proxy on this machine."""
    if req.remote_addr in {"127.0.0.1", "::1"}:
        forwarded = req.headers.get("X-Real-IP") or req.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return req.remote_addr or "unknown"
