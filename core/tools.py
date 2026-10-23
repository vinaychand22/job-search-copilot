"""Tool registry: a Python function + a Pydantic argument model = a tool.

Design rules (from Anthropic's "Building effective agents" guidance on tool design):
  * The schema the model sees is generated from the same Pydantic model we
    validate against, so the two can never drift apart.
  * Tools never raise into the agent loop. Every failure becomes a short,
    actionable error string with is_error=True, so the model can recover
    (fix its arguments, try another tool) instead of the run crashing.
  * Outputs are truncated so one chatty tool cannot blow the context window.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

MAX_TOOL_OUTPUT_CHARS = 6_000


class ToolError(Exception):
    """Expected, user-facing failure (e.g. 'application not found')."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[..., Any]

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.args.model_json_schema(),
        }

    def run(self, raw_input: dict[str, Any]) -> tuple[str, bool]:
        """Returns (content, is_error). Never raises."""
        try:
            args = self.args.model_validate(raw_input)
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(map(str, err['loc'])) or 'input'}: {err['msg']}" for err in e.errors()
            )
            return f"Invalid arguments for {self.name}: {problems}. Fix them and call again.", True
        try:
            out = self.fn(**args.model_dump())
        except ToolError as e:
            return str(e), True
        except Exception as e:  # unexpected bug: report, don't crash the run
            return f"{self.name} failed unexpectedly ({type(e).__name__}). Try a different approach.", True

        text = out if isinstance(out, str) else json.dumps(out, default=str, ensure_ascii=False)
        if len(text) > MAX_TOOL_OUTPUT_CHARS:
            text = text[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated]"
        return text, False
