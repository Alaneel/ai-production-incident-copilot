#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SCENARIOS = json.loads((ROOT / "scenarios.json").read_text())
PORTS = {
    "checkout-api": 8090,
    "payment-service": 8091,
    "ledger-service": 8092,
    "risk-engine": 8093,
}


def request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} failed ({error.code}): {detail}") from error


def service_url(service: str) -> str:
    return f"http://localhost:{PORTS[service]}"


def inject(name: str, duration: int, intensity: float) -> dict[str, Any]:
    scenario = SCENARIOS[name]
    return request_json(
        "POST",
        f"{service_url(scenario['service'])}/admin/fault",
        body={
            "mode": scenario["mode"],
            "duration_seconds": duration,
            "intensity": intensity,
            "incident_id": f"LAB-{int(time.time())}",
        },
        headers={
            "X-Fault-Token": os.getenv("FAULT_INJECTION_TOKEN", "local-fault-token")
        },
    )


def clear(name: str) -> dict[str, Any]:
    scenario = SCENARIOS[name]
    return request_json(
        "DELETE",
        f"{service_url(scenario['service'])}/admin/fault",
        headers={
            "X-Fault-Token": os.getenv("FAULT_INJECTION_TOKEN", "local-fault-token")
        },
    )


def exercise(service: str, requests: int) -> None:
    targets = ["http://localhost:8090/v1/work"]
    target = f"{service_url(service)}/v1/work"
    if target not in targets:
        targets.append(target)
    for _ in range(requests):
        for url in targets:
            try:
                request_json("POST", url, timeout=8)
            except (RuntimeError, urllib.error.URLError):
                pass


def verify(name: str) -> dict[str, Any]:
    scenario = SCENARIOS[name]
    service = scenario["service"]
    end = datetime.now(UTC) + timedelta(minutes=1)
    start = end - timedelta(minutes=10)
    metric = next(
        (
            signal
            for signal in scenario["expected_signals"]
            if signal
            in {
                "request_latency_p95",
                "db_pool_utilization",
                "error_rate",
                "cpu_load",
                "kafka_consumer_lag",
                "memory_working_set",
                "retry_rate",
                "cache_miss_rate",
            }
        ),
        "request_latency_p95",
    )
    gateway_headers = {
        "Authorization": "Bearer "
        + os.getenv("TELEMETRY_GATEWAY_TOKEN", "local-development-token")
    }
    metric_result = request_json(
        "POST",
        "http://localhost:8081/v1/metrics/query",
        body={
            "service": service,
            "metric": metric,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step_seconds": 5,
        },
        headers=gateway_headers,
    )
    log_query = next(
        (signal for signal in scenario["expected_signals"] if " " in signal),
        "error",
    )
    logs = request_json(
        "POST",
        "http://localhost:8081/v1/logs/search",
        body={
            "service": service,
            "query": log_query,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 50,
        },
        headers=gateway_headers,
    )
    traces = request_json(
        "POST",
        "http://localhost:8081/v1/traces/search",
        body={
            "service": service,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 20,
        },
        headers=gateway_headers,
    )
    points = metric_result.get("points", [])
    return {
        "scenario": name,
        "service": service,
        "metric": metric,
        "metric_points": len(points),
        "log_events": logs.get("count", 0),
        "trace_count": traces.get("count", 0),
        "passed": bool(points)
        and traces.get("count", 0) > 0
        and (
            logs.get("count", 0) > 0
            or not any(" " in item for item in scenario["expected_signals"])
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inject and verify repeatable incident fault-lab scenarios."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("list")
    inject_parser = subcommands.add_parser("inject")
    inject_parser.add_argument("scenario", choices=sorted(SCENARIOS))
    inject_parser.add_argument("--duration", type=int, default=180)
    inject_parser.add_argument("--intensity", type=float, default=0.8)
    clear_parser = subcommands.add_parser("clear")
    clear_parser.add_argument("scenario", choices=sorted(SCENARIOS))
    verify_parser = subcommands.add_parser("verify")
    verify_parser.add_argument("scenario", choices=sorted(SCENARIOS))
    verify_parser.add_argument("--warmup-seconds", type=int, default=15)
    verify_parser.add_argument("--requests", type=int, default=12)
    verify_parser.add_argument("--output", type=Path)
    verify_all_parser = subcommands.add_parser("verify-all")
    verify_all_parser.add_argument("--warmup-seconds", type=int, default=10)
    verify_all_parser.add_argument("--requests", type=int, default=4)
    verify_all_parser.add_argument("--output", type=Path)

    arguments = parser.parse_args()
    if arguments.command == "list":
        for name, scenario in SCENARIOS.items():
            print(f"{name:24} {scenario['service']:18} {scenario['question']}")
        return 0
    if arguments.command == "inject":
        print(
            json.dumps(
                inject(arguments.scenario, arguments.duration, arguments.intensity),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if arguments.command == "clear":
        print(json.dumps(clear(arguments.scenario), ensure_ascii=False, indent=2))
        return 0

    scenario_names = (
        [arguments.scenario] if arguments.command == "verify" else list(SCENARIOS)
    )
    results = []
    for scenario_name in scenario_names:
        inject(scenario_name, max(60, arguments.warmup_seconds + 30), 0.9)
        try:
            exercise(SCENARIOS[scenario_name]["service"], arguments.requests)
            time.sleep(arguments.warmup_seconds)
            results.append(verify(scenario_name))
        finally:
            clear(scenario_name)
    output: dict[str, Any] | list[dict[str, Any]]
    output = results[0] if arguments.command == "verify" else results
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n")
    print(rendered)
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
