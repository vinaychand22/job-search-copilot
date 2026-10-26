"""Today's shortlist: the best new jobs, and what you decided about them."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from copilot import db
from copilot.config import Profile


def shortlist(con: sqlite3.Connection, profile: Profile, days: int = 7, limit: int = 25) -> list[dict]:
    """New (undecided) jobs above your minimum score, best first, from the last `days` days."""
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    rows = con.execute(
        "SELECT id, title, company, portal, url, score, reasons_json, received_at, origin FROM postings "
        "WHERE status = 'new' AND score >= ? AND COALESCE(received_at, created_at) >= ? "
        "ORDER BY score DESC, received_at DESC LIMIT ?", (profile.min_score, since, limit)).fetchall()
    out = []
    for r in rows:
        reasons = json.loads(r["reasons_json"] or "{}")
        out.append({"id": r["id"], "score": r["score"], "title": r["title"], "company": r["company"] or "?",
                    "portal": r["portal"] or "", "url": r["url"], "received": (r["received_at"] or "")[:10],
                    "matched": ", ".join(reasons.get("matched_skills", [])),
                    "missing": ", ".join(reasons.get("missing_skills", [])),
                    "why": "; ".join(reasons.get("reasons", []))})
    return out


def decide(con: sqlite3.Connection, posting_id: int, decision: str) -> None:
    """'applied' also starts tracking the application; 'dismissed' hides the job for good."""
    if decision not in {"applied", "dismissed", "new"}:
        raise ValueError(decision)
    row = con.execute("SELECT title, company, portal FROM postings WHERE id = ?", (posting_id,)).fetchone()
    if row is None:
        raise ValueError(f"no posting {posting_id}")
    con.execute("UPDATE postings SET status = ? WHERE id = ?", (decision, posting_id))
    con.commit()
    if decision == "applied" and row["company"]:
        db.upsert_application(con, company=row["company"], role=row["title"] or "", status="applied",
                              source=row["portal"], summary="applied from the copilot shortlist")


def to_markdown(items: list[dict], when: str) -> str:
    lines = [f"# Job shortlist - {when}", "", f"{len(items)} new jobs above your minimum score.", ""]
    for i, j in enumerate(items, 1):
        link = f"[apply]({j['url']})" if j["url"] else "(no link)"
        lines.append(f"{i}. **{j['score']:.0f}** {j['title']} @ {j['company']} ({j['portal']}) {link}")
        if j["missing"]:
            lines.append(f"   - missing: {j['missing']}")
    return "\n".join(lines) + "\n"
