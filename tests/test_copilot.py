import base64
import datetime as dt
import os
import time

import pytest

from copilot import db
from copilot.analytics import funnel
from copilot.config import load_profile
from copilot.folders import parse_filename, scan
from copilot.impact import log, report
from copilot.matcher.market import skill_demand, top_gaps
from copilot.matcher.schema import JobPosting
from copilot.matcher.scoring import score_posting
from copilot.tracker.classifier import classify, rule_classify
from copilot.tracker.sources import Email, _extract_body, clean_body
from copilot.tracker.sync import action_items, sync
from core.llm import LLM
from tests.fakes import FakeClient, tool

PROFILE = load_profile()


def mem():
    return db.connect(":memory:")


def email(mid, subject, body="", sender="hr@acme.com", when="2026-09-01T10:00:00"):
    return Email(mid, sender, subject, when, body)


def triage(category, company="Acme Analytics", action=False, deadline=None, role="Data Analyst"):
    return tool("submit_emailtriage", {"category": category, "company": company, "role": role,
                                       "action_required": action, "deadline": deadline, "summary": f"{category} from {company}"})


# ---------- database rules ----------

def test_company_key_normalises_legal_suffixes():
    assert db.company_key("Quantium Analytics India Pvt. Ltd.") == "quantium analytics"
    assert db.company_key("ACME Technologies Private Limited") == "acme"


def test_status_only_moves_forward_and_terminal_states_stick():
    assert db.should_advance("applied", "interview")
    assert not db.should_advance("interview", "applied")       # a late confirmation email
    assert db.should_advance("interview", "rejected")
    assert not db.should_advance("rejected", "applied")        # rejection is final


def test_fuzzy_company_match_reuses_application():
    con = mem()
    a, _ = db.upsert_application(con, company="Brightpath Analytics Pvt Ltd", role="Data Analyst", status="applied")
    b, changed = db.upsert_application(con, company="BrightPath Analytics", role="Data Analyst", status="assessment")
    assert a == b and changed
    assert con.execute("SELECT status FROM applications").fetchone()[0] == "assessment"


# ---------- tracker ----------

def test_rules_catch_alerts_but_not_portal_relayed_rejections():
    assert rule_classify(email("1", "30+ new jobs for 'data analyst'", sender="jobalerts-noreply@linkedin.com")).category == "job_alert"
    assert rule_classify(email("2", "OTP for login")).category == "other"
    # LinkedIn relays real outcomes from a noreply address: must go to the model, not the alert rule
    assert rule_classify(email("3", "Your application to Product Analyst at Kestrel",
                               sender="LinkedIn <jobs-noreply@linkedin.com>")) is None


def test_invalid_deadline_is_repaired_by_model():
    client = FakeClient([triage("assessment", action=True, deadline="next Friday"),
                         triage("assessment", action=True, deadline="2026-09-04")])
    tri, method = classify(email("1", "Online test", "complete by Friday"), LLM(client))
    assert method == "llm" and tri.deadline == "2026-09-04" and len(client.requests) == 2


def test_sync_updates_status_and_is_idempotent():
    con = mem()
    mails = [email("a", "Thanks for applying", when="2026-09-01T10:00:00"),
             email("b", "Online assessment", when="2026-09-03T10:00:00"),
             email("c", "30+ new jobs for you", sender="jobalerts@naukri.com")]
    llm = LLM(FakeClient([triage("application_confirmation"),
                          triage("assessment", action=True, deadline="2026-09-05")]))
    rep = sync(con, mails, llm)
    assert rep.processed == 3 and rep.by_method == {"llm": 2, "rule": 1}
    row = con.execute("SELECT status, deadline FROM applications").fetchone()
    assert tuple(row) == ("assessment", "2026-09-05")
    rep2 = sync(con, mails, llm)       # FakeClient has no responses left: any model call would fail
    assert rep2.processed == 0 and rep2.skipped_seen == 3


def test_sync_survives_a_failing_email():
    con = mem()
    rep = sync(con, [email("x", "Interview invite")], LLM(FakeClient([])))
    assert rep.processed == 0 and len(rep.errors) == 1
    assert con.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 0  # retried next run


