"""Skill-demand report across every posting you've collected - analytics on your own job market.

Answers questions like "how many of my target postings ask for Power BI?" and
"which in-demand skills am I missing?", so learning time goes where the demand is.
This is pure aggregation over extracted fields: no model calls.
"""
from __future__ import annotations

from collections import Counter
from statistics import median

from copilot.config import Profile
from copilot.matcher.schema import JobPosting
from copilot.matcher.scoring import _has_skill, annual_inr


def skill_demand(jobs: list[JobPosting], profile: Profile, top: int = 20) -> list[dict]:
    n = len(jobs)
    if n == 0:
        return []
    counts = Counter(s for j in jobs for s in set(j.required_skills))
    return [{"skill": s, "postings": c, "share": round(c / n, 3), "you_have": _has_skill(s, profile.skills)}
            for s, c in counts.most_common(top)]


def market_summary(jobs: list[JobPosting]) -> dict:
    pays = [p for p in (annual_inr(j) for j in jobs) if p]
    modes = Counter(j.work_mode for j in jobs)
    return {
        "postings": len(jobs),
        "with_salary_inr": len(pays),
        "median_max_salary_inr": int(median(pays)) if pays else None,
        "work_mode_share": {k: round(v / len(jobs), 3) for k, v in modes.items()} if jobs else {},
    }


def top_gaps(jobs: list[JobPosting], profile: Profile, k: int = 5) -> list[dict]:
    """In-demand skills you don't have - the input to the learning planner (next project)."""
    return [d for d in skill_demand(jobs, profile, top=50) if not d["you_have"]][:k]
