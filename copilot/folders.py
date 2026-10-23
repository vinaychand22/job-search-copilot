"""Import your existing folder-based tracking into the database (a one-time migration).

You track applications as resume files in folders such as:
    Resumes/applied/Deloitte_Data Analyst.pdf
    Resumes/rejected/Swiggy - Business Analyst.pdf
Each file becomes an application whose status comes from its folder name
(mapping in [tracker.folder_status] of profile.toml). The file's modified
time is used as the application date.

Usage:  python -m copilot.folders ~/Documents/Resumes [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

from copilot import db
from copilot.config import load_profile

# "Company_Role", "Company - Role", "Company__Role_v2" -> (company, role). Edit if your naming differs.
SPLIT = re.compile(r"\s*(?:_{1,2}| - | – )\s*")
NOISE = re.compile(r"\b(resume|cv|final|updated|v\d+|copy|vinay\w*)\b", re.I)
RESUME_EXT = {".pdf", ".docx", ".doc"}


def parse_filename(stem: str) -> tuple[str, str]:
    parts = [p for p in SPLIT.split(stem) if p.strip()]
    parts = [NOISE.sub("", p).strip(" -_") for p in parts]
    parts = [p for p in parts if p]
    if not parts:
        return stem, ""
    return parts[0], " ".join(parts[1:])


def scan(root: Path, folder_status: dict[str, str]) -> list[dict]:
    found = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        status = folder_status.get(sub.name.lower())
        if status is None:
            print(f"skip folder '{sub.name}' (no mapping in profile.toml [tracker.folder_status])")
            continue
        for f in sorted(sub.rglob("*")):
            if f.suffix.lower() in RESUME_EXT:
                company, role = parse_filename(f.stem)
                when = dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
                found.append({"company": company, "role": role, "status": status, "when": when, "file": str(f)})
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description="Import resume folders into the tracker")
    ap.add_argument("root", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rows = scan(a.root.expanduser(), load_profile().folder_status)
    for r in rows:
        print(f"{r['status']:<10} {r['company']:<30} {r['role']}")
    if a.dry_run:
        print(f"\n{len(rows)} applications found (dry run, nothing saved)")
        return
    con = db.connect()
    for r in rows:
        db.upsert_application(con, company=r["company"], role=r["role"], status=r["status"],
                              when=r["when"], summary=f"imported from {r['file']}")
    print(f"\nimported {len(rows)} applications into {db.DEFAULT_DB}")


if __name__ == "__main__":
    main()
