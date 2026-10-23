"""Tools the analyst agent can call. All read-only, all return real numbers from your database.

The analyst never "knows" your stats: it must fetch them. submit_diagnosis then rejects any
finding whose numbers didn't appear in a tool result during this run - so the coach cannot
invent a 12% response rate. Same idea as the tracker's validated outputs, applied to an agent.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Literal

from pydantic import BaseModel, Field

from copilot.analytics import funnel
from copilot.config import Profile
from copilot.matcher.market import skill_demand
from copilot.matcher.schema import JobPosting
from core.tools import Tool, ToolError

NUM = re.compile(r"\d+(?:\.\d+)?")
READ_ONLY = re.compile(r"^\s*(select|with)\b", re.I)
SCHEMA_DOC = ("applications(id, company, role, source, status, applied_on, last_update, deadline); "
              "events(application_id, kind, status, occurred_at, summary); "
              "postings(title, company, score, data_json); statuses: saved, applied, assessment, interview, "
              "offer, rejected, withdrawn")


class FunnelArgs(BaseModel):
    group_by: Literal["all", "source", "role_family"] = Field(description="Split the funnel by this dimension")


class SqlArgs(BaseModel):
    query: str = Field(min_length=10, max_length=1500, description=f"One read-only SELECT. Tables: {SCHEMA_DOC}")


class GapArgs(BaseModel):
    top: int = Field(10, ge=1, le=25)


def numbers_in(text: str) -> set[str]:
    """Normalised numbers, so '0.25', '25%' and '25.0' can be matched against each other."""
    out = set()
    for n in NUM.findall(text):
        f = float(n)
        out |= {f"{f:g}", f"{round(f * 100, 1):g}" if f <= 1 else f"{f:g}", f"{round(f, 1):g}", f"{round(f):g}"}
    return out


class AnalystTools:
    """Holds the evidence collected during one run, so the final submission can be checked against it."""

    def __init__(self, con: sqlite3.Connection, profile: Profile) -> None:
        self.con, self.profile = con, profile
        self.evidence: list[str] = []
        self.diagnosis: dict | None = None

    def _record(self, out) -> str:
        text = json.dumps(out, default=str)
        self.evidence.append(text)
        return text

    def funnel_stats(self, group_by: str) -> str:
        rows = funnel(self.con, by=group_by)
        if not rows:
            raise ToolError("No applications in the funnel yet.")
        return self._record(rows)

    def run_sql(self, query: str) -> str:
        if not READ_ONLY.match(query) or ";" in query.strip().rstrip(";"):
            raise ToolError("Only a single SELECT/WITH statement is allowed.")
        self.con.execute("PRAGMA query_only = ON")   # database-level guard, not just a regex
        try:
            cur = self.con.execute(query)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchmany(50)]
        except sqlite3.Error as e:
            raise ToolError(f"SQL error: {e}. Tables: {SCHEMA_DOC}")
        finally:
            self.con.execute("PRAGMA query_only = OFF")
        return self._record({"rows": rows, "truncated_at": 50})

    def skill_gaps(self, top: int) -> str:
        jobs = [JobPosting.model_validate_json(r[0]) for r in self.con.execute("SELECT data_json FROM postings")]
        if not jobs:
            raise ToolError("No postings collected yet; skip skill gaps.")
        demand = skill_demand(jobs, self.profile, top=top)
        return self._record({"postings_analysed": len(jobs), "skills": demand})

    def submit_diagnosis(self, **diag) -> str:
        allowed = set().union(*(numbers_in(e) for e in self.evidence)) if self.evidence else set()
        bad = []
        for f in diag["findings"]:
            nums = NUM.findall(f)
            if not nums:
                bad.append(f"no numbers: '{f[:60]}'")
            bad += [f"'{n}' not in any tool result" for n in nums if not (numbers_in(n) & allowed)]
        if bad:
            raise ToolError("Diagnosis rejected - every finding needs numbers taken from tool results. "
                            f"Problems: {bad[:6]}. Call a tool to get the numbers, then resubmit.")
        self.diagnosis = diag
        return "Diagnosis accepted."

    def as_tools(self) -> list[Tool]:
        from copilot.coach.schemas import Diagnosis
        return [
            Tool("funnel_stats", "Conversion funnel (applied, assessment, interview, offer counts and rates), "
                 "optionally split by portal ('source') or role family.", FunnelArgs, self.funnel_stats),
            Tool("run_sql", "Run one read-only SQL query on the job-search database for anything the "
                 "funnel doesn't cover (e.g. response time, rejections by month).", SqlArgs, self.run_sql),
            Tool("skill_gaps", "Skill demand across collected postings, with whether the candidate has each skill.",
                 GapArgs, self.skill_gaps),
            Tool("submit_diagnosis", "TERMINAL. Submit the diagnosis. Findings must cite numbers from tool results.",
                 Diagnosis, self.submit_diagnosis),
        ]
