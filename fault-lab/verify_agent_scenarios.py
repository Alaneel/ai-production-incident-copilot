#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from faultctl import SCENARIOS, clear, exercise, inject, request_json

EXPECTED = {
    "db-pool-exhaustion": (
        "database_connection_pool_exhaustion",
        "Database connection pool exhaustion",
        "rollback_release",
    ),
    "downstream-timeout": (
        "downstream_timeout",
        "Downstream dependency timeout",
        "none",
    ),
    "cpu-spike": (
        "cpu_saturation",
        "CPU saturation and request throttling",
        "restart_deployment",
    ),
    "memory-leak": (
        "memory_leak",
        "Monotonic application memory leak",
        "rollback_release",
    ),
    "deployment-regression": (
        "deployment_regression",
        "Release regression after deployment",
        "rollback_release",
    ),
    "kafka-consumer-lag": (
        "kafka_consumer_lag",
        "Kafka consumer processing slowdown",
        "restart_deployment",
    ),
    "retry-storm": (
        "retry_storm",
        "Downstream retry amplification storm",
        "none",
    ),
    "cache-penetration": (
        "cache_penetration",
        "Cache penetration from repeated missing keys",
        "none",
    ),
}

MINIMUM_REQUESTS = {
    "cpu-spike": 8,
    "memory-leak": 12,
    "cache-penetration": 12,
}

# Trend classification needs several distinct Prometheus scrapes. A burst of
# allocations followed by one scrape is not evidence of sustained growth.
MINIMUM_WARMUP_SECONDS = {"memory-leak": 25}


def api_headers(*, operator: bool = False) -> dict[str, str]:
    mode = os.getenv("ORCHESTRATOR_AUTH_MODE", "disabled")
    if mode == "disabled":
        return {}
    if mode != "required":
        raise ValueError("ORCHESTRATOR_AUTH_MODE must be disabled or required.")
    name = "ORCHESTRATOR_OPERATOR_TOKEN" if operator else "ORCHESTRATOR_READ_TOKEN"
    token = os.getenv(name, "")
    if not 32 <= len(token) <= 4096 or not all(33 <= ord(char) <= 126 for char in token):
        raise ValueError(f"Required verifier authentication needs a valid {name}.")
    return {"Authorization": f"Bearer {token}"}


def approval_actor() -> str:
    if os.getenv("ORCHESTRATOR_AUTH_MODE", "disabled") == "disabled":
        return "scenario-verifier@opspilot.local"
    actor = os.getenv("ORCHESTRATOR_OPERATOR_ACTOR", "").strip()
    if not 2 <= len(actor) <= 120:
        raise ValueError("Required verifier authentication needs ORCHESTRATOR_OPERATOR_ACTOR.")
    return actor


