"""Connection check: python -m core.check

Sends one tiny request to the model configured in .env and explains any error in plain words.
Run this first whenever something AI-related seems stuck.
"""
from __future__ import annotations

import os
import time

from core.llm import FAST_MODEL, PROVIDER, SMART_MODEL, LLM

KEY_VARS = {"openai": "OPENAI_API_KEY", "azure": "AZURE_OPENAI_API_KEY", "gemini": "GEMINI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY", "ollama": None}


def explain(err: Exception) -> str:
    text = f"{type(err).__name__}: {err}".lower()
    if any(s in text for s in ("401", "403", "api key", "permission", "unauthenticated", "invalid_argument")):
        return "The key was rejected. Re-copy it into .env (no spaces, no quotes) and check it belongs to this provider."
    if "404" in text or "not found" in text or "model" in text and "exist" in text:
        return "The model name isn't available to your key. Check FAST_MODEL / SMART_MODEL in .env against the provider's model list."
    if "429" in text or "rate" in text or "quota" in text or "resource_exhausted" in text:
        return "Rate limit or quota reached (common on free tiers). Wait a minute, then retry with fewer parallel calls (--workers 1)."
    if "timeout" in text or "timed out" in text:
        return "The request timed out. Check your internet connection, or try again in a minute."
    if "connect" in text:
        return "Couldn't reach the provider. Check your internet connection (or that Ollama is running, for local models)."
    return "Unexpected error - send this message (without your key) for help."


def main() -> None:
    key_var = KEY_VARS.get(PROVIDER)
    print(f"provider   : {PROVIDER}")
    print(f"fast model : {FAST_MODEL or '(missing - set FAST_MODEL in .env)'}")
    print(f"smart model: {SMART_MODEL or '(missing - set SMART_MODEL in .env)'}")
    if key_var:
        val = os.getenv(key_var, "")
        print(f"{key_var}: {'set (' + str(len(val)) + ' characters)' if val else 'MISSING - add it to .env'}")
        if not val:
            return
    print("sending one test request...", flush=True)
    t0 = time.perf_counter()
    try:
        r = LLM(max_calls=1).chat(model=FAST_MODEL, system="Reply with exactly: OK",
                                  messages=[{"role": "user", "content": "ping"}], max_tokens=20)
    except Exception as e:
        print(f"FAILED after {time.perf_counter() - t0:.1f}s\n  {type(e).__name__}: {str(e)[:400]}\n  -> {explain(e)}")
        return
    print(f"SUCCESS in {time.perf_counter() - t0:.1f}s - model replied: {r.text.strip()[:50]!r}")


if __name__ == "__main__":
    main()
