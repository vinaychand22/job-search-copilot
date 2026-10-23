"""Synthetic job searches with a known, planted pattern - the coach's eval cases.

A real job search can't tell you the 'right' diagnosis. A synthetic one can: we plant
"Naukri barely responds" or "you fail assessments" and check the analyst finds it.
"""
from __future__ import annotations

import random
import sqlite3

from copilot import db
from copilot.matcher.schema import JobPosting

ROLES = {"data analyst": "Data Analyst", "business analyst": "Business Analyst",
         "analytics engineer": "Analytics Engineer", "data scientist": "Data Scientist"}


def _apps(con, rng, n, source, role, p_resp, p_int, p_offer=0.0, start=0):
    for i in range(n):
        company = f"{source[:3]}{role[:4]}Co{start + i}"
        day = 1 + (i % 27)
        db.upsert_application(con, company=company, role=role, status="applied", source=source,
                              when=f"2026-08-{day:02d}T10:00:00")
        if rng.random() < p_resp:
            db.upsert_application(con, company=company, role=role, status="assessment",
                                  when=f"2026-09-{day:02d}T10:00:00")
            if rng.random() < p_int:
                db.upsert_application(con, company=company, role=role, status="interview",
                                      when=f"2026-09-{day:02d}T15:00:00")
                if rng.random() < p_offer:
                    db.upsert_application(con, company=company, role=role, status="offer",
                                          when=f"2026-10-{day:02d}T10:00:00")
                    continue
        db.upsert_application(con, company=company, role=role, status="rejected", when=f"2026-10-{day:02d}T12:00:00")


def _postings(con, skills_share: dict[str, float], n=20, seed=0):
    rng = random.Random(seed)
    for i in range(n):
        req = [s for s, p in skills_share.items() if rng.random() < p] or ["sql"]
        job = JobPosting(title="Data Analyst", required_skills=req, location="Hyderabad", work_mode="hybrid")
        db.save_posting(con, f"h{seed}-{i}", "scenario", job.model_dump(), 0, {})


GAP_SKILLS = {"sql": 0.9, "power bi": 0.75, "python": 0.6, "tableau": 0.4, "dbt": 0.15}

SCENARIOS = {
    # Naukri: many applications, almost no replies. LinkedIn: fewer but much better.
    "weak_portal": dict(expected_stage="response", expected_weakest="naukri", expected_gap="power bi",
                        build=lambda con, rng: (_apps(con, rng, 24, "Naukri", ROLES["data analyst"], 0.04, 0.5),
                                                _apps(con, rng, 10, "LinkedIn", ROLES["data analyst"], 0.5, 0.6, start=100))),
    # Plenty of replies, but assessments rarely turn into interviews.
    "fails_assessments": dict(expected_stage="assessment", expected_weakest=None, expected_gap="power bi",
                              build=lambda con, rng: _apps(con, rng, 25, "LinkedIn", ROLES["data analyst"], 0.6, 0.07)),
    # Analytics-engineer roles convert, data-scientist roles don't.
    "wrong_role_family": dict(expected_stage="response", expected_weakest="data scientist", expected_gap="power bi",
                              build=lambda con, rng: (_apps(con, rng, 15, "LinkedIn", ROLES["data scientist"], 0.0, 0.0),
                                                      _apps(con, rng, 12, "LinkedIn", ROLES["analytics engineer"], 0.5, 0.9, start=200))),
    # Too little data: must NOT produce confident conclusions (and costs zero analyst calls).
    "too_little_data": dict(expected_stage="insufficient_data", expected_weakest=None, expected_gap=None,
                            build=lambda con, rng: _apps(con, rng, 5, "Naukri", ROLES["data analyst"], 0.2, 0.5)),
}


def build(name: str, seed: int = 7) -> sqlite3.Connection:
    con = db.connect(":memory:")
    SCENARIOS[name]["build"](con, random.Random(seed))
    _postings(con, GAP_SKILLS, seed=seed)
    return con
