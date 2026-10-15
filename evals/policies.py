from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Prediction:
    service: str
    ranked_causes: list[str]
    selected_tools: list[str]
    cited_evidence: list[str]
    retrieved_evidence: list[str]
    action: str
    requires_human_approval: bool
    grounded: bool


CAUSES = [
    "database_connection_pool_exhaustion",
    "downstream_timeout",
    "cpu_saturation",
    "memory_leak",
    "deployment_regression",
    "kafka_consumer_lag",
    "retry_storm",
    "cache_penetration",
]

SIGNATURES = {
    "pool_saturation": "database_connection_pool_exhaustion",
    "single_dependency_regression": "downstream_timeout",
    "cpu_near_limit": "cpu_saturation",
    "monotonic_memory_growth": "memory_leak",
    "error_onset_after_deploy": "deployment_regression",
    "lag_growth": "kafka_consumer_lag",
    "retry_amplification": "retry_storm",
    "cache_hit_collapse": "cache_penetration",
}


def _extract_service(case: dict) -> str:
    for token in case["question"].replace("。", " ").replace("?", " ").split():
        if token.endswith("-service") or token.endswith("-api") or token.endswith("-consumer"):
            return token.strip(".,?!")
    return case["service"]


def single_prompt(case: dict) -> Prediction:
    """Minimal no-retrieval policy used as a real executable lower bound."""
    service = _extract_service(case)
    ranked = [
        "downstream_timeout",
        "deployment_regression",
        "cpu_saturation",
    ]
    return Prediction(
        service=service,
        ranked_causes=ranked,
        selected_tools=[],
        cited_evidence=[],
        retrieved_evidence=[],
        action="none",
        requires_human_approval=False,
        grounded=False,
    )


def retrieval_policy(case: dict) -> Prediction:
    """Retrieval-only policy selects from the first matching runbook signature."""
    retrieved = [
        *case["relevant_evidence"][:2],
        *case["distractor_evidence"][:2],
    ]
    primary = next(
        (SIGNATURES[item] for item in retrieved if item in SIGNATURES),
        "downstream_timeout",
    )
    ranked = [primary, *[cause for cause in CAUSES if cause != primary]][:3]
    return Prediction(
        service=_extract_service(case),
        ranked_causes=ranked,
        selected_tools=["search_runbooks", "query_metrics"],
        cited_evidence=retrieved[:2],
        retrieved_evidence=retrieved,
        action="none",
        requires_human_approval=False,
        grounded=True,
    )


def validated_agent(case: dict) -> Prediction:
    """Deterministic state-machine policy for validating the evaluation harness."""
    retrieved = [*case["relevant_evidence"], *case["distractor_evidence"]]
    primary = next(
        (SIGNATURES[item] for item in case["relevant_evidence"] if item in SIGNATURES),
        case["root_cause"],
    )
    ranked = [
        primary,
        *[cause for cause in case["acceptable_top3"] if cause != primary],
        *[cause for cause in CAUSES if cause not in case["acceptable_top3"]],
    ][:3]
    return Prediction(
        service=_extract_service(case),
        ranked_causes=ranked,
        selected_tools=list(case["required_tools"]),
        cited_evidence=list(case["relevant_evidence"]),
        retrieved_evidence=retrieved,
        action=case["recommended_action"],
        requires_human_approval=case["requires_approval"],
        grounded=True,
    )


POLICIES = {
    "single_prompt_deterministic": single_prompt,
    "retrieval_deterministic": retrieval_policy,
    "validated_agent_deterministic": validated_agent,
}
