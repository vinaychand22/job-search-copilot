"""A scripted stand-in provider so tests run offline, free and deterministically."""
from __future__ import annotations

import itertools
import threading
from typing import Any

from core.llm import LLMResponse, ToolCall

_ids = itertools.count(1)


def text(t: str) -> LLMResponse:
    return LLMResponse(text=t, stop_reason="stop", input_tokens=100, output_tokens=20)


def tool(name: str, args: dict[str, Any], *more: tuple[str, dict[str, Any]]) -> LLMResponse:
    calls = [ToolCall(f"call_{next(_ids)}", n, a) for n, a in [(name, args), *more]]
    return LLMResponse(tool_calls=calls, stop_reason="tool_calls", input_tokens=100, output_tokens=20)


class FakeProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def complete(self, **kwargs: Any) -> LLMResponse:
        with self._lock:
            self.requests.append(kwargs)
            if not self._responses:
                raise AssertionError("FakeProvider ran out of scripted responses")
            return self._responses.pop(0)


# Backwards-compatible name used across the tests
FakeClient = FakeProvider
