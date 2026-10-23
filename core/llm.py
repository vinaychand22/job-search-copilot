"""Provider-agnostic LLM layer.

The rest of the code talks to ONE interface (`LLM.chat`) using ONE message format, and a
small adapter translates to each vendor:

    openai     OpenAI API                         (OPENAI_API_KEY)
    azure      Azure OpenAI                       (AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT)
    gemini     Google Gemini, OpenAI-compatible   (GEMINI_API_KEY)   - has a free tier
    ollama     Local open models on your Mac      (no key)           - free and private
    anthropic  Claude                             (ANTHROPIC_API_KEY)

Choose with LLM_PROVIDER plus FAST_MODEL / SMART_MODEL in .env. Switching vendors is a
config change, not a code change, which also makes "is the cheap model good enough?" a
one-line eval experiment.

Internal message format (what agents build and keep in history):
    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "..." | None, "tool_calls": [ToolCall, ...]}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "...", "is_error": bool}
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.tracing import Tracer

try:  # read .env automatically on any OS (Windows, macOS, Linux) - no "source .env" needed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv not installed: environment variables still work
    pass

PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))   # SDK retries on rate limits / server errors
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))     # seconds per request
FAST_MODEL = os.getenv("FAST_MODEL", "")    # cheap model: classification, extraction, critique
SMART_MODEL = os.getenv("SMART_MODEL", "")  # stronger model: planning, writing


class BudgetExceeded(RuntimeError):
    """Raised before a call that would break the run's call/token budget."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class Provider(Protocol):
    def complete(self, *, model: str, system: str, messages: list[dict], tools: list[dict] | None,
                 force_tool: str | None, max_tokens: int, temperature: float | None) -> LLMResponse: ...


# ---------------------------------------------------------------- OpenAI-compatible (OpenAI, Azure, Gemini, Ollama)

class OpenAICompatProvider:
    def __init__(self, client: Any) -> None:
        self.client = client

    @staticmethod
    def _messages(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                out.append({"role": "assistant", "content": m.get("content"),
                            "tool_calls": [{"id": c.id, "type": "function",
                                            "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                                           for c in m["tool_calls"]]})
            elif m["role"] == "tool":
                body = ("ERROR: " if m.get("is_error") else "") + m["content"]
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": body})
            else:
                out.append({"role": m["role"], "content": m.get("content") or ""})
        return out

    def complete(self, *, model, system, messages, tools, force_tool, max_tokens, temperature) -> LLMResponse:
        kwargs: dict[str, Any] = {"model": model, "messages": self._messages(system, messages),
                                  "max_completion_tokens": max_tokens}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
            # "required" = must call a tool; with one tool offered that forces it, and it is the
            # most widely supported form across OpenAI-compatible servers.
            kwargs["tool_choice"] = "required" if force_tool else "auto"
        if temperature is not None:
            kwargs["temperature"] = temperature
        r = self.client.chat.completions.create(**kwargs)
        msg = r.choices[0].message
        calls = []
        for c in msg.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_unparseable_arguments": c.function.arguments}
            calls.append(ToolCall(c.id, c.function.name, args))
        u = r.usage
        return LLMResponse(msg.content or "", calls, r.choices[0].finish_reason or "",
                           getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0)


# ---------------------------------------------------------------- Anthropic (Claude)

class AnthropicProvider:
    def __init__(self, client: Any) -> None:
        self.client = client

    @staticmethod
    def _messages(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "assistant":
                blocks: list[dict] = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                           for c in m.get("tool_calls") or []]
                out.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":  # consecutive tool results share one user turn
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"],
                         "is_error": bool(m.get("is_error"))}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def complete(self, *, model, system, messages, tools, force_tool, max_tokens, temperature) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens, "messages": self._messages(messages),
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        }
        if tools:
            kwargs["tools"] = tools
            if force_tool:
                kwargs["tool_choice"] = {"type": "tool", "name": force_tool}
        if temperature is not None:
            kwargs["temperature"] = temperature
        r = self.client.messages.create(**kwargs)
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in r.content if b.type == "tool_use"]
        return LLMResponse(text, calls, r.stop_reason or "", r.usage.input_tokens, r.usage.output_tokens)


def provider_from_env(name: str = PROVIDER) -> Provider:
    """Build the provider named by LLM_PROVIDER. SDKs are imported lazily so tests need no keys."""
    if name == "anthropic":
        import anthropic
        return AnthropicProvider(anthropic.Anthropic(max_retries=RETRIES, timeout=TIMEOUT))
    import openai
    if name == "azure":
        return OpenAICompatProvider(openai.AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"], azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"), max_retries=RETRIES, timeout=TIMEOUT))
    if name == "gemini":
        return OpenAICompatProvider(openai.OpenAI(
            api_key=os.environ["GEMINI_API_KEY"],
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/", max_retries=RETRIES, timeout=TIMEOUT))
    if name == "ollama":
        return OpenAICompatProvider(openai.OpenAI(
            api_key="ollama", base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"), timeout=300.0))
    if name == "openai":
        return OpenAICompatProvider(openai.OpenAI(max_retries=RETRIES, timeout=TIMEOUT))
    raise ValueError(f"unknown LLM_PROVIDER {name!r}")


# ---------------------------------------------------------------- the one interface everything uses

@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {"calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "latency_s": round(self.latency_s, 2)}


class LLM:
    def __init__(self, provider: Provider | None = None, *, max_calls: int = 60,
                 max_output_tokens_total: int = 150_000, tracer: Tracer | None = None) -> None:
        self.provider = provider or provider_from_env()
        self.max_calls = max_calls
        self.max_output_tokens_total = max_output_tokens_total
        self.usage = Usage()
        self.tracer = tracer or Tracer.disabled()
        self._lock = threading.Lock()  # agents fan out across threads

    def chat(self, *, model: str, system: str, messages: list[dict], tools: list[dict] | None = None,
             force_tool: str | None = None, max_tokens: int = 1024, temperature: float | None = None,
             tag: str = "") -> LLMResponse:
        if not model:
            raise ValueError("No model set. Put FAST_MODEL and SMART_MODEL in .env (see docs/SETUP.md).")
        with self._lock:
            if self.usage.calls >= self.max_calls:
                raise BudgetExceeded(f"call budget of {self.max_calls} reached")
            if self.usage.output_tokens + max_tokens > self.max_output_tokens_total:
                raise BudgetExceeded("output-token budget reached")
        t0 = time.perf_counter()
        r = self.provider.complete(model=model, system=system, messages=messages, tools=tools,
                                   force_tool=force_tool, max_tokens=max_tokens, temperature=temperature)
        dt = time.perf_counter() - t0
        with self._lock:
            self.usage.calls += 1
            self.usage.input_tokens += r.input_tokens
            self.usage.output_tokens += r.output_tokens
            self.usage.latency_s += dt
        self.tracer.log("llm_call", tag=tag, model=model, latency_s=round(dt, 3), stop=r.stop_reason,
                        input_tokens=r.input_tokens, output_tokens=r.output_tokens,
                        tools_called=[c.name for c in r.tool_calls])
        return r
