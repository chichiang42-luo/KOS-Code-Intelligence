from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from .service import KosService

COMMANDS = {
    "resolve": "resolve",
    "who_calls": "who_calls",
    "calls": "calls",
    "impact": "impact",
    "read_plan": "read_plan",
    "pack": "pack",
}


def run_evaluation(service: KosService, cases_path: Path) -> dict[str, Any]:
    suite = json.loads(cases_path.read_text(encoding="utf-8"))
    if suite.get("version") != 1 or not isinstance(suite.get("cases"), list):
        raise ValueError("evaluation file must contain version=1 and a cases array")
    update = service.update()
    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    for case in suite["cases"]:
        started = time.perf_counter()
        command = case.get("command", "pack")
        method_name = COMMANDS.get(command)
        if not method_name:
            raise ValueError(f"unknown evaluation command: {command}")
        result = getattr(service, method_name)(case["query"])
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)
        failures = evaluate_case(case, result)
        results.append(
            {
                "id": case["id"],
                "passed": not failures,
                "latency_ms": round(latency_ms, 3),
                "failures": failures,
            }
        )
    passed = sum(1 for item in results if item["passed"])
    return {
        "status": "ok" if passed == len(results) else "failed",
        "suite_version": 1,
        "index": update,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "p50_ms": round(percentile(latencies, 50), 3),
            "p95_ms": round(percentile(latencies, 95), 3),
        },
        "cases": results,
    }


def evaluate_case(case: dict[str, Any], result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected_status = case.get("expected_status", "ok")
    if result.get("status") != expected_status:
        failures.append(f"status: expected {expected_status}, got {result.get('status')}")
    expected_target = case.get("target_fqname")
    actual_target = (result.get("target") or {}).get("fqname")
    if expected_target and actual_target != expected_target:
        failures.append(f"target: expected {expected_target}, got {actual_target}")
    actual_files = {item.get("file_path") for item in result.get("files", [])}
    for file_path in case.get("required_files", []):
        if file_path not in actual_files:
            failures.append(f"missing file: {file_path}")
    actual_facts = {
        (
            item.get("rel_type"),
            (item.get("src") or {}).get("fqname"),
            (item.get("dst") or {}).get("fqname"),
        )
        for item in result.get("facts", [])
    }
    for fact in case.get("required_facts", []):
        expected = (fact.get("rel_type"), fact.get("src_fqname"), fact.get("dst_fqname"))
        if expected not in actual_facts:
            failures.append(f"missing fact: {expected}")
    return failures


def percentile(values: list[float], percent: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil((percent / 100) * len(ordered)) - 1)
    return ordered[index]
