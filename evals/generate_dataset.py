from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 2847
CASE_COUNT = 150

SCENARIOS = [
    {
        "cause": "database_connection_pool_exhaustion",
        "services": ["payment-service", "order-service", "profile-service"],
        "signals": ["db_pool_active_ratio", "db_acquire_timeout", "request_latency_p95"],
        "tools": ["query_metrics", "search_logs", "get_recent_deployments", "search_runbooks"],
        "evidence": ["pool_saturation", "acquire_timeout_burst", "recent_query_shape_deploy"],
        "action": "rollback_release",
    },
    {
        "cause": "downstream_timeout",
        "services": ["checkout-api", "payment-service", "order-service"],
        "signals": ["upstream_timeout_rate", "dependency_latency_p95", "request_error_rate"],
        "tools": ["query_metrics", "search_logs", "get_service_dependencies"],
        "evidence": ["timeout_burst", "single_dependency_regression", "upstream_healthy"],
        "action": "none",
    },
    {
        "cause": "cpu_saturation",
        "services": ["risk-engine", "pricing-service", "search-service"],
        "signals": ["cpu_utilization", "cpu_throttled_seconds", "request_latency_p95"],
        "tools": ["query_metrics", "search_logs", "get_recent_deployments"],
        "evidence": ["cpu_near_limit", "throttling_spike", "latency_correlation"],
        "action": "restart_deployment",
    },
    {
        "cause": "memory_leak",
        "services": ["catalog-service", "profile-service", "notification-service"],
        "signals": ["memory_working_set", "container_restarts", "gc_pause_p95"],
        "tools": ["query_metrics", "search_logs", "get_recent_deployments", "search_runbooks"],
        "evidence": ["monotonic_memory_growth", "oom_kill", "heap_regression_after_deploy"],
        "action": "rollback_release",
    },
    {
        "cause": "deployment_regression",
        "services": ["payment-service", "checkout-api", "risk-engine"],
        "signals": ["request_error_rate", "request_latency_p95", "pod_revision"],
        "tools": ["query_metrics", "search_logs", "get_recent_deployments"],
        "evidence": ["error_onset_after_deploy", "new_revision_only", "rollback_canary_healthy"],
        "action": "rollback_release",
    },
    {
        "cause": "kafka_consumer_lag",
        "services": ["ledger-consumer", "notification-consumer", "fraud-consumer"],
        "signals": ["consumer_group_lag", "consume_rate", "partition_skew"],
        "tools": ["query_metrics", "search_logs", "get_service_dependencies", "search_runbooks"],
        "evidence": ["lag_growth", "consume_rate_drop", "hot_partition"],
        "action": "restart_deployment",
    },
    {
        "cause": "retry_storm",
        "services": ["payment-service", "checkout-api", "inventory-service"],
        "signals": ["retry_rate", "request_volume", "dependency_error_rate"],
        "tools": ["query_metrics", "search_logs", "get_service_dependencies"],
        "evidence": ["retry_amplification", "dependency_5xx", "queue_growth"],
        "action": "none",
    },
    {
        "cause": "cache_penetration",
        "services": ["catalog-service", "pricing-service", "profile-service"],
        "signals": ["cache_hit_ratio", "database_qps", "request_latency_p95"],
        "tools": ["query_metrics", "search_logs", "get_service_dependencies", "search_runbooks"],
        "evidence": ["cache_hit_collapse", "database_qps_spike", "repeated_missing_keys"],
        "action": "none",
    },
]

QUESTION_TEMPLATES = [
    "Why did {service} latency spike today?",
    "Investigate the sudden production errors in {service}.",
    "What is causing the regression in {service}?",
    "{service} looks unhealthy after 14:00. Find the root cause.",
    "为什么 {service} 今天突然变慢？",
]


def build_cases() -> list[dict]:
    randomizer = random.Random(SEED)
    cases = []
    for index in range(CASE_COUNT):
        scenario = SCENARIOS[index % len(SCENARIOS)]
        service = randomizer.choice(scenario["services"])
        shuffled_evidence = list(scenario["evidence"])
        randomizer.shuffle(shuffled_evidence)
        distractor = randomizer.choice(
            [item for item in SCENARIOS if item["cause"] != scenario["cause"]]
        )
        cases.append(
            {
                "id": f"eval-{index + 1:03d}",
                "question": randomizer.choice(QUESTION_TEMPLATES).format(service=service),
                "service": service,
                "root_cause": scenario["cause"],
                "acceptable_top3": [
                    scenario["cause"],
                    distractor["cause"],
                ],
                "required_tools": scenario["tools"],
                "relevant_evidence": shuffled_evidence,
                "distractor_evidence": randomizer.sample(distractor["evidence"], 2),
                "recommended_action": scenario["action"],
                "requires_approval": scenario["action"]
                in {"rollback_release", "restart_deployment"},
                "fault_seed": SEED + index,
            }
        )
    return cases


def main() -> None:
    output = Path(__file__).parent / "dataset" / "incidents.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for case in build_cases():
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"wrote {CASE_COUNT} cases to {output}")


if __name__ == "__main__":
    main()