def test_action_items_deadlines_and_follow_ups():
    con = mem()
    db.upsert_application(con, company="A", status="assessment", deadline="2026-09-10", when="2026-09-01T09:00:00")
    db.upsert_application(con, company="B", status="applied", when="2026-08-01T09:00:00")
    items = action_items(con, 14, today=dt.date(2026, 9, 5))
    assert [d["company"] for d in items["deadlines"]] == ["A"]
    assert [f["company"] for f in items["follow_ups"]] == ["B"]


def test_gmail_body_prefers_plain_text_and_cleans_html():
    enc = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/html", "body": {"data": enc("<p>Hi <b>there</b></p>")}},
        {"mimeType": "text/plain", "body": {"data": enc("Hi there")}}]}
    assert _extract_body(payload) == ("Hi there", False)
    assert clean_body("<style>x{}</style><p>Hello&amp;bye</p>", is_html=True) == "Hello&bye"
    assert clean_body("Thanks\nOn Mon, 1 Sep 2026 Bob wrote:\n> old stuff") == "Thanks"


# ---------- folders migration ----------

def test_parse_filename_variants():
    assert parse_filename("Deloitte_Data Analyst") == ("Deloitte", "Data Analyst")
    assert parse_filename("Swiggy - Business Analyst - Vinay_Resume_v2") == ("Swiggy", "Business Analyst")


def test_scan_maps_folders_to_status(tmp_path):
    (tmp_path / "applied").mkdir()
    (tmp_path / "rejected").mkdir()
    (tmp_path / "random").mkdir()
    (tmp_path / "applied" / "Acme_Data Analyst.pdf").write_bytes(b"x")
    (tmp_path / "rejected" / "Zeta - BA.docx").write_bytes(b"x")
    (tmp_path / "random" / "Other_Role.pdf").write_bytes(b"x")
    rows = scan(tmp_path, PROFILE.folder_status)
    assert {(r["company"], r["status"]) for r in rows} == {("Acme", "applied"), ("Zeta", "rejected")}


# ---------- matcher ----------

def job(**kw):
    base = dict(title="Data Analyst", location="Hyderabad", work_mode="hybrid", min_years_experience=2,
                required_skills=["sql", "python", "power bi"])
    return JobPosting.model_validate({**base, **kw})


def test_score_rewards_fit_and_explains_gaps():
    s = score_posting(job(), PROFILE)
    assert s.matched_skills == ["sql", "python"] and s.missing_skills == ["power bi"]
    assert 70 < s.score < 100


def test_hard_filters_exclude():
    assert score_posting(job(title="Sales Analyst"), PROFILE).excluded
    low = job(salary_min=300000, salary_max=400000, salary_currency="INR", salary_period="year")
    assert "below your minimum" in score_posting(low, PROFILE).excluded


def test_wrong_city_and_seniority_rank_lower():
    good = score_posting(job(), PROFILE).score
    assert score_posting(job(location="Pune", work_mode="onsite"), PROFILE).score < good
    assert score_posting(job(min_years_experience=8), PROFILE).score < good


def test_skill_demand_and_gaps():
    jobs = [job(), job(required_skills=["power bi", "sql"]), job(required_skills=["tableau"])]
    sd = {d["skill"]: d for d in skill_demand(jobs, PROFILE)}
    assert sd["power bi"]["share"] == round(2 / 3, 3) and not sd["power bi"]["you_have"]
    assert [g["skill"] for g in top_gaps(jobs, PROFILE)] == ["power bi", "tableau"]


# ---------- analytics + impact ----------

def test_funnel_counts_stages_reached_even_after_rejection():
    con = mem()
    db.upsert_application(con, company="A", status="applied", source="Naukri")
    db.upsert_application(con, company="A", status="interview")
    db.upsert_application(con, company="A", status="rejected")
    db.upsert_application(con, company="B", status="rejected", source="LinkedIn")  # only a rejection seen
    f = funnel(con)[0]
    assert (f["applied"], f["interview"], f["interview_rate"]) == (2, 1, 0.5)
    assert {r["group"] for r in funnel(con, by_source=True)} == {"Naukri", "LinkedIn"}


def test_impact_report_medians(tmp_path):
    p = tmp_path / "log.csv"
    for m in (30, 20, 25):
        log("tracking", m, "manual", path=p)
    for m in (5, 4):
        log("tracking", m, "copilot", path=p)
    r = report(p)[0]
    assert (r["manual_median"], r["copilot_median"], r["reduction_pct"]) == (25.0, 4.5, 82.0)
