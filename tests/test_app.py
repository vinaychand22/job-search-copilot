"""Renders the dashboard headlessly against the demo database.

The generous timeout covers the first run on Windows, where importing Streamlit and pandas
(plus antivirus scanning of freshly installed packages) can take well over 30 seconds."""
import importlib
import os

from streamlit.testing.v1 import AppTest


def test_dashboard_renders_with_demo_data(tmp_path, monkeypatch):
    from copilot import demo
    path = demo.build(tmp_path / "demo.db")
    monkeypatch.setenv("COPILOT_DB", str(path))
    import copilot.db
    importlib.reload(copilot.db)  # pick up COPILOT_DB
    at = AppTest.from_file("../app.py", default_timeout=180).run()
    assert not at.exception
    labels = {m.label: m.value for m in at.metric}
    assert labels["Offer"] == "1" and labels["Applied"] == "1"
    assert labels["Interview rate"] == "38%"
    assert any("power bi" in c.label for c in at.checkbox)   # coach plan tab rendered its items
