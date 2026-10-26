"""End-to-end: `python -m copilot.daily --demo` with a scripted model instead of a real one."""
import sys

import copilot.daily as daily
from copilot import db
from tests.fakes import FakeProvider, tool
from tests.test_feed import GOOD_NAUKRI

_real_connect = db.connect


def test_daily_demo_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(daily, "DATA", tmp_path)
    monkeypatch.setattr(daily.db, "connect", lambda path=None: _real_connect(path or tmp_path / "copilot.db"))
    li = {"jobs": [{"title": "AI Analyst", "company": "Lumen FinServ", "location": "India (Remote)", "link": 3}]}
    fake = FakeProvider([tool("submit_alertjobs", GOOD_NAUKRI), tool("submit_alertjobs", li)])
    monkeypatch.setattr("core.llm.provider_from_env", lambda name=None: fake)
    monkeypatch.setattr(sys, "argv", ["daily", "--demo", "--budget", "2"])
    daily.main()
    out = capsys.readouterr().out
    assert "alerts 2 -> 6 jobs (6 new)" in out                 # one request per alert, not per job
    assert "AI Analyst" in out and "Data Analyst" in out        # shortlist printed
    assert "apply links" in out                                   # pointer to links
    assert "budget (2) used up" in out                          # stops cleanly, rest waits
    assert (tmp_path / "shortlist.md").exists() and (tmp_path / "runs.csv").exists()
    assert (tmp_path / "demo-daily.db").exists() and not (tmp_path / "copilot.db").exists()  # real DB untouched
