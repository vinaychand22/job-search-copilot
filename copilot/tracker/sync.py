"""The tracker pipeline: inbox -> classified events -> up-to-date applications.

    fetch new emails (skip ids already in the DB)
      -> classify (rules, then model)
      -> map to an application (fuzzy company match) and advance its status
      -> record the email so it is never processed twice

Idempotent: running sync twice changes nothing the second time and costs zero tokens.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from copilot import db
from copilot.config import Profile
from copilot.tracker.classifier import CATEGORY_STATUS, classify, detect_portal
from copilot.tracker.sources import Email
from core.llm import LLM, BudgetExceeded

ALERT_MAX_AGE_DAYS = 7   # older alerts are recorded but not parsed: those jobs are likely filled


@dataclass
class SyncReport:
    processed: int = 0
    skipped_seen: int = 0
    by_method: Counter = field(default_factory=Counter)
    by_category: Counter = field(default_factory=Counter)
    status_changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    alerts_parsed: int = 0
    jobs_found: int = 0
    new_jobs: int = 0
    budget_stopped: bool = False   # True if the daily request budget ran out; the rest waits for next run


def _recent(received_at: str, days: int) -> bool:
    try:
        return dt.datetime.fromisoformat(received_at) >= dt.datetime.now() - dt.timedelta(days=days)
    except ValueError:
        return True


def sync(con: sqlite3.Connection, emails: Iterable[Email], llm: LLM,
         profile: Profile | None = None) -> SyncReport:
    """Classify new emails, update applications, and (with a profile) turn job alerts into jobs."""
    t0 = time.perf_counter()
    rep = SyncReport()
    seen = {r[0] for r in con.execute("SELECT message_id FROM emails")}
    for e in emails:
        if e.message_id in seen:
            rep.skipped_seen += 1
            continue
        try:
            tri, method = classify(e, llm)
            if tri.category == "job_alert" and profile is not None and _recent(e.received_at, ALERT_MAX_AGE_DAYS):
                from copilot.feed.alerts import ingest_alert  # local import: feed depends on tracker
                found, new = ingest_alert(con, e, llm, profile)
                rep.alerts_parsed += 1
                rep.jobs_found += found
                rep.new_jobs += new
        except BudgetExceeded:   # out of requests for this run: stop cleanly, unprocessed mail waits
            rep.budget_stopped = True
            break
        except Exception as ex:  # one bad email must not stop the sync; it is retried next run
            rep.errors.append(f"{e.subject[:60]}: {type(ex).__name__}: {ex}")
            continue
        app_id = None
        status = CATEGORY_STATUS[tri.category]
        if status and tri.company:
            next_action = tri.summary if tri.action_required else None
            app_id, changed = db.upsert_application(
                con, company=tri.company, role=tri.role or "", status=status,
                source=detect_portal(e.sender, e.body), when=e.received_at, next_action=next_action,
                deadline=tri.deadline, message_id=e.message_id, summary=tri.summary)
            if changed:
                rep.status_changes.append(f"{tri.company}: {status}")
        con.execute("INSERT INTO emails VALUES (?,?,?,?,?,?,?,?)",
                    (e.message_id, e.sender, e.subject, e.received_at, tri.category, method, app_id, db.now()))
        con.commit()
        seen.add(e.message_id)
        rep.processed += 1
        rep.by_method[method] += 1
        rep.by_category[tri.category] += 1
    rep.seconds = round(time.perf_counter() - t0, 2)
    return rep


def action_items(con: sqlite3.Connection, follow_up_after_days: int = 14,
                 today: dt.date | None = None) -> dict[str, list[dict]]:
    """Upcoming deadlines and stale applications worth a follow-up."""
    today = today or dt.date.today()
    deadlines = [dict(r) for r in con.execute(
        "SELECT company, role, status, next_action, deadline FROM applications "
        "WHERE deadline IS NOT NULL AND deadline >= ? AND status NOT IN ('rejected','withdrawn','offer') "
        "ORDER BY deadline", (today.isoformat(),))]
    cutoff = (today - dt.timedelta(days=follow_up_after_days)).isoformat()
    stale = [dict(r) for r in con.execute(
        "SELECT company, role, status, last_update FROM applications "
        "WHERE status IN ('applied','assessment','interview') AND last_update < ? ORDER BY last_update",
        (cutoff,))]
    return {"deadlines": deadlines, "follow_ups": stale}
