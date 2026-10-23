"""Measure the copilot's impact on YOU: log minutes per task, manual vs with the copilot.

Week 1 (before):  python -m copilot.impact log tracking 25 --mode manual --note "updated folders + checked mail"
Week 2+ (after):  python -m copilot.impact log tracking 4 --mode copilot --note "ran sync, reviewed dashboard"
Report:           python -m copilot.impact report

Tasks are free text, but keep them consistent: finding_jobs, tracking, tailoring, prep.
Medians (not means) so one unusually long day doesn't dominate. The report shows n,
because "saved 80%" from 2 samples is not a claim you want to defend in an interview.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import statistics
from collections import defaultdict
from pathlib import Path

from copilot.config import ROOT

LOG = ROOT / "data" / "impact_log.csv"
FIELDS = ["date", "task", "mode", "minutes", "note"]


def log(task: str, minutes: float, mode: str, note: str = "", path: Path = LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerow({"date": dt.date.today().isoformat(), "task": task, "mode": mode,
                    "minutes": minutes, "note": note})


def report(path: Path = LOG) -> list[dict]:
    if not path.exists():
        return []
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    with path.open() as f:
        for r in csv.DictReader(f):
            groups[(r["task"], r["mode"])].append(float(r["minutes"]))
    out = []
    for task in sorted({t for t, _ in groups}):
        man, cop = groups.get((task, "manual"), []), groups.get((task, "copilot"), [])
        row = {"task": task, "manual_median": statistics.median(man) if man else None, "manual_n": len(man),
               "copilot_median": statistics.median(cop) if cop else None, "copilot_n": len(cop)}
        if man and cop:
            row["reduction_pct"] = round(100 * (1 - row["copilot_median"] / row["manual_median"]), 1)
        out.append(row)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Log and report time spent on job-search tasks")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("log")
    lg.add_argument("task")
    lg.add_argument("minutes", type=float)
    lg.add_argument("--mode", choices=["manual", "copilot"], required=True)
    lg.add_argument("--note", default="")
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "log":
        log(a.task, a.minutes, a.mode, a.note)
        print("logged")
        return
    rows = report()
    if not rows:
        print("no entries yet")
    for r in rows:
        print(r)
    if any(min(r["manual_n"], r["copilot_n"]) < 5 for r in rows):
        print("note: fewer than 5 samples on one side for some tasks - keep logging before quoting numbers")


if __name__ == "__main__":
    main()
