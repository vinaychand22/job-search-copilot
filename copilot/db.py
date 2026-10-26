"""One SQLite file is the shared memory of every agent in the copilot.

    applications  one row per (company, role) you're pursuing, with its current status
    events        the history behind each status change (which email, when)
    emails        every email already processed, so a sync never re-classifies (saves tokens)
    postings      job posts the matcher extracted and scored
    plans         weekly plans the career coach produced and you approved (+ plan_items)

Why SQLite: zero setup, a single file you own, real SQL for funnel analytics,
and good enough for thousands of rows. Swap for Postgres only if this becomes multi-user.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from copilot.config import ROOT

DEFAULT_DB = Path(os.getenv("COPILOT_DB", ROOT / "data" / "copilot.db"))  # COPILOT_DB=data/demo.db for the demo

# Pipeline order. A status only moves forward, except the terminal outcomes.
STATUS_RANK = {"saved": 0, "applied": 1, "assessment": 2, "interview": 3, "offer": 4}
TERMINAL = {"rejected", "offer", "withdrawn"}
ALL_STATUSES = [*STATUS_RANK, "rejected", "withdrawn"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    company TEXT NOT NULL,
    company_key TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    source TEXT,
    status TEXT NOT NULL,
    applied_on TEXT,
    last_update TEXT NOT NULL,
    next_action TEXT,
    deadline TEXT,
    notes TEXT,
    UNIQUE (company_key, role)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    kind TEXT NOT NULL,
    status TEXT,
    occurred_at TEXT NOT NULL,
    message_id TEXT,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS emails (
    message_id TEXT PRIMARY KEY,
    sender TEXT, subject TEXT, received_at TEXT,
    category TEXT, method TEXT, application_id INTEGER, processed_at TEXT
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    goal TEXT,
    diagnosis_json TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS plan_items (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    kind TEXT NOT NULL,
    description TEXT NOT NULL,
    hours REAL,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS postings (
    id INTEGER PRIMARY KEY,
    content_hash TEXT UNIQUE NOT NULL,
    source TEXT, title TEXT, company TEXT,
    data_json TEXT NOT NULL, score REAL, reasons_json TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL,
    url TEXT, portal TEXT, origin TEXT NOT NULL DEFAULT 'manual', received_at TEXT
);
"""

# Columns added after the first release. connect() adds any that an older database lacks,
# so `git pull` never breaks an existing copilot.db.
MIGRATIONS = {
    "postings": {"url": "TEXT", "portal": "TEXT", "origin": "TEXT NOT NULL DEFAULT 'manual'",
                 "received_at": "TEXT"},
}


