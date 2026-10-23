"""Loads config/profile.toml (falls back to the example so the repo runs out of the box)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@dataclass(frozen=True)
class Profile:
    name: str
    years_experience: float
    skills: frozenset[str]
    target_roles: tuple[str, ...]
    locations: tuple[str, ...]
    accept_remote: bool
    min_salary_inr: int
    max_years_required: int
    dealbreaker_words: tuple[str, ...]
    gmail_query: str
    follow_up_after_days: int
    folder_status: dict[str, str] = field(default_factory=dict)


def load_profile(path: str | Path | None = None) -> Profile:
    if path is None:
        path = CONFIG_DIR / "profile.toml"
        if not path.exists():
            path = CONFIG_DIR / "profile.example.toml"
    with open(path, "rb") as f:
        c = tomllib.load(f)
    cand, tgt, trk = c["candidate"], c["targets"], c["tracker"]
    return Profile(
        name=cand["name"],
        years_experience=float(cand["years_experience"]),
        skills=frozenset(s.lower() for s in cand["skills"]),
        target_roles=tuple(r.lower() for r in tgt["roles"]),
        locations=tuple(l.lower() for l in tgt["locations"]),
        accept_remote=bool(tgt.get("accept_remote", True)),
        min_salary_inr=int(tgt.get("min_salary_inr_per_year", 0)),
        max_years_required=int(tgt.get("max_years_required", 99)),
        dealbreaker_words=tuple(w.lower() for w in c.get("dealbreakers", {}).get("title_words", [])),
        gmail_query=trk.get("gmail_query", "newer_than:30d"),
        follow_up_after_days=int(trk.get("follow_up_after_days", 14)),
        folder_status={k.lower(): v for k, v in trk.get("folder_status", {}).items()},
    )