def diagnose(name: str) -> dict[str, Any]:
    scenario = SCENARIOS[name]
    service = scenario["service"]
    expected_type, expected_cause, expected_action = EXPECTED[name]
    question = f"What is causing the production regression in {service}?"
    started = time.perf_counter()
    incident = request_json(
        "POST",
        "http://localhost:8080/v1/incidents",
        body={"question": question, "environment": "prod-sg"},
        headers=api_headers(),
        timeout=45,
    )
    diagnostic_latency_ms = round((time.perf_counter() - started) * 1000, 2)
    primary_evidence = [item for item in incident["evidence"] if item.get("service") == service]
    leading = incident["hypotheses"][0] if incident["hypotheses"] else {}
    remediation = incident.get("remediation") or {}
    sources = {item["source"] for item in incident["evidence"]}
    kinds = {item["kind"] for item in incident["evidence"]}
    metric_queries = [
        item["query"] for item in primary_evidence if item["kind"] == "metric"
    ]
    metric_signals = {}
    for item in primary_evidence:
        if item["kind"] != "metric":
            continue
        values = [
            float(point["value"])
            for point in item.get("attributes", {}).get("series", [])
        ]
        metric_signals[item["query"]] = {
            "latest": values[-1] if values else None,
            "peak": max(values) if values else None,
            "points": len(values),
        }
    signal_events = [
        event for event in incident["trace"] if event["event"] == "signals_classified"
    ]
    traced_tools = {
        event.get("payload", {}).get("tool")
        for event in incident["trace"]
        if event["event"] in {"tool_call", "tool_result"}
    }
    expected_status = "diagnosed" if expected_action == "none" else "waiting_approval"
    checks = {
        "service": bool(incident["services"]) and incident["services"][0] == service,
        "incident_type": incident["incident_type"] == expected_type,
        "cause": leading.get("cause") == expected_cause,
        "action": remediation.get("tool") == expected_action,
        "status": incident["status"] == expected_status,
        "live_metrics": "prometheus-via-telemetry-gateway" in sources,
        "live_logs": "loki-via-telemetry-gateway" in sources,
        "live_traces": "tempo-via-telemetry-gateway" in sources,
        "retrieval": bool(kinds & {"runbook", "past_incident", "technical_doc"}),
        "signal_trace": bool(signal_events),
        "tool_trace": "query_traces" in traced_tools,
        "metric_panel": len(set(metric_queries)) == 8,
    }
    final_status = incident["status"]
    if incident["status"] == "waiting_approval":
        rejected = request_json(
            "POST",
            (
                "http://localhost:8080/v1/incidents/"
                f"{incident['incident_id']}/approval"
            ),
            body={
                "decision": "reject",
                "actor": approval_actor(),
                "reason": "Automated verification never executes production changes.",
                "idempotency_key": f"verify-{name}-{incident['incident_id']}",
            },
            timeout=30,
            headers=api_headers(operator=True),
        )
        final_status = rejected["status"]
        checks["rejection_prevented_execution"] = (
            final_status == "rejected"
            and not any(
                event["step"] == "execute_or_simulate" for event in rejected["trace"]
            )
        )

    return {
        "scenario": name,
        "incident_id": incident["incident_id"],
        "generic_question": question,
        "service": incident["services"][0] if incident["services"] else None,
        "incident_type": incident["incident_type"],
        "leading_cause": leading.get("cause"),
        "confidence": leading.get("confidence"),
        "action": remediation.get("tool"),
        "candidate_incident_types": incident.get("candidate_incident_types", []),
        "service_impacts": incident.get("service_impacts", []),
        "query_failures": [event.get("payload", {}) for event in incident["trace"] if event["event"] == "tool_error"],
        "initial_status": incident["status"],
        "final_status": final_status,
        "diagnostic_latency_ms": diagnostic_latency_ms,
        "evidence_count": len(incident["evidence"]),
        "evidence_kinds": sorted(kinds),
        "metric_queries": metric_queries,
        "metric_signals": metric_signals,
        "trace_events": len(incident["trace"]),
        "signal_classification": (
            signal_events[-1]["payload"] if signal_events else None
        ),
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run all eight live faults through the real Agent workflow."
    )
    parser.add_argument("--warmup-seconds", type=int, default=8)
    parser.add_argument("--settle-seconds", type=int, default=2)
    parser.add_argument("--requests", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        api_headers()
        api_headers(operator=True)
        approval_actor()
        if (os.getenv("ORCHESTRATOR_AUTH_MODE") == "required"
                and os.getenv("ORCHESTRATOR_READ_TOKEN") == os.getenv("ORCHESTRATOR_OPERATOR_TOKEN")):
            raise ValueError("Verifier read/operator credentials must be distinct.")
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    results = []
    for name, scenario in SCENARIOS.items():
        warmup_seconds = max(
            args.warmup_seconds, MINIMUM_WARMUP_SECONDS.get(name, 0)
        )
        inject(name, max(90, warmup_seconds + 45), 0.9)
        try:
            exercise(
                scenario["service"],
                max(args.requests, MINIMUM_REQUESTS.get(name, args.requests)),
            )
            time.sleep(warmup_seconds)
            result = diagnose(name)
        except Exception as error:
            result = {
                "scenario": name,
                "passed": False,
                "error": type(error).__name__,
                "reason": "Live verification failed; inspect scoped service logs for details.",
            }
        finally:
            clear(name)
        results.append(result)
        print(
            f"{name:24} "
            f"{'PASS' if result['passed'] else 'FAIL'} "
            f"{result.get('incident_type', result.get('error', ''))}"
        )
        time.sleep(args.settle_seconds)

    output = {
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "workflow": "orchestrator -> MCP -> Go gateway -> Prometheus/Loki/Tempo",
        "uses_generic_questions_without_fault_labels": True,
        "api_auth_mode": os.getenv("ORCHESTRATOR_AUTH_MODE", "disabled"),
        "scenarios": results,
        "passed": all(item["passed"] for item in results),
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
