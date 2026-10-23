"""Minimal JSONL tracer: one line per model call / tool call / decision.

Traces are how you debug agents: when an eval case fails you open its trace
and see exactly which tool returned what and why the model stopped.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class Tracer:
    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def disabled(cls) -> "Tracer":
        return cls(None)

    @classmethod
    def for_run(cls, name: str, root: str = "runs") -> "Tracer":
        return cls(Path(root) / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")

    def log(self, event: str, **data: Any) -> None:
        if not self.path:
            return
        line = json.dumps({"ts": round(time.time(), 3), "event": event, **data}, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
