"""Eval: python -m copilot.coach.eval

Runs the coach on synthetic job searches with planted patterns (scenarios.py), auto-approving the plan.

Metrics
  stage_correct       diagnosed bottleneck == planted bottleneck
  weakest_correct     weakest segment names the planted weak portal / role family (when one is planted)
  gap_found           the top planted skill gap appears in the diagnosis
  plan_passes_critic  final plan has no critic issues (feasible hours, addresses bottleneck, targets gaps)
  first_plan_passes   no revision needed (quality of the planner prompt)
  no_llm_when_thin    'too little data' answered with zero analyst tool calls (cost + honesty guard)
"""
from __future__ import annotations

import argparse

from copilot.coach.graph import Coach
from copilot.coach.scenarios import SCENARIOS, build
from copilot.config import load_profile
from core.evals import run_eval
from core.llm import LLM


def run_scenario(name: str, llm: LLM) -> dict:
    con = build(name)
    coach = Coach(con, load_profile(), llm)
    out = coach.start(thread_id=name, goal="Land a data/analytics role in Hyderabad or remote", hours_per_week=8)
    attempts_before_approval = out["state"].get("attempts", 0)
    if out["waiting_for_approval"]:
        out = coach.resume(name, approved=True)
    s = out["state"]
    return {"diagnosis": s["diagnosis"], "plan": s["plan"], "issues": s.get("issues", []),
            "attempts": attempts_before_approval, "analyst_tool_calls": s.get("analyst_tool_calls", 0),
            "saved": bool(s.get("plan_id"))}


def score(case: dict, out: dict) -> dict[str, float]:
    spec = SCENARIOS[case["id"]]
    d = out["diagnosis"]
    s = {
        "stage_correct": float(d["stalls_at"] == spec["expected_stage"]),
        "plan_passes_critic": float(not out["issues"]),
        "first_plan_passes": float(out["attempts"] == 1 and not out["issues"]),
    }
    if spec["expected_weakest"]:
        s["weakest_correct"] = float(spec["expected_weakest"] in (d.get("weakest_segment") or "").lower())
    if spec["expected_gap"]:
        s["gap_found"] = float(spec["expected_gap"] in [g.lower() for g in d.get("top_skill_gaps", [])])
    if spec["expected_stage"] == "insufficient_data":
        s["no_llm_when_thin"] = float(out["analyst_tool_calls"] == 0)
    return s


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    llm = LLM(max_calls=300)
    run_eval("coach", [{"id": n} for n in SCENARIOS], lambda c: run_scenario(c["id"], llm), score,
             workers=2, usage=llm.usage.summary)


if __name__ == "__main__":
    main()
