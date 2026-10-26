"""Test settings: placeholder model names; the fake provider never calls a real API."""
import os

os.environ.setdefault("FAST_MODEL", "fast-test-model")
os.environ.setdefault("SMART_MODEL", "smart-test-model")
os.environ.setdefault("LLM_PROVIDER", "openai")

import pytest


@pytest.fixture(autouse=True)
def _reset_json_mode():
    """The JSON-mode fallback is remembered per run; start every test in tool mode."""
    from core.structured import JSON_MODE
    JSON_MODE.clear()
    yield
    JSON_MODE.clear()
