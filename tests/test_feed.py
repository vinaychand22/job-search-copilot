import datetime as dt

from copilot import db
from copilot.config import load_profile
from copilot.feed.alerts import AlertJob, job_key, parse_alert, resolve_link, salary_from, to_posting, years_from
from copilot.feed.eval import score as feed_score
from copilot.feed.shortlist import decide, shortlist
from copilot.tracker.sources import JsonlSource, extract_links
from copilot.tracker.sync import sync
from core.llm import LLM
from tests.fakes import FakeProvider, tool

PROFILE = load_profile()
ALERTS = list(JsonlSource("copilot/feed/data/alerts_golden.jsonl").fetch())
NAUKRI = ALERTS[0]

GOOD_NAUKRI = {"jobs": [
    {"title": "Data Analyst", "company": "Brightpath Analytics", "location": "Hyderabad", "experience": "2-5 Yrs",
     "salary": "8-12 Lacs PA", "skills": ["SQL", "Python", "Power BI", "Excel"], "link": 1},
    {"title": "Pricing Analyst - FMCG", "company": "Northstar Foods", "location": "Bengaluru (Hybrid)",
     "experience": "2-4 Yrs", "salary": "Not disclosed", "skills": ["SQL", "Price Elasticity", "Python"], "domains": ["fmcg", "pricing"],
     "link": 2},
    {"title": "Senior Data Scientist", "company": "Orbit Retail", "location": "Bengaluru", "experience": "7-12 Yrs",
     "salary": "30-45 Lacs PA", "skills": ["Deep Learning", "PyTorch"], "link": 3},
    {"title": "Business Analyst", "company": "Crestline Technologies Pvt Ltd", "location": "Hyderabad",
     "experience": "1-3 Yrs", "salary": "6-9 Lacs PA", "skills": ["SQL", "Excel"], "link": 99},   # bad number
    {"title": "Field Sales Executive", "company": "Kestrel Commerce", "location": "Hyderabad",
     "experience": "0-2 Yrs", "salary": "3-4 Lacs PA", "skills": ["Sales"], "link": 5},
]}


def test_parsers():
    assert years_from("2-5 Yrs") == 2 and years_from("5+ years") == 5 and years_from(None) is None
    assert salary_from("8-12 Lacs PA") == (800_000, 1_200_000)
    assert salary_from("₹ 6.5 LPA") == (650_000, 650_000)
    assert salary_from("Not disclosed") is None and salary_from("$100k") is None


def test_links_are_extracted_by_code_and_junk_removed():
    urls = [u for _, u in NAUKRI.links]
    assert urls[0].startswith("https://www.naukri.com/job-listings-231025001")
    assert not any("unsubscribe" in u or "settings" in u or "play.google" in u for u in urls)
    assert extract_links("see https://x.com/job/1 and https://x.com/unsubscribe", False) == (("", "https://x.com/job/1"),)


def test_bad_link_number_falls_back_to_title_match():
    links = list(NAUKRI.links)
    j = AlertJob(title="Business Analyst", link=99)
    assert resolve_link(j, links).endswith("231025004?src=jobalert")
    assert resolve_link(AlertJob(title="Totally Different Role", link=None), links) is None


def test_parse_alert_one_call_for_whole_email():
    fp = FakeProvider([tool("submit_alertjobs", GOOD_NAUKRI)])
    out = parse_alert(NAUKRI, LLM(fp))
    assert len(out) == 5 and len(fp.requests) == 1
    assert out[3][1].endswith("231025004?src=jobalert")          # rescued by title match
    m = feed_score({"expected_jobs": [{"title": "Data Analyst", "company": "Brightpath Analytics",
                                       "url": out[0][1]}]}, out[:1])
    assert m == {"job_recall": 1.0, "job_precision": 1.0, "link_accuracy": 1.0, "company_accuracy": 1.0}


def test_to_posting_and_domain_scoring():
    p = to_posting(AlertJob(title="Pricing Analyst - FMCG", company="Northstar Foods", location="Bengaluru (Hybrid)",
                            experience="2-4 Yrs", skills=["SQL", "Price Elasticity"]))
    assert (p.work_mode, p.min_years_experience, p.required_skills) == ("hybrid", 2, ["sql", "price elasticity"])
    with_domain = p.model_copy(update={"domains": ["fmcg", "pricing"]})
    from copilot.matcher.scoring import score_posting
    assert score_posting(with_domain, PROFILE).score > score_posting(p, PROFILE).score
    assert "domain match" in "; ".join(score_posting(with_domain, PROFILE).reasons)


def test_sync_turns_alert_into_jobs_and_dedupes_across_portals():
    con = db.connect(":memory:")
    fp = FakeProvider([tool("submit_alertjobs", GOOD_NAUKRI),
                       tool("submit_alertjobs", {"jobs": [   # same job again via LinkedIn
                           {"title": "Data Analyst", "company": "BrightPath Analytics", "location": "Hyderabad",
                            "link": 1}]})])
    rep = sync(con, [NAUKRI], LLM(fp), PROFILE)
    assert (rep.alerts_parsed, rep.jobs_found, rep.new_jobs) == (1, 5, 5)
    li = ALERTS[1]
    rep2 = sync(con, [li], LLM(fp), PROFILE)
    assert rep2.new_jobs == 0                                   # duplicate recognised
    titles = [j["title"] for j in shortlist(con, PROFILE)]
    assert "Data Analyst" in titles and "Field Sales Executive" not in titles   # dealbreaker filtered
    assert "Senior Data Scientist" not in titles                                # below min score


def test_old_alerts_are_recorded_but_not_parsed():
    con = db.connect(":memory:")
    old = NAUKRI.__class__(**{**NAUKRI.__dict__, "message_id": "old",
                              "received_at": (dt.datetime.now() - dt.timedelta(days=30)).isoformat()})
    rep = sync(con, [old], LLM(FakeProvider([])), PROFILE)     # no model call allowed
    assert rep.processed == 1 and rep.alerts_parsed == 0


def test_budget_stop_leaves_rest_for_next_run():
    con = db.connect(":memory:")
    fp = FakeProvider([tool("submit_alertjobs", GOOD_NAUKRI)])
    rep = sync(con, ALERTS, LLM(fp, max_calls=1), PROFILE)
    assert rep.budget_stopped and rep.processed == 1
    assert con.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 1     # LinkedIn alert waits


def test_decide_applied_starts_tracking_and_dismissed_stays_hidden():
    con = db.connect(":memory:")
    sync(con, [NAUKRI], LLM(FakeProvider([tool("submit_alertjobs", GOOD_NAUKRI)])), PROFILE)
    items = shortlist(con, PROFILE)
    decide(con, items[0]["id"], "applied")
    decide(con, items[1]["id"], "dismissed")
    assert con.execute("SELECT status FROM applications").fetchone()[0] == "applied"
    remaining = {i["id"] for i in shortlist(con, PROFILE)}
    assert items[0]["id"] not in remaining and items[1]["id"] not in remaining
    # re-seeing the same job later must not resurrect it
    db.save_posting(con, job_key(items[1]["title"], items[1]["company"]), "x", {"title": items[1]["title"]}, 99, {})
    assert items[1]["id"] not in {i["id"] for i in shortlist(con, PROFILE)}
