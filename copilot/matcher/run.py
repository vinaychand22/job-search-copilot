"""CLI
  python -m copilot.matcher.run inbox/postings      # extract + score every .txt posting in the folder
  python -m copilot.matcher.run --report            # skill-demand report from postings already in the DB

Save postings you find on Naukri / LinkedIn / Talent500 as .txt files (copy-paste the
description) into inbox/postings/. Already-processed postings are skipped (content hash),
so re-running costs nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from copilot import db
from copilot.config import load_profile
from copilot.matcher.extractor import content_hash, extract
from copilot.matcher.market import market_summary, skill_demand, top_gaps
from copilot.matcher.schema import JobPosting
from copilot.matcher.scoring import score_posting
from core.llm import LLM
from core.tracing import Tracer


def process_folder(folder: Path, con, llm: LLM, workers: int | None = None) -> list[dict]:
    profile = load_profile()
    # Free tiers allow only a few requests per minute, so default to 2 parallel calls (LLM_WORKERS to change).
    workers = workers or int(os.getenv("LLM_WORKERS", "2"))
    known = {r[0] for r in con.execute("SELECT content_hash FROM postings")}
    todo, skipped = [], 0
    for f in sorted(folder.glob("*.txt")):
        text = f.read_text(encoding="utf-8")
        h = content_hash(text)
        if h in known:
            skipped += 1
        else:
            todo.append((f, text, h))
    print(f"{len(todo)} new postings to process ({skipped} already in the database), "
          f"{workers} at a time...", flush=True)

    def work(item):
        f, text, h = item
        t0 = time.perf_counter()
        try:
            return f, h, extract(llm, text), None, time.perf_counter() - t0
        except Exception as e:
            return f, h, None, e, time.perf_counter() - t0

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:   # extraction is I/O-bound: parallelise
        futures = [pool.submit(work, item) for item in todo]
        for fut in as_completed(futures):                   # report and save each one as it finishes
            f, h, job, err, secs = fut.result()
            done += 1
            if err:
                print(f"  [{done}/{len(todo)}] FAILED {f.name} after {secs:.0f}s: {type(err).__name__}: {err}", flush=True)
                continue
            fit = score_posting(job, profile)
            db.save_posting(con, h, f.name, job.model_dump(), fit.score, fit.as_dict())
            print(f"  [{done}/{len(todo)}] {f.name}: {job.title} @ {job.company or '?'} "
                  f"-> score {fit.score} ({secs:.0f}s)", flush=True)
    return rescore_all(con)


def rescore_all(con) -> list[dict]:
    """Scores are cheap code, so re-score everything whenever the profile changes - no new tokens."""
    profile = load_profile()
    out = []
    for r in con.execute("SELECT id, data_json FROM postings"):
        job = JobPosting.model_validate_json(r["data_json"])
        fit = score_posting(job, profile)
        con.execute("UPDATE postings SET score = ?, reasons_json = ? WHERE id = ?",
                    (fit.score, json.dumps(fit.as_dict()), r["id"]))
        out.append({"title": job.title, "company": job.company, **fit.as_dict()})
    con.commit()
    return sorted(out, key=lambda x: x["score"], reverse=True)


def load_jobs(con) -> list[JobPosting]:
    return [JobPosting.model_validate_json(r[0]) for r in con.execute("SELECT data_json FROM postings")]


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract, score and rank job postings")
    ap.add_argument("folder", nargs="?", type=Path)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--workers", type=int, default=None, help="parallel model calls (default 2, or LLM_WORKERS)")
    a = ap.parse_args()
    con = db.connect()
    profile = load_profile()

    if a.folder:
        llm = LLM(tracer=Tracer.for_run("matcher"), max_calls=500)
        ranked = process_folder(a.folder, con, llm, a.workers)
        print(f"\nTop {a.top} matches:")
        for r in ranked[: a.top]:
            tag = f"EXCLUDED: {r['excluded']}" if r["excluded"] else \
                f"missing: {', '.join(r['missing_skills']) or '-'}"
            print(f"  {r['score']:5.1f}  {r['title']} @ {r['company'] or '?'}  ({tag})")
        print(f"usage: {llm.usage.summary()}")

    if a.report or not a.folder:
        rescore_all(con)  # free: picks up profile changes without any model calls
        jobs = load_jobs(con)
        print("\nMarket:", market_summary(jobs))
        print("\nSkill demand (share of postings):")
        for d in skill_demand(jobs, profile):
            print(f"  {d['skill']:<28} {d['share']:>6.0%}  {'✓' if d['you_have'] else 'gap'}")
        print("\nTop gaps to learn:", [g["skill"] for g in top_gaps(jobs, profile)])


if __name__ == "__main__":
    main()
