"""CLI
  python -m copilot.tracker.run                    # sync from Gmail (read-only)
  python -m copilot.tracker.run --demo             # sync the bundled sample emails instead
  python -m copilot.tracker.run --max 50           # cap how many emails to fetch
"""
from __future__ import annotations

import argparse
from pathlib import Path

from copilot import db
from copilot.config import load_profile
from copilot.tracker.sources import GmailSource, JsonlSource
from copilot.tracker.sync import action_items, sync
from core.llm import LLM
from core.tracing import Tracer

DEMO = Path(__file__).parent / "data" / "emails_golden.jsonl"


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync job-search emails into the application tracker")
    ap.add_argument("--demo", action="store_true", help="use bundled sample emails, not Gmail")
    ap.add_argument("--max", type=int, default=200)
    ap.add_argument("--db", default=str(db.DEFAULT_DB))
    a = ap.parse_args()

    profile = load_profile()
    con = db.connect(a.db)
    llm = LLM(max_calls=a.max + 20, tracer=Tracer.for_run("tracker"))
    if a.demo:
        emails = JsonlSource(DEMO).fetch()
    else:
        seen = {r[0] for r in con.execute("SELECT message_id FROM emails")}
        emails = GmailSource(profile.gmail_query, a.max).fetch(skip_ids=seen)

    rep = sync(con, emails, llm)
    print(f"processed={rep.processed} skipped_already_seen={rep.skipped_seen} in {rep.seconds}s")
    print(f"handled by rules={rep.by_method['rule']} by model={rep.by_method['llm']}")
    print(f"categories: {dict(rep.by_category)}")
    for c in rep.status_changes:
        print(f"  updated -> {c}")
    for err in rep.errors:
        print(f"  ERROR {err}")
    items = action_items(con, profile.follow_up_after_days)
    print(f"\nupcoming deadlines: {len(items['deadlines'])}, follow-ups due: {len(items['follow_ups'])}")
    print(f"usage: {llm.usage.summary()}")


if __name__ == "__main__":
    main()
