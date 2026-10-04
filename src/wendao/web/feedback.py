"""Students' thumbs up / thumbs down on answers, so the teacher can see where the course AI goes wrong.

Kept in usage.db next to the question counts, without names, emails, or addresses: each sender is
stored as a keyed hash that changes every day, used only to cap how much one sender can store.
`wendao feedback` shows the result.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

MAX_PER_SENDER_PER_DAY = 100
LIMITS = {"question": 1000, "answer": 6000, "note": 1000, "page": 300}


class FeedbackError(ValueError):
    """Feedback that can't be stored; the message is safe to show in the widget."""


class FeedbackStore:
    def __init__(self, path: Path, secret: bytes):
        self.path = path
        self.secret = secret
        self._lock = threading.Lock()
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, day TEXT, "
                "sender TEXT, rating TEXT, page TEXT, question TEXT, answer TEXT, sources TEXT, note TEXT)"
            )

    def _sender(self, who: str, day: str) -> str:
        return hmac.new(self.secret, f"{day}:{who}".encode("utf-8"), hashlib.sha256).hexdigest()[:16]

    def add(self, who: str, payload: dict) -> None:
        rating = payload.get("rating")
        if rating not in {"up", "down"}:
            raise FeedbackError("Rating must be up or down.")
        fields = {key: " ".join(str(payload.get(key) or "").split())[:limit] if key != "answer"
                  else str(payload.get(key) or "")[:limit] for key, limit in LIMITS.items()}
        if not fields["question"]:
            raise FeedbackError("Which question is this about?")
        sources = [str(item)[:300] for item in (payload.get("sources") or [])[:6] if item]
        now = datetime.now()
        day = now.date().isoformat()
        sender = self._sender(who, day)
        with self._lock:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            try:
                db.execute("BEGIN IMMEDIATE")  # count and insert together, so server workers share the cap
                (count,) = db.execute("SELECT COUNT(*) FROM feedback WHERE sender = ? AND day = ?", (sender, day)).fetchone()
                if count >= MAX_PER_SENDER_PER_DAY:
                    db.execute("ROLLBACK")
                    raise FeedbackError("Thanks, that's enough feedback for today.")
                db.execute(
                    "INSERT INTO feedback (time, day, sender, rating, page, question, answer, sources, note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now.isoformat(timespec="seconds"), day, sender, rating, fields["page"], fields["question"],
                     fields["answer"], json.dumps(sources), fields["note"]),
                )
                db.execute("COMMIT")
            finally:
                db.close()

    def recent(self, days: int = 7) -> list[dict]:
        since = (date.today() - timedelta(days=max(days, 1) - 1)).isoformat()
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            rows = db.execute(
                "SELECT time, rating, page, question, answer, sources, note FROM feedback WHERE day >= ? ORDER BY id DESC",
                (since,),
            ).fetchall()
        keys = ["time", "rating", "page", "question", "answer", "sources", "note"]
        items = [dict(zip(keys, row)) for row in rows]
        for item in items:
            item["sources"] = json.loads(item["sources"] or "[]")
        return items
