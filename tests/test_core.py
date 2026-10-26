import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from core.agent_loop import run_agent
from core.llm import LLM, AnthropicProvider, BudgetExceeded, OpenAICompatProvider, ToolCall
from core.structured import StructuredOutputError, structured
from core.tools import Tool, ToolError
from tests.fakes import FakeProvider, text, tool


class AddArgs(BaseModel):
    a: int
    b: int


def _add(a: int, b: int) -> int:
    return a + b


def _boom(a: int, b: int) -> int:
    raise ToolError("division by zero, try other inputs")


ADD = Tool("add", "Add two ints", AddArgs, _add)


def chat(llm, **kw):
    return llm.chat(model="m", system="s", messages=[{"role": "user", "content": "hi"}], **kw)


# ---------- tools + loop ----------

def test_tool_validates_and_never_raises():
    assert ADD.run({"a": 2, "b": 3}) == ("5", False)
    out, err = ADD.run({"a": "x"})
    assert err and "Invalid arguments" in out
    out, err = Tool("boom", "", AddArgs, _boom).run({"a": 1, "b": 0})
    assert err and "division" in out


def test_agent_loop_runs_tools_then_finishes():
    llm = LLM(FakeProvider([tool("add", {"a": 1, "b": 2}), text("The answer is 3.")]))
    res = run_agent(llm, model="m", system="s", user="1+2?", tools=[ADD])
    assert res.stopped_reason == "end_turn" and res.final_text == "The answer is 3."
    assert res.tool_calls[0]["output"] == "3" and res.steps == 2


def test_agent_loop_reports_unknown_tool_and_stops_at_max_steps():
    llm = LLM(FakeProvider([tool("nope", {}), tool("add", {"a": 1, "b": 1})]))
    res = run_agent(llm, model="m", system="s", user="x", tools=[ADD], max_steps=2)
    assert res.stopped_reason == "max_steps" and "Unknown tool" in res.tool_calls[0]["output"]


def test_terminal_tool_ends_loop_immediately():
    llm = LLM(FakeProvider([tool("add", {"a": 4, "b": 4})]))
    res = run_agent(llm, model="m", system="s", user="x", tools=[ADD], terminal_tools=frozenset({"add"}))
    assert res.stopped_reason == "terminal" and res.terminal_input == {"a": 4, "b": 4}


# ---------- structured output ----------

class Score(BaseModel):
    """A score."""
    value: int = Field(ge=1, le=10)


def test_structured_repairs_once_with_validation_feedback():
    fp = FakeProvider([tool("submit_score", {"value": 11}), tool("submit_score", {"value": 7})])
    assert structured(LLM(fp), model="m", system="s", user="rate", schema=Score).value == 7
    last = fp.requests[1]["messages"][-1]
    assert last["role"] == "tool" and last["is_error"] and "less than or equal to 10" in last["content"]


def test_structured_falls_back_to_json_in_text():
    fp = FakeProvider([text('Sure! {"value": 4}')])
    assert structured(LLM(fp), model="m", system="s", user="rate", schema=Score).value == 4


def test_structured_gives_up_after_max_repairs():
    fp = FakeProvider([tool("submit_score", {"value": 0}), text("no json here")])
    with pytest.raises(StructuredOutputError):
        structured(LLM(fp), model="m", system="s", user="rate", schema=Score)


def test_budget_guard_and_missing_model():
    llm = LLM(FakeProvider([text("a"), text("b")]), max_calls=1)
    chat(llm)
    with pytest.raises(BudgetExceeded):
        chat(llm)
    with pytest.raises(ValueError, match="FAST_MODEL"):
        LLM(FakeProvider([])).chat(model="", system="s", messages=[])


# ---------- provider adapters translate the shared format correctly ----------

HISTORY = [
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": None, "tool_calls": [ToolCall("t1", "add", {"a": 1, "b": 2}),
                                                          ToolCall("t2", "add", {"a": 3, "b": 4})]},
    {"role": "tool", "tool_call_id": "t1", "name": "add", "content": "3", "is_error": False},
    {"role": "tool", "tool_call_id": "t2", "name": "add", "content": "bad", "is_error": True},
]


def test_openai_adapter_round_trip():
    seen = {}

    def create(**kw):
        seen.update(kw)
        call = SimpleNamespace(id="c9", function=SimpleNamespace(name="add", arguments='{"a": 5, "b": 6}'))
        msg = SimpleNamespace(content=None, tool_calls=[call])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")],
                               usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3))

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    r = OpenAICompatProvider(client).complete(model="m", system="sys", messages=HISTORY, tools=[ADD.schema()],
                                              force_tool="add", max_tokens=50, temperature=None)
    assert r.tool_calls == [ToolCall("c9", "add", {"a": 5, "b": 6})] and r.input_tokens == 11
    msgs = seen["messages"]
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert json.loads(msgs[2]["tool_calls"][1]["function"]["arguments"]) == {"a": 3, "b": 4}
    assert msgs[4] == {"role": "tool", "tool_call_id": "t2", "content": "ERROR: bad"}
    assert seen["tool_choice"] == "required" and seen["tools"][0]["function"]["name"] == "add"


def test_anthropic_adapter_groups_tool_results_and_caches_system():
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="done")], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=7, output_tokens=2))

    r = AnthropicProvider(SimpleNamespace(messages=SimpleNamespace(create=create))).complete(
        model="m", system="sys", messages=HISTORY, tools=None, force_tool=None, max_tokens=50, temperature=None)
    assert r.text == "done"
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"}
    results = seen["messages"][2]["content"]
    assert [b["tool_use_id"] for b in results] == ["t1", "t2"] and results[1]["is_error"]


def test_inline_refs_removes_references_for_gemini():
    import json
    from core.llm import inline_refs
    from copilot.coach.schemas import WeeklyPlan
    from copilot.feed.alerts import AlertJobs
    for model in (AlertJobs, WeeklyPlan):
        flat = inline_refs(model.model_json_schema())
        text = json.dumps(flat)
        assert "$ref" not in text and "$defs" not in text
    item = inline_refs(AlertJobs.model_json_schema())["properties"]["jobs"]["items"]
    assert item["type"] == "object" and "title" in item["properties"] and "link" in item["properties"]


def test_openai_adapter_sends_inlined_schema():
    from types import SimpleNamespace
    from copilot.feed.alerts import AlertJobs
    seen = {}

    def create(**kw):
        seen.update(kw)
        msg = SimpleNamespace(content="{}", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    tool_def = {"name": "submit_alertjobs", "description": "d", "input_schema": AlertJobs.model_json_schema()}
    OpenAICompatProvider(client).complete(model="m", system="s", messages=[{"role": "user", "content": "x"}],
                                          tools=[tool_def], force_tool="submit_alertjobs", max_tokens=10,
                                          temperature=None)
    assert "$ref" not in json.dumps(seen["tools"])
