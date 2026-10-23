"""Career coach: a multi-agent LangGraph workflow with a human approval step.

    data_check ──(too little data)──────────────────────────────┐
        │                                                       ▼
        └─► analyst agent ─► planner agent ─► critic ─► human approval ─► save plan
            (tools + SQL,       (structured     (code      (interrupt:        (plans +
             evidence-checked)   WeeklyPlan)     checks)    approve / edit)    plan_items)
                                   ▲              │             │
                                   └── fix ───────┘◄── feedback─┘

Why LangGraph here (and plain code elsewhere): this flow has loops, branching and a pause
for a human that can resume later. LangGraph gives explicit state, conditional edges and a
checkpointer for interrupt/resume, which are exactly those three things.

Agent roles
  analyst  - tool-using agent (hand-written loop in core/agent_loop.py). Fetches its own numbers.
  planner  - turns the diagnosis into a concrete week; structured output.
  critic   - deterministic checks (hours budget, targets, plan addresses the bottleneck).
             Code, not a model: the rules are clear, so a model would only add cost and noise.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from copilot.analytics import funnel
from copilot.coach.schemas import Diagnosis, WeeklyPlan
from copilot.coach.tools import AnalystTools
from copilot.config import Profile
from core.agent_loop import run_agent
from core.llm import FAST_MODEL, LLM, SMART_MODEL
from core.structured import structured

MIN_APPLICATIONS = 10   # below this, conversion rates are noise - don't let a model over-read them
MAX_PLAN_ATTEMPTS = 3

# What a plan must contain for each bottleneck (critic rule; keyword match on actions)
STAGE_ACTIONS = {
    "response": ["resume", "target", "referral", "keyword", "portal", "tailor"],
    "assessment": ["sql", "practice", "case", "test", "assessment", "excel"],
    "interview": ["mock", "interview", "story", "star", "project walkthrough", "behavioural", "behavioral"],
    "offer": ["negotiat", "follow", "reference", "salary"],
    "insufficient_data": ["apply", "applications", "track"],
}

ANALYST_SYSTEM = """You are a data analyst diagnosing a candidate's job search from their own data.
Use the tools to fetch numbers: start with funnel_stats (all, then by source and by role_family),
then skill_gaps. Use run_sql only for questions the funnel can't answer.
Then call submit_diagnosis. Rules:
- Every finding must quote the exact numbers from tool results (counts or rates as returned). Do not compute
  new ratios or invent figures; do not include dates.
