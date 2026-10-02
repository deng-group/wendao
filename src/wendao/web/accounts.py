"""Student sign-in and daily question limits.

The teacher lists the class in a roster CSV (`[student] roster`, e.g. `students.csv`) with an
`email` column and optional `name` and `limit` columns (a personal daily limit). A student signs
in with their email; if it is on the roster, the server returns a signed token that the app
sends with each question. Questions on the teacher's AI are then counted per student per day.

Signing in with an email alone means someone who knows a classmate's email could use that
classmate's daily questions. They can never get more than that student's limit, and only
enrolled emails work.

Files, next to `wendao.toml` on the server:
    students.csv    the roster (personal data: keep it out of git)
    usage.db        questions per student per day (SQLite)
    .wendao-secret  key that signs sign-in tokens (or set WENDAO_SECRET)
"""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignInError(Exception):
    """Sign-in was refused; the message tells the student why."""


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


@dataclass
class Student:
    email: str
    name: str = ""
    limit: int | None = None  # personal daily limit; None means the course default


def load_roster(path: Path) -> dict[str, Student]:
    """Read the roster CSV. Column names are matched loosely (Email, E-mail, Name, Limit...)."""
    if not path.is_file():
        raise FileNotFoundError(f"Roster not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = {re.sub(r"[^a-z]", "", (name or "").lower()): name for name in reader.fieldnames or []}
        email_col = columns.get("email") or columns.get("emailaddress") or columns.get("mail")
        if not email_col:
            raise ValueError(f"{path.name} needs an `email` column.")
        name_col = columns.get("name") or columns.get("fullname") or columns.get("student")
        limit_col = columns.get("limit") or columns.get("dailylimit")
        roster: dict[str, Student] = {}
        for number, row in enumerate(reader, start=2):
            email = normalize_email(row.get(email_col, ""))
            if not email:
                continue
            if not EMAIL_RE.match(email):
                raise ValueError(f"{path.name}, line {number}: `{email}` is not an email address.")
            raw_limit = (row.get(limit_col) or "").strip() if limit_col else ""
            limit = int(raw_limit) if raw_limit else None
            roster[email] = Student(email, (row.get(name_col) or "").strip() if name_col else "", limit)
    return roster


class TokenSigner:
    """Signs sign-in tokens so the server can trust them without storing sessions."""

    def __init__(self, secret: bytes):
        self.secret = secret

    @classmethod
    def for_folder(cls, folder: Path) -> TokenSigner:
        if os.environ.get("WENDAO_SECRET"):
            return cls(os.environ["WENDAO_SECRET"].encode("utf-8"))
        path = folder / ".wendao-secret"
        if not path.exists():
            # Several server workers may start at once: write a candidate, then link it into place.
            # Linking fails if another worker got there first, and then everyone reads the winner's secret.
            candidate = folder / f".wendao-secret.{os.getpid()}.{secrets.token_hex(4)}"
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(secrets.token_hex(32))
            try:
                os.link(candidate, path)
            except FileExistsError:
                pass
            finally:
                candidate.unlink(missing_ok=True)
        return cls(path.read_text(encoding="utf-8").strip().encode("utf-8"))

    def sign(self, email: str) -> str:
        body = base64.urlsafe_b64encode(email.encode("utf-8")).decode("ascii").rstrip("=")
        mac = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
        return f"{body}.{mac}"

    def verify(self, token: str) -> str | None:
        try:
            body, mac = str(token or "").split(".", 1)
        except ValueError:
            return None
        expected = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(mac, expected):
            return None
        padded = body + "=" * (-len(body) % 4)
        try:
            return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None


class UsageStore:
    """Questions per student per day, in SQLite so counts survive restarts."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS usage (email TEXT, day TEXT, questions INTEGER, PRIMARY KEY (email, day))")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def take(self, email: str, limit: int) -> int:
        """Count one question; return how many the student has left today. Raise if over the limit.

        The check and the update run in one locked transaction, so server workers sharing this file
        can't both let the last allowed question through.
        """
        today = date.today().isoformat()
        with self._lock:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT questions FROM usage WHERE email = ? AND day = ?", (email, today)).fetchone()
                used = row[0] if row else 0
                if limit > 0 and used >= limit:
                    db.execute("ROLLBACK")
                    raise SignInError(
                        f"You've used all {limit} of today's questions on the course AI. Try again tomorrow"
                        + ", or add your own AI key in Settings if your course allows it."
                    )
                db.execute(
                    "INSERT INTO usage (email, day, questions) VALUES (?, ?, 1) "
                    "ON CONFLICT(email, day) DO UPDATE SET questions = questions + 1",
                    (email, today),
                )
                db.execute("COMMIT")
            finally:
                db.close()
        return max(limit - used - 1, 0) if limit > 0 else -1

    def report(self, day: str | None = None) -> list[tuple[str, int]]:
        with self._connect() as db:
            # Rows for anonymous visitors (no roster) are keyed "address:<ip>"; reports show students only.
            if day:
                rows = db.execute("SELECT email, questions FROM usage WHERE day = ? AND email NOT LIKE 'address:%' "
                                  "ORDER BY questions DESC", (day,))
            else:
                rows = db.execute("SELECT email, SUM(questions) FROM usage WHERE email NOT LIKE 'address:%' "
                                  "GROUP BY email ORDER BY 2 DESC")
            return [(email, int(count)) for email, count in rows.fetchall()]


class Accounts:
    """Roster, sign-in tokens, and per-student limits for one course server."""

    def __init__(self, roster_path: Path, folder: Path, default_limit: int = 0):
        self.roster_path = roster_path
        self.default_limit = default_limit
        self.signer = TokenSigner.for_folder(folder)
        self.usage = UsageStore(folder / "usage.db")
        self._roster: dict[str, Student] = {}
        self._mtime = 0.0
        self.reload()

    def reload(self) -> None:
        """Reread the roster if the file changed, so teachers can add students without a restart."""
        mtime = self.roster_path.stat().st_mtime
        if mtime != self._mtime:
            self._roster = load_roster(self.roster_path)
            self._mtime = mtime

    def sign_in(self, email: str) -> tuple[str, Student]:
        if not EMAIL_RE.match(normalize_email(email)):
            raise SignInError("Enter your email address.")
        self.reload()
        student = self._roster.get(normalize_email(email))
        if not student:
            raise SignInError("This email is not on the class list. Use the email your course knows you by, or ask your teacher.")
        return self.signer.sign(student.email), student

    def student_for(self, token: str) -> Student | None:
        email = self.signer.verify(token)
        if not email:
            return None
        self.reload()
        return self._roster.get(email)  # removed from the roster = signed out

    def take_question(self, student: Student) -> int:
        limit = student.limit if student.limit is not None else self.default_limit
        return self.usage.take(student.email, limit)
