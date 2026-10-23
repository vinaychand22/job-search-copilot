"""Eval: python -m copilot.tracker.eval [--data your_labelled_emails.jsonl]

Metrics
  category_accuracy   correct category (drives every status update)
  status_accuracy     category maps to the right tracker status - what the dashboard shows
  company_accuracy    hiring company matches (normalised), for emails that have one
  action_accuracy     action_required flag correct (missed assessments are the costliest error)
  deadline_exact      deadline date exactly right, when the gold label has one
  rule_coverage       share of emails handled by rules, i.e. with zero tokens spent

The bundled set is synthetic. Your real number comes from labelling ~50 of your own
emails in the same format (see README) and running with --data.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from copilot.db import company_key
from copilot.tracker.classifier import CATEGORY_STATUS, classify
from copilot.tracker.sources import Email, clean_body
from core.evals import load_jsonl, run_eval
from core.llm import FAST_MODEL, LLM

DATA = Path(__file__).parent / "data" / "emails_golden.jsonl"


def score(case: dict, out: tuple) -> dict[str, float]:
    tri, method = out
    exp = case["expected"]
    s = {
        "category_accuracy": float(tri.category == exp["category"]),
        "status_accuracy": float(CATEGORY_STATUS[tri.category] == CATEGORY_STATUS[exp["category"]]),
        "action_accuracy": float(tri.action_required == exp["action_required"]),
        "rule_coverage": float(method == "rule"),
    }
    if exp.get("company"):
        s["company_accuracy"] = float(bool(tri.company) and company_key(tri.company) == company_key(exp["company"]))
    if exp.get("deadline"):
        s["deadline_exact"] = float(tri.deadline == exp["deadline"])
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--model", default=FAST_MODEL)
    a = ap.parse_args()
    llm = LLM(max_calls=500)

    def run_case(c: dict):
        e = Email(c["message_id"], c["sender"], c["subject"], c["received_at"], clean_body(c["body"]))
        return classify(e, llm, model=a.model)

    run_eval("tracker", load_jsonl(a.data), run_case, score, usage=llm.usage.summary,
             config={"model": a.model, "data": a.data})


if __name__ == "__main__":
    main()
