"""Test settings: placeholder model names; the fake provider never calls a real API."""
import os

os.environ.setdefault("FAST_MODEL", "fast-test-model")
os.environ.setdefault("SMART_MODEL", "smart-test-model")
os.environ.setdefault("LLM_PROVIDER", "openai")
