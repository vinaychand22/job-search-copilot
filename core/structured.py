"""Get a validated Pydantic object out of any model - with one repair round.

1. Offer a single tool whose parameters ARE the Pydantic schema and require a tool call.
2. If a provider ignores that and answers in text (some local models do), parse the first
   JSON object in the text instead.
3. Validate with Pydantic (types + business rules). On failure, send the exact errors back
   once and let the model fix its own output. Fail loudly after that - never save bad data.
"""
from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from core.llm import LLM, ToolCall

T = TypeVar("T", bound=BaseModel)
_JSON = re.compile(r"\{.*\}", re.S)


class StructuredOutputError(RuntimeError):
    pass


def _from_text(text: str) -> dict | None:
    m = _JSON.search(text or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def structured(llm: LLM, *, model: str, system: str, user: str, schema: type[T],
               max_repairs: int = 1, max_tokens: int = 2048, tag: str = "") -> T:
    name = f"submit_{schema.__name__.lower()}"
    tool = {"name": name, "description": f"Submit the final {schema.__name__}. {schema.__doc__ or ''}".strip(),
            "input_schema": schema.model_json_schema()}
    messages: list[dict] = [{"role": "user", "content": user}]

    for attempt in range(max_repairs + 1):
        r = llm.chat(model=model, system=system, messages=messages, tools=[tool], force_tool=name,
                     max_tokens=max_tokens, tag=f"{tag}:attempt{attempt}")
        call = next((c for c in r.tool_calls if c.name == name), None)
        raw = call.args if call else _from_text(r.text)
        if raw is None:
            error = f"No {name} call and no JSON object found. Call {name} with the fields."
        else:
            try:
                return schema.model_validate(raw)
            except ValidationError as e:
                error = f"Validation failed, fix these and resubmit:\n{e}"
        llm.tracer.log("validation_error", schema=schema.__name__, attempt=attempt, error=error[:500])
        if attempt == max_repairs:
            raise StructuredOutputError(f"{schema.__name__}: {error}")
        call = call or ToolCall(f"repair_{attempt}", name, raw or {})
        messages += [{"role": "assistant", "content": r.text or None, "tool_calls": [call]},
                     {"role": "tool", "tool_call_id": call.id, "name": name, "content": error, "is_error": True}]
    raise AssertionError("unreachable")
