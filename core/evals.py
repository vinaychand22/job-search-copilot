"""Tiny eval harness shared by all agents.

An eval = golden cases + a function under test + scorers that return numbers.
Output: per-metric means, per-case rows, failures, and token/latency usage,
saved as JSON under evals/results/ so you can diff runs and chart progress
(the Phase 3 gate is literally "show a chart where a change moved a metric").
"""
from __future__ import annotations

import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

Scorer = Callable[[dict[str, Any], Any], dict[str, float]]


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run_eval(
    name: str,
    cases: list[dict[str, Any]],
    run_case: Callable[[dict[str, Any]], Any],
    scorer: Scorer,
    *,
    workers: int = 4,
    usage: Callable[[], dict[str, Any]] | None = None,
    out_dir: str = "evals/results",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def one(case: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            output = run_case(case)
            scores = scorer(case, output)
            error = None
        except Exception as e:  # a crash is a failed case, not a failed eval run
            output, scores, error = None, {}, f"{type(e).__name__}: {e}"
        return {"id": case.get("id"), "scores": scores, "error": error,
                "seconds": round(time.perf_counter() - t0, 2), "output": _jsonable(output)}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, cases))

    metric_names = sorted({k for r in rows for k in r["scores"]})
    metrics = {}
    for m in metric_names:
        vals = [r["scores"][m] for r in rows if m in r["scores"]]
        metrics[m] = round(statistics.mean(vals), 4) if vals else None
    report = {
        "eval": name,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": config or {},
        "n_cases": len(cases),
        "n_errors": sum(r["error"] is not None for r in rows),
        "metrics": metrics,
        "usage": usage() if usage else None,
        "cases": rows,
    }
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = Path(out_dir) / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    _print(report, path)
    return report


def _jsonable(x: Any) -> Any:
    if hasattr(x, "model_dump"):
        return x.model_dump()
    if hasattr(x, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(x)
    return x


def _print(report: dict[str, Any], path: Path) -> None:
    print(f"\n=== {report['eval']}  ({report['n_cases']} cases, {report['n_errors']} errors) ===")
    for k, v in report["metrics"].items():
        print(f"  {k:<28} {v}")
    if report["usage"]:
        print(f"  usage: {report['usage']}")
    failed = [r for r in report["cases"] if r["error"] or any(v < 1 for v in r["scores"].values())]
    for r in failed[:10]:
        print(f"  - {r['id']}: {r['error'] or {k: v for k, v in r['scores'].items() if v < 1}}")
    print(f"  saved -> {path}")
