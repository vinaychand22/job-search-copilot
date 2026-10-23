"""CLI: python -m copilot.coach.run --hours 8 --goal "Land a data analyst role in Hyderabad"

Diagnoses your search from your own data, proposes next week's plan, and waits for your
approval (approve / give feedback / reject) before saving anything.
"""
from __future__ import annotations

import argparse
import json
import uuid

from copilot import db
from copilot.coach.graph import Coach
from copilot.config import load_profile
from core.llm import LLM
from core.tracing import Tracer


def show(payload: dict) -> None:
    d, p = payload["diagnosis"], payload["plan"]
    print("\n=== Diagnosis ===")
    for f in d["findings"]:
        print(f"  - {f}")
    print(f"  bottleneck: {d['stalls_at']} | strongest: {d.get('strongest_segment')} | "
          f"weakest: {d.get('weakest_segment')} | skill gaps: {d.get('top_skill_gaps')}")
    print("\n=== Proposed plan for next week ===")
    print(f"  focus roles: {p['focus_roles']}  portals: {p['focus_sources']}")
    print(f"  applications: {p['applications_target']}")
    for l in p["learning"]:
        print(f"  learn {l['skill']} ({l['hours']}h): {l['deliverable']}  - {l['why']}")
    for a in p["actions"]:
        print(f"  action: {a}")
    print(f"  why: {p['rationale']}")
    if payload["issues"]:
        print(f"\n  critic still flags: {payload['issues']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Career coach: diagnose your search and plan next week")
    ap.add_argument("--goal", default="Land a data/analytics role")
    ap.add_argument("--hours", type=float, default=8, help="learning hours available next week")
    a = ap.parse_args()

    llm = LLM(tracer=Tracer.for_run("coach"), max_calls=40)
    coach = Coach(db.connect(), load_profile(), llm)
    thread = str(uuid.uuid4())
    out = coach.start(thread, a.goal, a.hours)
    while out["waiting_for_approval"]:
        show(out["payload"])
        ans = input("\nApprove? [y = save / n = discard / or type feedback to revise]: ").strip()
        if ans.lower() in {"y", "yes"}:
            out = coach.resume(thread, approved=True)
        elif ans.lower() in {"n", "no", ""}:
            out = coach.resume(thread, approved=False)
        else:
            out = coach.resume(thread, approved=False, feedback=ans)
    pid = out["state"].get("plan_id")
    print(f"\n{'Saved plan #' + str(pid) + ' - see the Coach tab in the dashboard.' if pid else 'Nothing saved.'}")
    print(f"usage: {llm.usage.summary()}")


if __name__ == "__main__":
    main()
