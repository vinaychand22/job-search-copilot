"""Get a validated Pydantic object out of any model - with one repair round.

1. Tool mode (default): offer a single tool whose parameters ARE the Pydantic schema and
   require a tool call. If a provider answers in text instead (some local models do), parse
   the first JSON object in the text.
2. JSON mode (automatic fallback): if the provider rejects the tool request itself with a
   400 "invalid argument" - Gemini's OpenAI-compatible endpoint does this for some nested
   schemas - send the same request with no tools, the schema written into the prompt, and
   parse the JSON reply. The fallback is remembered per schema, so the failing request is
   sent at most once per run.
3. Either way, validate with Pydantic (types + business rules). On failure, send the exact
   errors back once and let the model fix its own output. Fail loudly after that.
"""
from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from core.llm import LLM, ToolCall, inline_refs

T = TypeVar("T", bound=BaseModel)
_JSON = re.compile(r"\{.*\}", re.S)
JSON_MODE: set[str] = set()   # schema names a provider refused as tools during this run


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


def _is_rejected_request(e: Exception) -> bool:
    """True for 'the provider refused this request shape' (HTTP 400), not for rate limits or outages."""
    return getattr(e, "status_code", None) == 400 or type(e).__name__ == "BadRequestError"


def structured(llm: LLM, *, model: str, system: str, user: str, schema: type[T],
               max_repairs: int = 1, max_tokens: int = 2048, tag: str = "") -> T:
    name = f"submit_{schema.__name__.lower()}"
    if name not in JSON_MODE:
        try:
            return _tool_mode(llm, model, system, user, schema, name, max_repairs, max_tokens, tag)
        except Exception as e:
            if not _is_rejected_request(e):
                raise
            llm.tracer.log("structured_fallback", schema=schema.__name__, error=str(e)[:300])
            JSON_MODE.add(name)
    return _json_mode(llm, model, system, user, schema, max_repairs, max_tokens, tag)


def _tool_mode(llm: LLM, model: str, system: str, user: str, schema: type[T], name: str,
               max_repairs: int, max_tokens: int, tag: str) -> T:
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


def _json_mode(llm: LLM, model: str, system: str, user: str, schema: type[T],
               max_repairs: int, max_tokens: int, tag: str) -> T:
    spec = json.dumps(inline_refs(schema.model_json_schema()), separators=(",", ":"))
    messages: list[dict] = [{"role": "user", "content": (
        f"{user}\n\nReply with ONLY one JSON object - no prose, no markdown - that matches this "
        f"JSON schema ({schema.__doc__ or schema.__name__}):\n{spec}")}]

    for attempt in range(max_repairs + 1):
        r = llm.chat(model=model, system=system, messages=messages, max_tokens=max_tokens,
                     tag=f"{tag}:json{attempt}")
        raw = _from_text(r.text)
        if raw is None:
            error = "No JSON object found in your reply."
        else:
            try:
                return schema.model_validate(raw)
            except ValidationError as e:
                error = f"Validation failed:\n{e}"
        llm.tracer.log("validation_error", schema=schema.__name__, attempt=attempt, error=error[:500], mode="json")
        if attempt == max_repairs:
            raise StructuredOutputError(f"{schema.__name__}: {error}")
        messages += [{"role": "assistant", "content": r.text or ""},
                     {"role": "user", "content": f"{error}\nReply with ONLY the corrected JSON object."}]
    raise AssertionError("unreachable")
