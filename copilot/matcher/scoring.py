"""Fit score 0-100 between a JobPosting and your profile - deterministic, explainable code.

Why not ask the LLM "how good a fit is this, 1-10"?
  * Its scores drift between runs and between prompt tweaks, so rankings aren't stable.
  * You can't explain them ("the model said 7").
  * It costs tokens per posting per profile change; this costs nothing to re-run.
The LLM's job is reading messy text into fields (extractor.py). Judging fit is a rule
you own, can tune, and can defend in an interview line by line.

Weights (sum 100):  required skills 45 · role/title 20 · experience 15 · location 15 · nice-to-have 5
Hard filters (score 0): dealbreaker title words, stated salary below your minimum.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from copilot.config import Profile
from copilot.matcher.schema import JobPosting

WEIGHTS = {"skills": 45, "role": 20, "experience": 15, "location": 15, "nice_to_have": 5}


@dataclass
class FitScore:
    score: float
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    excluded: str | None = None

    def as_dict(self) -> dict:
        return {"score": self.score, "matched_skills": self.matched_skills, "missing_skills": self.missing_skills,
                "reasons": self.reasons, "excluded": self.excluded}


def _has_skill(skill: str, skills: frozenset[str]) -> bool:
    # "power bi" matches "power bi"; "sql" counts for "advanced sql" / "mysql"-style variants
    return skill in skills or any(skill.endswith(" " + s) or s == skill.split()[-1] for s in skills if len(s) > 2)


def annual_inr(job: JobPosting) -> int | None:
    if job.salary_currency != "INR" or job.salary_max is None:
        return None
    return {"year": 1, "month": 12, "hour": 2080}.get(job.salary_period or "year", 1) * job.salary_max


def score_posting(job: JobPosting, p: Profile) -> FitScore:
    title = job.title.lower()
    for w in p.dealbreaker_words:
        if w in title:
            return FitScore(0.0, excluded=f"dealbreaker word in title: '{w}'")
    pay = annual_inr(job)
    if pay is not None and pay < p.min_salary_inr:
        return FitScore(0.0, excluded=f"max salary INR {pay:,} below your minimum {p.min_salary_inr:,}")

    reasons: list[str] = []
    req = job.required_skills
    matched = [s for s in req if _has_skill(s, p.skills)]
    missing = [s for s in req if s not in matched]
    skill_part = len(matched) / len(req) if req else 0.5  # no skills listed: neutral, not perfect
    reasons.append(f"skills {len(matched)}/{len(req)}" if req else "no required skills listed")

    role_part = 1.0 if any(r in title for r in p.target_roles) else \
        0.5 if any(w in title for r in p.target_roles for w in r.split() if len(w) > 3) else 0.0
    reasons.append({1.0: "target role", 0.5: "related title", 0.0: "title outside targets"}[role_part])

    if job.min_years_experience is None:
        exp_part = 0.7
    elif job.min_years_experience <= p.years_experience + 0.5:
        exp_part = 1.0
    elif job.min_years_experience <= p.max_years_required:
        exp_part = 0.5
        reasons.append(f"asks {job.min_years_experience}+ yrs (stretch)")
    else:
        exp_part = 0.0
        reasons.append(f"asks {job.min_years_experience}+ yrs")

    loc = (job.location or "").lower()
    if job.work_mode == "remote" and p.accept_remote:
        loc_part = 1.0
    elif any(l in loc for l in p.locations):
        loc_part = 1.0
    elif not loc:
        loc_part = 0.5
    else:
        loc_part = 0.0
        reasons.append(f"location {job.location}")

    nice = job.nice_to_have_skills
    nice_part = sum(_has_skill(s, p.skills) for s in nice) / len(nice) if nice else 0.5

    parts = {"skills": skill_part, "role": role_part, "experience": exp_part,
             "location": loc_part, "nice_to_have": nice_part}
    total = round(sum(WEIGHTS[k] * v for k, v in parts.items()), 1)
    return FitScore(total, matched, missing, reasons)
