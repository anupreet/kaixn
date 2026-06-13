"""Waitlist capture for the marketing site.

The public marketing site (kaixn.com / kaixn.webflow.io) posts signups here; the
form action points at app.kaixn.com so capture lives in our own backend rather
than a third party. Storage mirrors the rest of the app's two-mode story:

    KAIXN_DSN set  -> append to a Postgres `waitlist` table (created on demand,
                      independent of the norm schema / migrations)
    unset          -> append to a JSONL file (KAIXN_WAITLIST_FILE, default
                      ./waitlist.jsonl) so local/offline runs still capture

Append-only and idempotent on email: a repeat signup is accepted, not an error.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import threading

# Deliberately permissive — enough to reject obvious junk without policing valid
# but unusual addresses. Real verification happens via the confirmation email.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_file_lock = threading.Lock()


def valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip())) and len(email) <= 320


def _file_path() -> pathlib.Path:
    return pathlib.Path(os.getenv("KAIXN_WAITLIST_FILE", "waitlist.jsonl"))


def _add_file(email: str, source: str, ts: str) -> None:
    line = json.dumps({"email": email, "source": source, "created_at": ts})
    with _file_lock:
        with _file_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _add_pg(dsn: str, email: str, source: str) -> None:
    from kaixn.store import pg_connect

    conn = pg_connect(dsn)
    # Self-contained table — no migration coupling to the norm schema.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS waitlist (
            id         bigserial PRIMARY KEY,
            email      text NOT NULL,
            source     text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (email)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO waitlist (email, source) VALUES (%s, %s)
        ON CONFLICT (email) DO NOTHING
        """,
        (email, source),
    )


def add(email: str, *, source: str = "", now: str | None = None) -> dict:
    """Capture a signup. Returns {ok, email}. Raises ValueError on a bad email."""
    email = email.strip().lower()
    if not valid_email(email):
        raise ValueError("a valid email address is required")

    dsn = os.getenv("KAIXN_DSN")
    if dsn:
        _add_pg(dsn, email, source)
    else:
        from datetime import datetime, timezone

        _add_file(email, source, now or datetime.now(timezone.utc).isoformat())
    return {"ok": True, "email": email}