- stalls_at = the stage with the largest relative drop-off.
- Segments with fewer than 3 applications are too small to call strongest/weakest.
- top_skill_gaps = skills with you_have=false and the highest share."""

PLANNER_SYSTEM = """You are a pragmatic career coach for an early-career data/AI candidate.
Write next week's plan targeted at the diagnosed bottleneck. Be specific: name portals, role families,
skills and concrete deliverables. Respect the weekly hour budget for learning. Prefer depth over breadth:
at most 2-3 skills. Ground every choice in the diagnosis."""


class CoachState(TypedDict, total=False):
    goal: str
    hours_per_week: float
    diagnosis: dict
    analyst_tool_calls: int
    plan: dict
    issues: list[str]
    attempts: int
    human_feedback: str
    approved: bool
    plan_id: int


class Coach:
    def __init__(self, con: sqlite3.Connection, profile: Profile, llm: LLM,
                 fast: str = FAST_MODEL, smart: str = SMART_MODEL) -> None:
        self.con, self.profile, self.llm, self.fast, self.smart = con, profile, llm, fast, smart
        self.graph = self._build().compile(checkpointer=InMemorySaver())

    # ---------------- nodes ----------------
    def data_check(self, s: CoachState) -> CoachState:
        f = funnel(self.con)
        applied = f[0]["applied"] if f else 0
        if applied >= MIN_APPLICATIONS:
            return {}
        diag = Diagnosis(findings=[f"Only {applied} applications are tracked; at least {MIN_APPLICATIONS} are "
                                   "needed before conversion rates mean anything."],
                         stalls_at="insufficient_data")
        return {"diagnosis": diag.model_dump(), "analyst_tool_calls": 0}

    def analyst(self, s: CoachState) -> CoachState:
        tools = AnalystTools(self.con, self.profile)
        res = run_agent(self.llm, model=self.fast, system=ANALYST_SYSTEM, tools=tools.as_tools(),
                        user=f"Candidate goal: {s['goal']}\nSkills: {sorted(self.profile.skills)}",
                        terminal_tools=frozenset({"submit_diagnosis"}), max_steps=8)
        if tools.diagnosis is None:
            raise RuntimeError(f"analyst did not produce a valid diagnosis ({res.stopped_reason})")
        return {"diagnosis": tools.diagnosis, "analyst_tool_calls": len(res.tool_calls)}

    def planner(self, s: CoachState) -> CoachState:
        feedback = [*s.get("issues", [])]
        if s.get("human_feedback"):
            feedback.append(f"Candidate feedback: {s['human_feedback']}")
        user = (f"Goal: {s['goal']}\nLearning hours available this week: {s['hours_per_week']}\n"
                f"Target roles in profile: {list(self.profile.target_roles)}\n"
                f"<diagnosis>\n{json.dumps(s['diagnosis'], indent=1)}\n</diagnosis>")
        if s.get("plan") and feedback:
            user += f"\n<previous_plan>\n{json.dumps(s['plan'])}\n</previous_plan>\nFix:\n- " + "\n- ".join(feedback)
        plan = structured(self.llm, model=self.smart, system=PLANNER_SYSTEM, user=user, schema=WeeklyPlan,
                          max_tokens=2000, tag="coach_plan")
        return {"plan": plan.model_dump(), "attempts": s.get("attempts", 0) + 1, "human_feedback": ""}

    def critic(self, s: CoachState) -> CoachState:
        return {"issues": check_plan(s["plan"], s["diagnosis"], s["hours_per_week"])}

    def approval(self, s: CoachState) -> CoachState:
        decision = interrupt({"diagnosis": s["diagnosis"], "plan": s["plan"], "issues": s.get("issues", [])})
        return {"approved": bool(decision.get("approved")), "human_feedback": decision.get("feedback", "")}

    def save(self, s: CoachState) -> CoachState:
        cur = self.con.execute("INSERT INTO plans (created_at, goal, diagnosis_json, plan_json) VALUES (?,?,?,?)",
                               (dt.datetime.now().isoformat(timespec="seconds"), s["goal"],
                                json.dumps(s["diagnosis"]), json.dumps(s["plan"])))
        pid = cur.lastrowid
        p = s["plan"]
        items = [("applications", f"Send {p['applications_target']} tailored applications "
                                  f"({', '.join(p['focus_roles'])})", None)]
        items += [("learning", f"{l['skill']}: {l['deliverable']}", l["hours"]) for l in p["learning"]]
        items += [("action", a, None) for a in p["actions"]]
        self.con.executemany("INSERT INTO plan_items (plan_id, kind, description, hours) VALUES (?,?,?,?)",
                             [(pid, k, d, h) for k, d, h in items])
        self.con.execute("UPDATE plans SET status = 'superseded' WHERE id != ? AND status = 'active'", (pid,))
        self.con.commit()
        return {"plan_id": pid}

    # ---------------- routing ----------------
    @staticmethod
    def after_check(s: CoachState) -> str:
        return "planner" if s.get("diagnosis") else "analyst"

    @staticmethod
    def after_critic(s: CoachState) -> str:
        return "planner" if s["issues"] and s["attempts"] < MAX_PLAN_ATTEMPTS else "approval"

    @staticmethod
    def after_approval(s: CoachState) -> str:
        if s["approved"]:
            return "save"
        return "planner" if s.get("human_feedback") and s["attempts"] < MAX_PLAN_ATTEMPTS + 2 else END

    def _build(self) -> StateGraph:
        g = StateGraph(CoachState)
        for name in ("data_check", "analyst", "planner", "critic", "approval", "save"):
            g.add_node(name, getattr(self, name))
        g.add_edge(START, "data_check")
        g.add_conditional_edges("data_check", self.after_check, ["analyst", "planner"])
        g.add_edge("analyst", "planner")
        g.add_edge("planner", "critic")
        g.add_conditional_edges("critic", self.after_critic, ["planner", "approval"])
        g.add_conditional_edges("approval", self.after_approval, ["save", "planner", END])
        g.add_edge("save", END)
        return g

    # ---------------- public API ----------------
    def start(self, thread_id: str, goal: str, hours_per_week: float) -> dict[str, Any]:
        """Runs until the human-approval pause. Returns what to show the candidate."""
        cfg = {"configurable": {"thread_id": thread_id}}
        self.graph.invoke({"goal": goal, "hours_per_week": hours_per_week, "attempts": 0}, cfg)
        return self.pending(thread_id)

    def resume(self, thread_id: str, approved: bool, feedback: str = "") -> dict[str, Any]:
        from langgraph.types import Command
        cfg = {"configurable": {"thread_id": thread_id}}
        self.graph.invoke(Command(resume={"approved": approved, "feedback": feedback}), cfg)
        return self.pending(thread_id)

    def pending(self, thread_id: str) -> dict[str, Any]:
        snap = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        waiting = [i.value for t in snap.tasks for i in t.interrupts]
        return {"waiting_for_approval": bool(waiting), "payload": waiting[0] if waiting else None,
                "state": dict(snap.values)}


def check_plan(plan: dict, diagnosis: dict, hours: float) -> list[str]:
    """The critic. Pure code, so it's free, instant and testable."""
    issues = []
    total = sum(l["hours"] for l in plan["learning"])
    if total > hours:
        issues.append(f"Learning hours total {total:g} exceed the {hours:g} available. Cut or shrink items.")
    stage = diagnosis["stalls_at"]
    words = STAGE_ACTIONS[stage]
    if not any(w in a.lower() for a in plan["actions"] for w in words):
        issues.append(f"The bottleneck is '{stage}' but no action addresses it (expected something about: "
                      f"{', '.join(words[:4])}).")
    gaps = {g.lower() for g in diagnosis.get("top_skill_gaps", [])}
    if gaps and plan["learning"] and not any(l["skill"].lower() in gaps for l in plan["learning"]):
        issues.append(f"No learning item targets the diagnosed skill gaps {sorted(gaps)}.")
    weakest = (diagnosis.get("weakest_segment") or "").lower()
    if weakest and any(weakest == f.lower() for f in plan.get("focus_sources", []) + plan["focus_roles"]):
        issues.append(f"The plan focuses on '{weakest}', the worst-converting segment. Justify or change it.")
    return issues
