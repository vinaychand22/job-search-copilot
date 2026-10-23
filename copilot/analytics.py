"""Funnel analytics on your own job search - the same thinking as a conversion funnel at work.

"Reached a stage" uses the event history, not just the current status: an application
rejected after an interview still counts as having reached the interview stage.
The career coach reasons over these numbers through tools, never from memory.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Literal

from copilot.db import STATUS_RANK

STAGES = ["applied", "assessment", "interview", "offer"]
GroupBy = Literal["all", "source", "role_family"]

ROLE_FAMILIES = [  # first match wins; order from most to least specific
    ("analytics engineer", r"analytics engineer|bi engineer|data engineer"),
    ("data scientist", r"data scien|decision scien|machine learning|ml engineer|applied scien"),
    ("ai / llm", r"\bai\b|llm|genai|gen ai|prompt"),
    ("business analyst", r"business analyst|\bba\b|product analyst|insights|strategy"),
    ("data analyst", r"data analyst|analytics|reporting|mis|bi analyst|power bi|tableau"),
]


def role_family(title: str) -> str:
    t = (title or "").lower()
    return next((fam for fam, pat in ROLE_FAMILIES if re.search(pat, t)), "other")


def _max_rank_per_app(con: sqlite3.Connection) -> list[sqlite3.Row]:
    # A rejection or withdrawal proves you at least applied, so it counts as reaching 'applied'.
    ranks = {**STATUS_RANK, "rejected": STATUS_RANK["applied"], "withdrawn": STATUS_RANK["applied"]}
    case = " ".join(f"WHEN '{s}' THEN {r}" for s, r in ranks.items())
    return con.execute(f"""
        SELECT a.id, COALESCE(a.source, 'Direct / other') AS source, a.role, a.status,
               MAX(CASE e.status {case} ELSE -1 END) AS max_rank
        FROM applications a LEFT JOIN events e ON e.application_id = a.id
        GROUP BY a.id""").fetchall()


def funnel(con: sqlite3.Connection, by_source: bool = False, by: GroupBy | None = None) -> list[dict]:
    by = by or ("source" if by_source else "all")
    groups: dict[str, list[int]] = {}
    for r in _max_rank_per_app(con):
        if r["max_rank"] < STATUS_RANK["applied"]:  # 'saved' leads aren't in the funnel yet
            continue
        key = {"all": "All", "source": r["source"], "role_family": role_family(r["role"])}[by]
        groups.setdefault(key, []).append(r["max_rank"])
    out = []
    for name, ranks in sorted(groups.items()):
        counts = {s: sum(rk >= STATUS_RANK[s] for rk in ranks) for s in STAGES}
        applied = counts["applied"]
        out.append({
            "group": name, **counts,
            "response_rate": round(counts["assessment"] / applied, 3) if applied else 0.0,
            "interview_rate": round(counts["interview"] / applied, 3) if applied else 0.0,
            # of those who got a response, how many converted to an interview
            "assessment_to_interview": round(counts["interview"] / counts["assessment"], 3) if counts["assessment"] else 0.0,
        })
    return out


def status_counts(con: sqlite3.Connection) -> dict[str, int]:
    return {r[0]: r[1] for r in con.execute("SELECT status, COUNT(*) FROM applications GROUP BY status")}
