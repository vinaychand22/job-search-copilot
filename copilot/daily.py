"""The daily run - the one command a scheduler calls every morning.

    python -m copilot.daily                 # Gmail: job alerts first, then company replies
    python -m copilot.daily --budget 15     # cap model requests for this run (free tiers!)
    python -m copilot.daily --demo          # bundled sample alert + emails, no Gmail needed

Steps
  1. Job alerts  (Naukri, LinkedIn, ...)  -> new jobs, scored        [the main value: comes first]
  2. Company replies                      -> application statuses
  3. Re-score every job with your current profile                    [code only, free]
  4. Shortlist -> printed, saved to data/shortlist.md, and on the dashboard
  5. One line appended to data/runs.csv: emails, jobs, requests, seconds (observability)

If the request budget runs out, the run stops cleanly and the remaining emails are picked
up next time - nothing is lost or double-processed.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import time
from pathlib import Path

from copilot import db
from copilot.config import ROOT, load_profile
from copilot.feed.shortlist import shortlist, to_markdown
from copilot.matcher.run import rescore_all
from copilot.tracker.sources import GmailSource, JsonlSource
from copilot.tracker.sync import action_items, sync
from core.llm import FAST_MODEL, LLM
from core.tracing import Tracer

DATA = ROOT / "data"
DEMO_EMAILS = [ROOT / "copilot/feed/data/alerts_golden.jsonl", ROOT / "copilot/tracker/data/emails_golden.jsonl"]


def log_run(row: dict, path: Path | None = None) -> None:
    path = path or DATA / "runs.csv"   # resolved at call time, not import time
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def main() -> None:
    ap = argparse.ArgumentParser(description="Daily run: job alerts -> shortlist, replies -> tracker")
    ap.add_argument("--budget", type=int, default=int(os.getenv("LLM_DAILY_BUDGET", "18")),
                    help="max model requests this run (default 18, under a 20/day free tier)")
    ap.add_argument("--max-emails", type=int, default=100, help="max emails fetched per Gmail query")
    ap.add_argument("--demo", action="store_true", help="use bundled sample emails instead of Gmail")
    a = ap.parse_args()

    if not FAST_MODEL:  # fail once, clearly, instead of once per email
        raise SystemExit("No AI model configured. Set FAST_MODEL and SMART_MODEL in .env, "
                         "then check the connection with: python -m core.check")
    t0 = time.perf_counter()
    profile = load_profile()
    # Demo data goes to its own database so sample emails never mix with your real applications.
    con = db.connect(DATA / "demo-daily.db") if a.demo else db.connect()
    llm = LLM(max_calls=a.budget, tracer=Tracer.for_run("daily"))
    totals = {"processed": 0, "alerts": 0, "jobs_found": 0, "new_jobs": 0, "status_changes": 0, "errors": 0}
    stopped = False

    if a.demo:
        batches = [("demo", JsonlSource(p).fetch()) for p in DEMO_EMAILS]
    else:
        seen = {r[0] for r in con.execute("SELECT message_id FROM emails")}
        batches = [("job alerts", GmailSource(profile.alerts_query, a.max_emails).fetch(skip_ids=seen)),
                   ("company replies", GmailSource(profile.gmail_query, a.max_emails).fetch(skip_ids=seen))]

    for name, emails in batches:
        print(f"[{name}] reading new emails...", flush=True)
        rep = sync(con, emails, llm, profile)
        print(f"  processed {rep.processed} | alerts {rep.alerts_parsed} -> {rep.jobs_found} jobs "
              f"({rep.new_jobs} new) | status updates {len(rep.status_changes)}", flush=True)
        for c in rep.status_changes:
            print(f"    {c}")
        for err in rep.errors:
            print(f"    ERROR {err}")
        totals["processed"] += rep.processed
        totals["alerts"] += rep.alerts_parsed
        totals["jobs_found"] += rep.jobs_found
        totals["new_jobs"] += rep.new_jobs
        totals["status_changes"] += len(rep.status_changes)
        totals["errors"] += len(rep.errors)
        if rep.budget_stopped:
            stopped = True
            print(f"  request budget ({a.budget}) used up - remaining emails will be read next run")
            break

    rescore_all(con)
    items = shortlist(con, profile)
    today = dt.date.today().isoformat()
    DATA.mkdir(exist_ok=True)
    (DATA / "shortlist.md").write_text(to_markdown(items, today), encoding="utf-8")

    print(f"\n=== Shortlist ({len(items)} jobs scoring {profile.min_score:g}+) ===")
    for j in items[:10]:
        print(f"  {j['score']:5.1f}  {j['title']} @ {j['company']} [{j['portal']}]")
        if j["url"]:
            print(f"         {j['url']}")
    acts = action_items(con, profile.follow_up_after_days)
    print(f"\nDeadlines coming up: {len(acts['deadlines'])} | follow-ups due: {len(acts['follow_ups'])}")
    print("Open the dashboard to apply / dismiss:  python -m streamlit run app.py")

    secs = round(time.perf_counter() - t0, 1)
    log_run({"date": today, "time": dt.datetime.now().strftime("%H:%M"), **totals,
             "requests": llm.usage.calls, "budget_stopped": stopped, "shortlist": len(items), "seconds": secs})
    print(f"\n{llm.usage.calls}/{a.budget} model requests used, {secs}s")


if __name__ == "__main__":
    main()
