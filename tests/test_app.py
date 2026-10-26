"""Renders the dashboard headlessly against the demo database.

The generous timeout covers the first run on Windows, where importing Streamlit and pandas
(plus antivirus scanning of freshly installed packages) can take well over 30 seconds."""
import tempfile
from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_dashboard_demo_mode_and_shortlist_actions(monkeypatch):
    (Path(tempfile.gettempdir()) / "copilot-demo.db").unlink(missing_ok=True)
    monkeypatch.setenv("COPILOT_DEMO", "1")
    at = AppTest.from_file("../app.py", default_timeout=180).run()
    assert not at.exception
    labels = {m.label: m.value for m in at.metric}
    assert labels["Offer"] == "1" and labels["Interview rate"] == "38%"
    assert any("power bi" in c.label for c in at.checkbox)            # coach plan tab rendered
    applied = [b for b in at.button if b.label == "Applied"]
    assert len(applied) >= 4                                           # shortlist cards rendered
    before = len(applied)
    applied[0].click().run()                                           # click "Applied" on the top job
    assert not at.exception
    assert len([b for b in at.button if b.label == "Applied"]) == before - 1
    import sqlite3
    con = sqlite3.connect(Path(tempfile.gettempdir()) / "copilot-demo.db")
    applied_jobs = con.execute("SELECT title, company FROM postings WHERE status = 'applied'").fetchall()
    assert len(applied_jobs) == 1
    company = applied_jobs[0][1]
    status = con.execute("SELECT status FROM applications WHERE company = ?", (company,)).fetchone()[0]
    assert status in {"applied", "interview"}   # tracked; an existing later stage is never downgraded