def _migrate(con: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    con.commit()

_SUFFIXES = re.compile(
    r"\b(private|pvt|ltd|limited|llp|inc|corp|corporation|technologies|technology|tech|solutions|"
    r"services|software|systems|labs|india|global|group|co|company)\b\.?"
)


def company_key(name: str) -> str:
    """'Quantium Analytics India Pvt. Ltd.' -> 'quantium analytics'. Matching emails to applications
    depends on this: recruiters, portals and you all spell company names differently."""
    s = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    s = _SUFFIXES.sub(" ", s)
    return " ".join(s.split())


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def same_company(a: str, b: str) -> bool:
    """Do two normalised company keys refer to the same employer?

    1. identical                         'brightpath analytics' == 'brightpath analytics'
    2. one's words contain the other's   'brightpath' vs 'brightpath analytics' (same first word)
    3. near-identical spelling (>= 0.9)  'brightpath analytcs' (typo)
    Digits must match exactly: 'Acme 1' and 'Acme 2' are different companies. An earlier version
    used plain fuzzy matching at 0.85 and merged them; the coach's synthetic eval exposed it.
    """
    if a == b:
        return True
    if not a or not b or re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return False
    ta, tb = a.split(), b.split()
    if ta[0] == tb[0] and (set(ta) <= set(tb) or set(tb) <= set(ta)):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


def find_application(con: sqlite3.Connection, company: str, role: str | None = None) -> sqlite3.Row | None:
    """Find the application a message refers to. Among several roles at the same company,
    pick the closest role title."""
    key = company_key(company)
    rows = con.execute("SELECT * FROM applications WHERE company_key = ?", (key,)).fetchall()
    if not rows:
        keys = [r[0] for r in con.execute("SELECT DISTINCT company_key FROM applications")]
        match = next((k for k in keys if same_company(key, k)), None)
        if match:
            rows = con.execute("SELECT * FROM applications WHERE company_key = ?", (match,)).fetchall()
    if not rows:
        return None
    if len(rows) == 1 or not role:
        return max(rows, key=lambda r: r["last_update"])
    return max(rows, key=lambda r: difflib.SequenceMatcher(None, role.lower(), r["role"].lower()).ratio())


def upsert_application(con: sqlite3.Connection, *, company: str, role: str = "", status: str,
                       source: str | None = None, when: str | None = None, next_action: str | None = None,
                       deadline: str | None = None, message_id: str | None = None,
                       summary: str | None = None) -> tuple[int, bool]:
    """Create or advance an application. Returns (application_id, status_changed)."""
    if status not in ALL_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    when = when or now()
    existing = find_application(con, company, role)
    if existing is None:
        cur = con.execute(
            "INSERT INTO applications (company, company_key, role, source, status, applied_on, last_update,"
            " next_action, deadline) VALUES (?,?,?,?,?,?,?,?,?)",
            (company, company_key(company), role or "", source, status,
             when if STATUS_RANK.get(status, 9) >= 1 else None, when, next_action, deadline))
        app_id, changed = cur.lastrowid, True
    else:
        app_id = existing["id"]
        changed = should_advance(existing["status"], status)
        con.execute(
            "UPDATE applications SET status = ?, last_update = ?, next_action = COALESCE(?, next_action),"
            " deadline = COALESCE(?, deadline), source = COALESCE(source, ?),"
            " applied_on = COALESCE(applied_on, ?) WHERE id = ?",
            (status if changed else existing["status"], max(when, existing["last_update"]), next_action,
             deadline, source, when if STATUS_RANK.get(status, 9) >= 1 else None, app_id))
    con.execute("INSERT INTO events (application_id, kind, status, occurred_at, message_id, summary)"
                " VALUES (?,?,?,?,?,?)",
                (app_id, "email" if message_id else "manual", status, when, message_id, summary))
    con.commit()
    return app_id, changed


def should_advance(current: str, new: str) -> bool:
    if current in TERMINAL:
        return False  # a late 'thanks for applying' email must not resurrect a rejection
    if new in TERMINAL:
        return True
    return STATUS_RANK.get(new, -1) > STATUS_RANK.get(current, -1)


def save_posting(con: sqlite3.Connection, content_hash: str, source: str, data: dict[str, Any],
                 score: float, reasons: dict[str, Any], *, url: str | None = None,
                 portal: str | None = None, origin: str = "manual", received_at: str | None = None) -> bool:
    """Insert a posting, or refresh its score if already known. Returns True if it was new.
    An existing posting keeps its status, so a job you dismissed never comes back as 'new'."""
    existed = con.execute("SELECT 1 FROM postings WHERE content_hash = ?", (content_hash,)).fetchone()
    con.execute(
        "INSERT INTO postings (content_hash, source, title, company, data_json, score, reasons_json, created_at,"
        " url, portal, origin, received_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(content_hash) DO UPDATE SET score = excluded.score, reasons_json = excluded.reasons_json,"
        " url = COALESCE(postings.url, excluded.url), portal = COALESCE(postings.portal, excluded.portal)",
        (content_hash, source, data.get("title"), data.get("company"), json.dumps(data), score,
         json.dumps(reasons), now(), url, portal, origin, received_at or now()))
    con.commit()
    return existed is None
