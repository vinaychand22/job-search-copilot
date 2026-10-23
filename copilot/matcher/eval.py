"""Eval: python -m copilot.matcher.eval [--offline]

Two questions, measured separately so you know which part to fix:
  1. Extraction - does the model read postings correctly?   (field_accuracy, skills_f1, salary_exact)
  2. Ranking    - does the score put relevant jobs on top?   (precision@5, recall@5)

--offline scores the GOLD extracted fields, so it measures the ranking rule alone,
costs nothing, and runs in CI. Without --offline it is end-to-end (model extraction + ranking).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from copilot.config import load_profile
from copilot.matcher.extractor import extract
from copilot.matcher.schema import JobPosting, canonical_skill
from copilot.matcher.scoring import score_posting
from core.evals import load_jsonl, run_eval
from core.llm import FAST_MODEL, LLM

DATA = Path(__file__).parent / "data" / "postings_golden.jsonl"
PROFILE = Path(__file__).parent / "data" / "eval_profile.toml"
SCALARS = ["title", "company", "location", "work_mode", "seniority", "min_years_experience",
           "salary_min", "salary_max", "salary_currency", "salary_period"]


def set_f1(pred: set[str], gold: set[str]) -> float:
    if not pred and not gold:
        return 1.0
    tp = len(pred & gold)
    if tp == 0:
        return 0.0
    p, r = tp / len(pred), tp / len(gold)
    return 2 * p * r / (p + r)


def _norm(v):
    return " ".join(v.lower().split()) if isinstance(v, str) else v


def extraction_score(case: dict, job: JobPosting) -> dict[str, float]:
    gold, pred = case["expected"], job.model_dump()
    sal = ["salary_min", "salary_max", "salary_currency", "salary_period"]
    return {
        "field_accuracy": sum(_norm(pred[f]) == _norm(gold[f]) for f in SCALARS) / len(SCALARS),
        "skills_f1": set_f1(set(pred["required_skills"]), {canonical_skill(s) for s in gold["required_skills"]}),
        "salary_exact": float(all(pred[f] == gold[f] for f in sal)),
    }


def ranking_metrics(cases: list[dict], jobs: list[JobPosting], k: int = 5) -> dict[str, float]:
    profile = load_profile(PROFILE)
    scored = sorted(zip(cases, jobs), key=lambda cj: score_posting(cj[1], profile).score, reverse=True)
    top = [c["relevant"] for c, _ in scored[:k]]
    n_rel = sum(c["relevant"] for c in cases)
    return {f"precision@{k}": round(sum(top) / k, 3), f"recall@{k}": round(sum(top) / n_rel, 3) if n_rel else 0.0,
            "ranking": [(c["id"], round(score_posting(j, profile).score, 1), c["relevant"]) for c, j in scored]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="rank gold fields only (no API calls)")
    ap.add_argument("--model", default=FAST_MODEL)
    a = ap.parse_args()
    cases = load_jsonl(DATA)

    if a.offline:
        jobs = [JobPosting.model_validate(c["expected"]) for c in cases]
    else:
        llm = LLM(max_calls=200)
        report = run_eval("matcher_extraction", cases, lambda c: extract(llm, c["text"], a.model),
                          extraction_score, usage=llm.usage.summary, config={"model": a.model})
        jobs = [JobPosting.model_validate(r["output"]) if r["output"] else JobPosting(title="")
                for r in report["cases"]]
    rank = ranking_metrics(cases, jobs)
    print("\n=== ranking ({}) ===".format("gold fields" if a.offline else "end-to-end"))
    for k, v in rank.items():
        if k != "ranking":
            print(f"  {k:<14} {v}")
    print("  order (id, score, relevant):", rank["ranking"])
    out = Path("evals/results") / f"matcher_ranking-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"mode": "offline" if a.offline else "end_to_end", **rank}, indent=2))
    print(f"  saved -> {out}")


if __name__ == "__main__":
    main()
