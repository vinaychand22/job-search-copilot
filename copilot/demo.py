"""Build a demo database from the bundled labelled samples - no API key, no Gmail.

    python -m copilot.demo
    COPILOT_DB=data/demo.db streamlit run app.py

Emails are applied using their gold labels and postings use their gold fields, so the
dashboard shows exactly what a perfect classifier/extractor would produce.
"""
from __future__ import annotations

import json
from pathlib import Path

from copilot import db
from copilot.config import ROOT, load_profile
from copilot.matcher.extractor import content_hash
from copilot.matcher.schema import JobPosting
from copilot.matcher.scoring import score_posting
from copilot.tracker.classifier import CATEGORY_STATUS, detect_portal
from core.evals import load_jsonl

DEMO_DB = ROOT / "data" / "demo.db"
HERE = Path(__file__).parent


def build(path: Path = DEMO_DB) -> Path:
    path.unlink(missing_ok=True)
    con = db.connect(path)
    for e in sorted(load_jsonl(HERE / "tracker/data/emails_golden.jsonl"), key=lambda x: x["received_at"]):
        exp = e["expected"]
        status = CATEGORY_STATUS[exp["category"]]
        app_id = None
        if status and exp.get("company"):
            app_id, _ = db.upsert_application(
                con, company=exp["company"], status=status, source=detect_portal(e["sender"], e["body"]),
                when=e["received_at"], deadline=exp.get("deadline"), message_id=e["message_id"],
                next_action=e["subject"] if exp["action_required"] else None, summary=e["subject"])
        con.execute("INSERT INTO emails VALUES (?,?,?,?,?,?,?,?)", (e["message_id"], e["sender"], e["subject"],
                    e["received_at"], exp["category"], "gold", app_id, db.now()))
    profile = load_profile()
    for c in load_jsonl(HERE / "matcher/data/postings_golden.jsonl"):
        job = JobPosting.model_validate(c["expected"])
        fit = score_posting(job, profile)
        db.save_posting(con, content_hash(c["text"]), c["id"], job.model_dump(), fit.score, fit.as_dict())
    # A sample coach plan so the "Coach plan" tab has something to show (normally produced by
    # `python -m copilot.coach.run` and saved only after you approve it).
    diag = {"findings": ["Demo data: 8 applications tracked, 0.5 response rate"], "stalls_at": "interview",
            "strongest_segment": None, "weakest_segment": None, "top_skill_gaps": ["power bi"]}
    plan = {"focus_roles": ["data analyst"], "focus_sources": [], "applications_target": 10,
            "learning": [{"skill": "power bi", "hours": 5, "why": "most-requested gap",
                          "deliverable": "Sales dashboard published to GitHub"}],
            "actions": ["Two mock interviews walking through the analytics project"],
            "rationale": "Demo plan: responses are fine, interviews are where applications stall."}
    cur = con.execute("INSERT INTO plans (created_at, goal, diagnosis_json, plan_json) VALUES (?,?,?,?)",
                      (db.now(), "demo", json.dumps(diag), json.dumps(plan)))
    con.executemany("INSERT INTO plan_items (plan_id, kind, description, hours) VALUES (?,?,?,?)",
                    [(cur.lastrowid, "applications", "Send 10 tailored applications (data analyst)", None),
                     (cur.lastrowid, "learning", "power bi: Sales dashboard published to GitHub", 5),
                     (cur.lastrowid, "action", plan["actions"][0], None)])
    con.commit()
    return path


if __name__ == "__main__":
    print(f"demo database written to {build()}")
    print("open it with:  COPILOT_DB=data/demo.db streamlit run app.py")
