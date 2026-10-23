import json

import pytest

from copilot.coach.graph import Coach, check_plan
from copilot.coach.scenarios import build
from copilot.coach.tools import AnalystTools
from copilot.config import load_profile
from core.llm import LLM
from core.tools import ToolError
from tests.fakes import FakeProvider, tool

PROFILE = load_profile()

GOOD_DIAG = {"findings": ["Naukri: 24 applications with a 0.042 response rate",
                          "LinkedIn: 10 applications with a 0.7 response rate"],
             "stalls_at": "response", "strongest_segment": "LinkedIn", "weakest_segment": "Naukri",
             "top_skill_gaps": ["power bi"]}
GOOD_PLAN = {"focus_roles": ["data analyst"], "focus_sources": ["LinkedIn"], "applications_target": 12,
             "learning": [{"skill": "power bi", "hours": 6, "why": "top gap", "deliverable": "dashboard on GitHub"}],
             "actions": ["Tailor resume keywords for each LinkedIn application"], "rationale": "LinkedIn converts."}


def test_tools_record_evidence_and_block_writes():
    con = build("weak_portal")
    t = AnalystTools(con, PROFILE)
    out = json.loads(t.funnel_stats("source"))
    assert {r["group"] for r in out} == {"LinkedIn", "Naukri"} and t.evidence
    with pytest.raises(ToolError):
        t.run_sql("DELETE FROM applications")
    with pytest.raises(ToolError):  # passes the regex, blocked by PRAGMA query_only
        t.run_sql("WITH x AS (SELECT 1) DELETE FROM applications")
    assert con.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 34


def test_diagnosis_with_invented_numbers_is_rejected():
    t = AnalystTools(build("weak_portal"), PROFILE)
    t.funnel_stats("source")
    with pytest.raises(ToolError, match="not in any tool result"):
        t.submit_diagnosis(**{**GOOD_DIAG, "findings": ["Naukri response rate is 12%"]})
    assert t.submit_diagnosis(**GOOD_DIAG) == "Diagnosis accepted."
    # percentages are matched against fractions: 70% <-> 0.7
    assert t.submit_diagnosis(**{**GOOD_DIAG, "findings": ["LinkedIn responds 70% of the time"]})


def test_full_graph_pauses_for_approval_then_saves():
    con = build("weak_portal")
    fp = FakeProvider([
        tool("funnel_stats", {"group_by": "source"}),
        tool("submit_diagnosis", {**GOOD_DIAG, "findings": ["Naukri response rate is 12%"]}),  # rejected
        tool("submit_diagnosis", GOOD_DIAG),                                                   # accepted
        tool("submit_weeklyplan", GOOD_PLAN),
    ])
    coach = Coach(con, PROFILE, LLM(fp))
    out = coach.start("t1", "data analyst role", 8)
    assert out["waiting_for_approval"] and out["payload"]["plan"]["applications_target"] == 12
    assert con.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0       # nothing saved before approval
    out = coach.resume("t1", approved=True)
    assert not out["waiting_for_approval"] and out["state"]["plan_id"] == 1
    kinds = [r[0] for r in con.execute("SELECT kind FROM plan_items ORDER BY id")]
    assert kinds == ["applications", "learning", "action"]


def test_human_feedback_triggers_revision():
    fp = FakeProvider([tool("funnel_stats", {"group_by": "source"}), tool("submit_diagnosis", GOOD_DIAG),
                       tool("submit_weeklyplan", GOOD_PLAN),
                       tool("submit_weeklyplan", {**GOOD_PLAN, "applications_target": 6})])
    coach = Coach(build("weak_portal"), PROFILE, LLM(fp))
    coach.start("t2", "goal", 8)
    out = coach.resume("t2", approved=False, feedback="Only 6 applications, I have interviews this week")
    assert out["waiting_for_approval"] and out["payload"]["plan"]["applications_target"] == 6
    assert "Candidate feedback" in fp.requests[-1]["messages"][0]["content"]


def test_thin_data_skips_the_analyst_entirely():
    plan = {**GOOD_PLAN, "learning": [], "actions": ["Apply to 15 roles and track every application"]}
    fp = FakeProvider([tool("submit_weeklyplan", plan)])   # only the planner may be called
    out = Coach(build("too_little_data"), PROFILE, LLM(fp)).start("t3", "goal", 8)
    assert out["state"]["diagnosis"]["stalls_at"] == "insufficient_data"
    assert out["state"]["analyst_tool_calls"] == 0 and len(fp.requests) == 1


def test_critic_rules():
    assert check_plan(GOOD_PLAN, GOOD_DIAG, 8) == []
    over = {**GOOD_PLAN, "learning": [{**GOOD_PLAN["learning"][0], "hours": 12}]}
    assert "exceed" in check_plan(over, GOOD_DIAG, 8)[0]
    off_target = {**GOOD_PLAN, "actions": ["Learn Kubernetes"]}
    assert any("bottleneck" in i for i in check_plan(off_target, GOOD_DIAG, 8))
    weak_focus = {**GOOD_PLAN, "focus_sources": ["Naukri"]}
    assert any("worst-converting" in i for i in check_plan(weak_focus, GOOD_DIAG, 8))
