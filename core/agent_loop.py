"""A hand-written tool-using agent loop (no framework), provider-agnostic.

    model -> tool calls? -> run tools -> results -> model ... until done

Stops when (1) a terminal tool succeeds, (2) the model stops calling tools, or
(3) max_steps is hit - a hard guard against loops and runaway cost.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.llm import LLM
from core.tools import Tool


@dataclass
class AgentResult:
    stopped_reason: str  # "terminal" | "end_turn" | "max_steps"
    steps: int
    final_text: str = ""
    terminal_tool: str | None = None
    terminal_input: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def run_agent(llm: LLM, *, model: str, system: str, user: str, tools: list[Tool],
              terminal_tools: frozenset[str] = frozenset(), max_steps: int = 8,
              max_tokens: int = 1500) -> AgentResult:
    registry = {t.name: t for t in tools}
    schemas = [t.schema() for t in tools]
    messages: list[dict] = [{"role": "user", "content": user}]
    calls: list[dict[str, Any]] = []

    for step in range(1, max_steps + 1):
        r = llm.chat(model=model, system=system, messages=messages, tools=schemas,
                     max_tokens=max_tokens, tag=f"step{step}")
        messages.append({"role": "assistant", "content": r.text or None, "tool_calls": r.tool_calls})
        if not r.tool_calls:
            return AgentResult("end_turn", step, final_text=r.text, tool_calls=calls)

        terminal = None
        for c in r.tool_calls:
            tool = registry.get(c.name)
            out, is_err = (tool.run(c.args) if tool else
                           (f"Unknown tool '{c.name}'. Available: {sorted(registry)}", True))
            calls.append({"tool": c.name, "input": c.args, "is_error": is_err, "output": out[:300]})
            llm.tracer.log("tool_call", tool=c.name, input=c.args, is_error=is_err, output=out[:500])
            messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": out, "is_error": is_err})
            if c.name in terminal_tools and not is_err:
                terminal = c
        if terminal is not None:
            return AgentResult("terminal", step, terminal_tool=terminal.name,
                               terminal_input=dict(terminal.args), tool_calls=calls)
    return AgentResult("max_steps", max_steps, tool_calls=calls)
