"""Job Search Copilot dashboard.   Run:  streamlit run app.py

Reads the same SQLite file the agents write to. Nothing here calls an LLM, so the
dashboard is instant and free to open as often as you like.
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from copilot import db
from copilot.analytics import funnel, status_counts
from copilot.config import load_profile
from copilot.impact import report as impact_report
from copilot.matcher.market import market_summary, skill_demand, top_gaps
from copilot.matcher.run import load_jobs
from copilot.tracker.sync import action_items

st.set_page_config(page_title="Job Search Copilot", layout="wide")
profile = load_profile()
con = db.connect()

st.title("Job Search Copilot")
st.caption(f"Profile: {profile.name} · data: {db.DEFAULT_DB.name} · "
           "update with `python -m copilot.tracker.run` and `python -m copilot.matcher.run inbox/postings`")

overview, apps_tab, actions_tab, coach_tab, matches_tab, skills_tab, impact_tab = st.tabs(
    ["Overview", "Applications", "Action items", "Coach plan", "Job matches", "Skill demand", "Impact"])

with overview:
    counts = status_counts(con)
    cols = st.columns(6)
    for col, s in zip(cols, ["saved", "applied", "assessment", "interview", "offer", "rejected"]):
        col.metric(s.title(), counts.get(s, 0))
    f_all = funnel(con)
    if f_all:
        f = f_all[0]
        st.subheader("Funnel")
        st.bar_chart(pd.DataFrame({"applications": [f[s] for s in ("applied", "assessment", "interview", "offer")]},
                                  index=["applied", "assessment", "interview", "offer"]))
        c1, c2 = st.columns(2)
        c1.metric("Response rate (reached assessment+)", f"{f['response_rate']:.0%}")
        c2.metric("Interview rate", f"{f['interview_rate']:.0%}")
        st.subheader("By portal")
        st.dataframe(pd.DataFrame(funnel(con, by="source")), hide_index=True, width="stretch")
        st.subheader("By role family")
        st.dataframe(pd.DataFrame(funnel(con, by="role_family")), hide_index=True, width="stretch")
    else:
        st.info("No applications yet. Import your resume folders or run a tracker sync.")

with apps_tab:
    df = pd.read_sql("SELECT id, company, role, status, source, applied_on, last_update, next_action, deadline "
                     "FROM applications ORDER BY last_update DESC", con)
    pick = st.multiselect("Status", db.ALL_STATUSES, default=[s for s in db.ALL_STATUSES if s != "withdrawn"])
    st.dataframe(df[df.status.isin(pick)], hide_index=True, width="stretch")
    with st.form("manual_update"):
        st.write("Manual update (for things that never reach your inbox, like a phone call)")
        c1, c2, c3 = st.columns(3)
        company = c1.text_input("Company")
        role = c2.text_input("Role")
        status = c3.selectbox("Status", db.ALL_STATUSES, index=1)
        if st.form_submit_button("Save") and company.strip():
            db.upsert_application(con, company=company.strip(), role=role.strip(), status=status,
                                  summary="manual update from dashboard")
            st.success(f"Saved {company} → {status}")
            st.rerun()

with actions_tab:
    items = action_items(con, profile.follow_up_after_days)
    st.subheader(f"Upcoming deadlines ({len(items['deadlines'])})")
    st.dataframe(pd.DataFrame(items["deadlines"]), hide_index=True, width="stretch")
    st.subheader(f"Follow-ups due: no update in {profile.follow_up_after_days}+ days ({len(items['follow_ups'])})")
    st.dataframe(pd.DataFrame(items["follow_ups"]), hide_index=True, width="stretch")

with coach_tab:
    plan = con.execute("SELECT * FROM plans WHERE status = 'active' ORDER BY id DESC LIMIT 1").fetchone()
    if plan is None:
        st.info("No plan yet. Run `python -m copilot.coach.run --hours 8` and approve a plan.")
    else:
        diag, p = json.loads(plan["diagnosis_json"]), json.loads(plan["plan_json"])
        st.caption(f"Plan #{plan['id']} · created {plan['created_at']} · goal: {plan['goal']}")
        st.subheader(f"Bottleneck: {diag['stalls_at']}")
        for f in diag["findings"]:
            st.write(f"- {f}")
        st.subheader("This week")
        items = con.execute("SELECT id, kind, description, hours, done FROM plan_items WHERE plan_id = ?",
                            (plan["id"],)).fetchall()
        for it in items:
            label = f"[{it['kind']}] {it['description']}" + (f" ({it['hours']:g}h)" if it["hours"] else "")
            done = st.checkbox(label, value=bool(it["done"]), key=f"item{it['id']}")
            if done != bool(it["done"]):
                con.execute("UPDATE plan_items SET done = ? WHERE id = ?", (int(done), it["id"]))
                con.commit()
        total = len(items) or 1
        st.progress(sum(bool(i["done"]) for i in items) / total, text="completion")
        st.caption(p["rationale"])

with matches_tab:
    rows = con.execute("SELECT title, company, score, reasons_json, status FROM postings ORDER BY score DESC").fetchall()
    if rows:
        min_score = st.slider("Minimum score", 0, 100, 60)
        data = []
        for r in rows:
            reasons = json.loads(r["reasons_json"] or "{}")
            if r["score"] >= min_score:
                data.append({"score": r["score"], "title": r["title"], "company": r["company"],
                             "matched": ", ".join(reasons.get("matched_skills", [])),
                             "missing": ", ".join(reasons.get("missing_skills", [])),
                             "notes": "; ".join(reasons.get("reasons", []))})
        st.dataframe(pd.DataFrame(data), hide_index=True, width="stretch")
    else:
        st.info("No postings yet. Save postings as .txt in inbox/postings and run the matcher.")

with skills_tab:
    jobs = load_jobs(con)
    if jobs:
        st.write(market_summary(jobs))
        sd = pd.DataFrame(skill_demand(jobs, profile))
        st.bar_chart(sd.set_index("skill")["share"])
        st.dataframe(sd, hide_index=True, width="stretch")
        st.subheader("Top gaps to learn")
        st.write(", ".join(g["skill"] for g in top_gaps(jobs, profile)) or "None. Your skills cover the top demand.")
    else:
        st.info("Run the matcher first.")

with impact_tab:
    rows = impact_report()
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("Log your time: `python -m copilot.impact log tracking 25 --mode manual`")
