"""Eval: python -m copilot.feed.eval [--data my_alerts.jsonl]

Metrics, per alert email
  job_recall      share of real jobs in the email the reader found (title match)
  job_precision   share of found entries that are real jobs (ads / "view all" must not appear)
  link_accuracy   of jobs found, share with the exact right apply link
  company_accuracy of jobs found, share with the right company
Cost: 1 model request per alert email.

The bundled alerts are synthetic. For your real number, save ~10 of your own alert emails in
the same format (Gmail: "Show original" -> copy the HTML) with their jobs labelled, and pass --data.
"""
from __future__ import annotations

import argparse
import difflib
from pathlib import Path

from copilot.db import company_key
from copilot.feed.alerts import parse_alert
from copilot.tracker.sources import make_email
from core.evals import load_jsonl, run_eval
from core.llm import FAST_MODEL, LLM

DATA = Path(__file__).parent / "data" / "alerts_golden.jsonl"


def _same(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.85


def score(case: dict, out: list) -> dict[str, float]:
    gold = case["expected_jobs"]
    found = [(j.title, j.company, url) for j, url in out]
    matched = [(g, next((f for f in found if _same(f[0], g["title"])), None)) for g in gold]
    hits = [(g, f) for g, f in matched if f]
    real = sum(any(_same(f[0], g["title"]) for g in gold) for f in found)
    return {
        "job_recall": len(hits) / len(gold) if gold else 1.0,
        "job_precision": real / len(found) if found else 0.0,
        "link_accuracy": sum(f[2] == g["url"] for g, f in hits) / len(hits) if hits else 0.0,
        "company_accuracy": sum(bool(f[1]) and company_key(f[1]) == company_key(g["company"])
                                for g, f in hits) / len(hits) if hits else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--model", default=FAST_MODEL)
    a = ap.parse_args()
    llm = LLM(max_calls=50)

    def run_case(c: dict):
        e = make_email(c["message_id"], c["sender"], c["subject"], c["received_at"],
                       c.get("html") or c["body"], bool(c.get("html")))
        return parse_alert(e, llm, model=a.model)

    run_eval("feed", load_jsonl(a.data), run_case, score, workers=1, usage=llm.usage.summary,
             config={"model": a.model, "data": a.data})


if __name__ == "__main__":
    main()
