import asyncio
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.incident_profiles import (
    PROFILES,
    classify_question,
    infer_profile_from_evidence,
)
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import (
    ApprovalDecision,
    ApprovalRequest,
    IncidentQuestion,
    IncidentStatus,
    Evidence,
    EvidenceKind,
    WorkflowStep,
)
from incident_copilot.tools import DemoTelemetryTools

ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = json.loads(
    (ROOT / "fault-lab" / "scenarios.json").read_text(encoding="utf-8")
)
EXPECTED = {
    "db-pool-exhaustion": (
        "database_connection_pool_exhaustion",
        "rollback_release",
    ),
    "downstream-timeout": ("downstream_timeout", "none"),
    "cpu-spike": ("cpu_saturation", "restart_deployment"),
    "memory-leak": ("memory_leak", "rollback_release"),
    "deployment-regression": ("deployment_regression", "rollback_release"),
    "kafka-consumer-lag": ("kafka_consumer_lag", "restart_deployment"),
    "retry-storm": ("retry_storm", "none"),
    "cache-penetration": ("cache_penetration", "none"),
}


class IncidentProfileWorkflowTests(unittest.TestCase):
    def test_generic_question_is_classified_from_current_signals(self) -> None:
        fallback = PROFILES["database_connection_pool_exhaustion"]

        def metric(name: str, values: list[float]) -> Evidence:
            return Evidence(
                kind=EvidenceKind.METRIC,
                source="prometheus",
                title=name,
                observation="bounded signal",
                observed_at=datetime.now(UTC),
                service="example-service",
                score=0.9,
                query=name,
                attributes={
                    "series": [
                        {"timestamp": str(index), "value": value}
                        for index, value in enumerate(values)
                    ]
                },
            )

        def log(message: str) -> Evidence:
            return Evidence(
                kind=EvidenceKind.LOG,
                source="loki",
                title="logs",
                observation=message,
                observed_at=datetime.now(UTC),
                service="example-service",
                score=0.9,
                attributes={"events": [{"message": message, "timestamp": datetime.now(UTC).isoformat()}]},
            )

        cases = {
            "database_connection_pool_exhaustion": [
                metric("db_pool_active_ratio", [0.95, 1.0])
            ],
            "downstream_timeout": [log("downstream request timed out")],
            "cpu_saturation": [metric("cpu_utilization", [0.91, 0.94])],
            "memory_leak": [
                metric(
                    "memory_working_set",
                    [0, 2 * 1024 * 1024, 4 * 1024 * 1024],
                )
            ],
            "deployment_regression": [
                log("release regression: invalid ledger response shape")
            ],
            "kafka_consumer_lag": [metric("kafka_consumer_lag", [3000, 4800])],
            "retry_storm": [metric("retry_rate", [30, 38])],
            "cache_penetration": [metric("cache_miss_rate", [200, 210])],
        }
        for expected, evidence in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(
                    infer_profile_from_evidence(evidence, fallback).key,
                    expected,
                )

    def test_memory_leak_requires_sustained_growth(self) -> None:
        mib = 1024 * 1024
        cases = [
            ("stable high working set", [512, 512, 512, 512], False),
            ("cache warming plateau", [128, 256, 256, 256, 256, 256, 256], False),
            ("temporary spike", [128, 128, 512, 128, 128], False),
            ("garbage collection", [128, 256, 192, 320, 256], False),
            ("single jump", [128, 128, 256], False),
            ("insufficient samples", [128, 256], False),
            ("small relative growth", [512, 514, 516, 518], False),
            ("sustained growth", [128, 160, 192, 224, 256], True),
            ("allocation plateaus", [128, 160, 160, 192, 192], True),
        ]
        for name, samples, expected in cases:
            with self.subTest(case=name):
                evidence = Evidence(
                    kind=EvidenceKind.METRIC,
                    source="prometheus",
                    title="memory working set",
                    observation="bounded memory samples",
                    observed_at=datetime.now(UTC),
                    service="payment-service",
                    score=0.9,
                    query="memory_working_set",
                    attributes={
                        "series": [{"value": sample * mib} for sample in samples]
                    },
                )
                profile = infer_profile_from_evidence(
                    [evidence], PROFILES["downstream_timeout"]
                )
                self.assertEqual(profile.key == "memory_leak", expected)

    def test_bad_metric_sample_does_not_discard_valid_current_signals(self) -> None:
        for bad_point in (
            {},
            {"value": None},
            {"value": "broken"},
            {"value": float("nan")},
            {"value": float("inf")},
        ):
            with self.subTest(point=bad_point):
                evidence = Evidence(
                    kind=EvidenceKind.METRIC,
                    source="prometheus",
                    title="CPU",
                    observation="current CPU pressure",
                    observed_at=datetime.now(UTC),
                    service="payment-service",
                    score=0.9,
                    query="cpu_utilization",
                    attributes={
                        "series": [{"value": 0.91}, bad_point, {"value": 0.94}]
                    },
                )
                self.assertEqual(
                    infer_profile_from_evidence(
                        [evidence], PROFILES["database_connection_pool_exhaustion"]
                    ).key,
                    "cpu_saturation",
                )

    def test_old_deployment_log_does_not_override_current_cache_signal(
        self,
    ) -> None:
        now = datetime.now(UTC)
        evidence = [
            Evidence(
                kind=EvidenceKind.METRIC,
                source="prometheus",
                title="cache",
                observation="current cache misses",
                observed_at=now,
                service="payment-service",
                score=0.9,
                query="cache_miss_rate",
                attributes={
                    "series": [
                        {"timestamp": "1", "value": 2},
                        {"timestamp": "2", "value": 2},
                    ]
                },
            ),
            Evidence(
                kind=EvidenceKind.LOG,
                source="loki",
                title="logs",
                observation="bounded log window",
                observed_at=now,
                service="payment-service",
                score=0.9,
                attributes={
                    "events": [
                        {"message": "release regression: invalid ledger response shape"}
                    ]
                },
            ),
        ]
        self.assertEqual(
            infer_profile_from_evidence(
                evidence,
                PROFILES["database_connection_pool_exhaustion"],
            ).key,
            "cache_penetration",
        )

    def test_all_fault_lab_questions_reach_the_expected_workflow_outcome(
        self,
    ) -> None:
        async def scenario() -> None:
            for name, source in SCENARIOS.items():
                with self.subTest(scenario=name):
                    expected_profile, expected_action = EXPECTED[name]
                    profile = classify_question(source["question"])
                    self.assertEqual(profile.key, expected_profile)

                    workflow = LangGraphIncidentWorkflow(
                        tools=DemoTelemetryTools(),
                        checkpointer=InMemorySaver(),
                        approval_signer=ApprovalReceiptSigner(
                            "local-approval-secret-change-me"
                        ),
                    )
                    state = await workflow.start(
                        # This suite covers a fault on its explicitly selected
                        # service. Multi-service complaint/source separation is
                        # exercised by test_cross_service_workflow instead.
                        IncidentQuestion(question=f"Investigate {source['service']}: {source['question']}")
                    )
                    self.assertEqual(state.incident_type, expected_profile)
                    self.assertEqual(state.services[0], source["service"])
                    self.assertEqual(
                        state.hypotheses[0].cause,
                        PROFILES[expected_profile].cause,
                    )
                    self.assertIsNotNone(state.remediation)
                    self.assertEqual(state.remediation.tool, expected_action)
                    self.assertGreaterEqual(
                        len(state.hypotheses[0].evidence_ids),
                        2,
                    )

                    if expected_action == "none":
                        self.assertEqual(
                            state.status,
                            IncidentStatus.DIAGNOSED,
                        )
                        self.assertEqual(
                            state.current_step,
                            WorkflowStep.COMPLETE,
                        )
                        self.assertFalse(state.remediation.requires_approval)
                    else:
                        self.assertEqual(
                            state.status,
                            IncidentStatus.WAITING_APPROVAL,
                        )
                        self.assertEqual(
                            state.current_step,
                            WorkflowStep.HUMAN_APPROVAL,
                        )
                        self.assertTrue(state.remediation.requires_approval)
                        rejected = await workflow.resume_with_approval(
                            state.incident_id,
                            ApprovalRequest(
                                decision=ApprovalDecision.REJECT,
                                actor="test-commander@example.com",
                                reason="Scenario coverage does not execute changes.",
                                idempotency_key=f"reject-{name}-0001",
                            ),
                        )
                        self.assertEqual(
                            rejected.status,
                            IncidentStatus.REJECTED,
                        )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
