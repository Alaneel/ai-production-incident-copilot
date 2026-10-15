import asyncio
import unittest
from unittest.mock import AsyncMock
from datetime import UTC, datetime, timedelta

from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import (
    ApprovalDecision,
    ApprovalRequest,
    IncidentQuestion,
    IncidentStatus,
    Hypothesis,
    Evidence,
    EvidenceKind,
    WorkflowStep,
)
from incident_copilot.tools import DemoTelemetryTools


class LangGraphIncidentWorkflowTests(unittest.TestCase):
    def test_profile_citations_and_selection_policy_survive_checkpoint(self) -> None:
        async def scenario() -> None:
            workflow = self.build_workflow()
            state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
            retained = await workflow.get(state.incident_id)
            cited = [item for item in retained.evidence if item.id in retained.hypotheses[0].evidence_ids]
            metric = next(item for item in cited if item.kind == EvidenceKind.METRIC)
            self.assertEqual(metric.query, "db_pool_active_ratio")
            self.assertTrue(any(item.kind == EvidenceKind.RUNBOOK for item in cited))
            selection = next(event for event in retained.trace if event.event == "evidence_selection")
            self.assertEqual(selection.payload["profile"], "database_connection_pool_exhaustion")
            self.assertEqual(selection.payload["selected_ids"], retained.hypotheses[0].evidence_ids)
            self.assertIn("not a validated causal conclusion", " ".join(retained.hypotheses[0].validation_notes))

        asyncio.run(scenario())

    def test_anomaly_window_is_derived_from_retained_metric_timestamps(self) -> None:
        async def scenario() -> None:
            workflow = self.build_workflow()
            now = datetime.now(UTC)
            timestamps = [now - timedelta(minutes=5-index) for index in range(5)]
            metric = Evidence(
                kind=EvidenceKind.METRIC, source="prometheus-via-telemetry-gateway",
                title="pool", observation="sampled pool saturation",
                observed_at=now, service="payment-service", score=0.9,
                query="db_pool_active_ratio",
                attributes={"series": [
                    {"timestamp": timestamp.isoformat(), "value": value}
                    for timestamp, value in zip(timestamps, [0.4, 0.4, 1.0, 1.0, 0.4])
                ]},
            )
            workflow.tools.query_metrics = AsyncMock(side_effect=lambda service, metrics: [metric]
                if service == metric.service and metric.query in metrics else [])
            state = await workflow.start(IncidentQuestion(
                question="Investigate payment-service database pool saturation",
            ))
            self.assertEqual(state.anomalous_window, (timestamps[2], timestamps[3]))
            event = next(e for e in state.trace if e.event == "anomaly_windows_detected")
            self.assertEqual(event.payload["windows"][0]["evidence_id"], metric.id)
            retained = await workflow.get(state.incident_id)
            self.assertEqual(retained.anomalous_window, state.anomalous_window)

        asyncio.run(scenario())

    def test_model_can_investigate_unclassified_signals_without_default_rollback(self) -> None:
        class Generator:
            async def generate(self, *, question, incident_type, evidence):
                self.incident_type = incident_type
                metric = next(e for e in evidence if e.kind == EvidenceKind.METRIC)
                log = next(e for e in evidence if e.kind == EvidenceKind.LOG)
                return [Hypothesis(
                    rank=1, cause="Unclassified request scheduling delay",
                    confidence=0.6, evidence_ids=[metric.id, log.id],
                )], {"provider": "test", "model": "test"}

        async def scenario() -> None:
            workflow = self.build_workflow()
            generator = Generator()
            workflow.hypothesis_generator = generator
            latency = Evidence(
                kind=EvidenceKind.METRIC, source="prometheus-via-telemetry-gateway",
                title="latency", observation="slow requests",
                observed_at=datetime.now(UTC), service="payment-service", score=0.9,
                query="request_latency_p95", attributes={"series": [{"timestamp": datetime.now(UTC).isoformat(), "value": 3500}]},
            )
            workflow.tools.query_metrics = AsyncMock(side_effect=lambda service, metrics: [latency]
                if service == latency.service and latency.query in metrics else [])
            search = workflow.tools.search_incident_knowledge
            workflow.tools.search_incident_knowledge = AsyncMock(wraps=search)
            workflow.tools.rollback_release = AsyncMock()
            workflow.tools.restart_deployment = AsyncMock()
            question = "Why is payment-service slow today?"
            state = await workflow.start(IncidentQuestion(question=question))
            self.assertIsNone(generator.incident_type)
            self.assertIsNone(state.incident_type)
            self.assertEqual(state.hypotheses[0].cause, "Unclassified request scheduling delay")
            self.assertEqual(state.status, IncidentStatus.DIAGNOSED)
            self.assertEqual(state.remediation.tool, "none")
            self.assertFalse(state.remediation.requires_approval)
            workflow.tools.rollback_release.assert_not_awaited()
            workflow.tools.restart_deployment.assert_not_awaited()
            workflow.tools.search_incident_knowledge.assert_awaited_once_with("payment-service", question)

        asyncio.run(scenario())

    def test_generic_questions_do_not_force_a_cause_from_normal_or_unknown_signals(self) -> None:
        async def scenario() -> None:
            for latency in (120, 3500):
                with self.subTest(latency=latency):
                    workflow = self.build_workflow()
                    values = {
                        "request_latency_p95": latency, "request_error_rate": 0.001,
                        "db_pool_active_ratio": 0.4, "cpu_utilization": 0.3,
                        "memory_working_set": 512 * 1024 * 1024,
                        "kafka_consumer_lag": 0, "retry_rate": 0, "cache_miss_rate": 0,
                    }
                    samples = [
                        Evidence(
                            kind=EvidenceKind.METRIC, source="prometheus-via-telemetry-gateway",
                            title=name, observation="live metric series",
                            observed_at=datetime.now(UTC), service="payment-service",
                            score=0.9, query=name,
                            attributes={"series": [{"value": value}] * 5},
                        ) for name, value in values.items()
                    ]
                    workflow.tools.query_metrics = AsyncMock(side_effect=lambda service, metrics: [
                        item for item in samples if item.service == service and item.query in metrics
                    ])
                    log = Evidence(
                        kind=EvidenceKind.LOG, source="loki-via-telemetry-gateway",
                        title="logs", observation="normal request events",
                        observed_at=datetime.now(UTC), service="payment-service", score=0.9,
                        attributes={"events": [{"message": "request completed"}]},
                    )
                    workflow.tools.search_logs = AsyncMock(side_effect=lambda service, query: [log]
                        if service == log.service else [])
                    workflow.tools.rollback_release = AsyncMock()
                    state = await workflow.start(IncidentQuestion(
                        question="Why is payment-service slow today?",
                    ))
                    self.assertEqual(state.status, IncidentStatus.INSUFFICIENT_EVIDENCE)
                    self.assertIsNone(state.incident_type)
                    self.assertEqual(state.hypotheses, [])
                    self.assertEqual(state.remediation.tool, "none")
                    self.assertIn("does not match", state.remediation.expected_result)
                    workflow.tools.rollback_release.assert_not_awaited()
                    self.assertEqual(sum(e.kind == EvidenceKind.METRIC for e in state.evidence), 8)

        asyncio.run(scenario())

    def test_rollback_target_requires_latest_primary_service_deployment(self) -> None:
        now = datetime.now(UTC)

        def deployment(previous="141", **changes):
            return Evidence(
                kind=changes.get("kind", EvidenceKind.DEPLOYMENT),
                source="deployment-catalog", title="release", observation="release metadata",
                observed_at=changes.get("observed_at", now),
                service=changes.get("service", "payment-service"),
                score=changes.get("score", 0.9),
                attributes={"revision": "142", "previous_revision": previous},
            )

        blocked = [
            [], [deployment(None)], [deployment("141; restart")],
            [deployment("142")], [deployment("141", service="ledger-service")],
            [deployment("141", kind=EvidenceKind.RUNBOOK)],
            [deployment("141", score=0)],
            [deployment("141", observed_at=now - timedelta(minutes=10)), deployment(None)],
            [deployment("140"), deployment("141")],
        ]

        async def scenario() -> None:
            for evidence in blocked:
                with self.subTest(evidence=[e.attributes for e in evidence]):
                    workflow = self.build_workflow()
                    workflow.tools.get_recent_deployments = AsyncMock(return_value=evidence)
                    workflow.tools.rollback_release = AsyncMock()
                    state = await workflow.start(IncidentQuestion(
                        question="Investigate payment-service latency regression",
                    ))
                    self.assertEqual(state.status, IncidentStatus.REMEDIATION_FAILED)
                    self.assertFalse(state.remediation.dry_run_passed)
                    self.assertNotIn("revision", state.remediation.arguments)
                    workflow.tools.rollback_release.assert_not_awaited()
                    self.assertFalse(any(e.step == WorkflowStep.HUMAN_APPROVAL for e in state.trace))
            workflow = self.build_workflow()
            workflow.tools.get_recent_deployments = AsyncMock(return_value=[
                deployment("139", observed_at=now - timedelta(minutes=10)),
                deployment("140"),
            ])
            original = workflow.tools.rollback_release
            workflow.tools.rollback_release = AsyncMock(wraps=original)
            state = await workflow.start(IncidentQuestion(
                question="Investigate payment-service latency regression",
            ))
            self.assertEqual(state.status, IncidentStatus.WAITING_APPROVAL)
            self.assertEqual(state.remediation.arguments["revision"], "140")
            self.assertEqual(workflow.tools.rollback_release.call_args.kwargs["revision"], "140")

        asyncio.run(scenario())

    def test_failed_or_malformed_dry_run_never_interrupts_for_approval(self) -> None:
        async def scenario() -> None:
            for tool, question in (
                ("rollback_release", "Investigate payment-service latency regression"),
                ("restart_deployment", "Investigate risk-engine CPU saturation"),
            ):
                for result in ({"accepted": False}, {"accepted": "false"},
                               {"accepted": 1}, {}, {"accepted": None}):
                    with self.subTest(tool=tool, result=result):
                        workflow = self.build_workflow()
                        operation = AsyncMock(return_value=result)
                        setattr(workflow.tools, tool, operation)
                        state = await workflow.start(IncidentQuestion(question=question))
                        self.assertEqual(state.status, IncidentStatus.REMEDIATION_FAILED)
                        self.assertEqual(state.current_step, WorkflowStep.COMPLETE)
                        self.assertFalse(state.remediation.dry_run_passed)
                        operation.assert_awaited_once()
                        self.assertTrue(operation.call_args.kwargs["dry_run"])
                        self.assertIsNone(state.approval_decision)
                        self.assertFalse(any(e.step in {WorkflowStep.HUMAN_APPROVAL,
                                                        WorkflowStep.EXECUTE}
                                             for e in state.trace))
                        self.assertTrue(any(e.event == "remediation_blocked" for e in state.trace))
                        stored = await workflow.get(state.incident_id)
                        self.assertEqual(stored.status, IncidentStatus.REMEDIATION_FAILED)

        asyncio.run(scenario())

    def test_execution_acknowledgement_without_verified_recovery_stays_pending(self) -> None:
        cases = [
            ({"accepted": False, "executed": False}, IncidentStatus.REMEDIATION_FAILED),
            ({"accepted": False, "executed": True}, IncidentStatus.REMEDIATION_FAILED),
            ({"accepted": "false", "executed": "true"}, IncidentStatus.REMEDIATION_FAILED),
            ({"accepted": True, "executed": False}, IncidentStatus.REMEDIATION_PENDING),
            ({"accepted": True}, IncidentStatus.REMEDIATION_PENDING),
            ({"accepted": True, "executed": True}, IncidentStatus.REMEDIATION_PENDING),
        ]

        async def scenario() -> None:
            for tool, question in (
                ("rollback_release", "Investigate payment-service latency regression"),
                ("restart_deployment", "Investigate risk-engine CPU saturation"),
            ):
                for result, expected in cases:
                    with self.subTest(tool=tool, result=result):
                        workflow = self.build_workflow()
                        paused = await workflow.start(IncidentQuestion(question=question))
                        operation = AsyncMock(return_value=result)
                        setattr(workflow.tools, tool, operation)
                        completed = await workflow.resume_with_approval(
                            paused.incident_id,
                            ApprovalRequest(
                                decision=ApprovalDecision.APPROVE,
                                actor="oncall@example.com",
                                reason="Reviewed evidence and dry-run",
                                idempotency_key=f"outcome-{paused.incident_id}",
                            ),
                        )
                        self.assertEqual(completed.status, expected)
                        self.assertEqual(completed.current_step, WorkflowStep.COMPLETE)
                        operation.assert_awaited_once()
                        call = operation.call_args.kwargs
                        self.assertFalse(call["dry_run"])
                        self.assertTrue(call["approval_receipt"])
                        event = next(e for e in reversed(completed.trace)
                                     if e.event == "workflow_completed")
                        self.assertEqual(event.payload["executed"], result.get("accepted") is True and result.get("executed") is True)
                        self.assertFalse(event.payload["recovery_verified"])
                        retained = await workflow.get(paused.incident_id)
                        self.assertEqual(retained.status, expected)

        asyncio.run(scenario())

    def test_absent_telemetry_abstains_without_model_or_destructive_tools(self) -> None:
        async def scenario() -> None:
            from datetime import UTC, datetime

            for explicit in (False, True):
                with self.subTest(explicit_signal=explicit):
                    tools = DemoTelemetryTools()
                    tools.query_metrics = AsyncMock(return_value=[Evidence(
                        kind=EvidenceKind.METRIC, source="prometheus",
                        title="unavailable", observation="No valid samples",
                        observed_at=datetime.now(UTC), service="payment-service",
                        score=0.95 if explicit else 0.0,
                        attributes={"series": (
                            [{"value": "NaN"}, {"value": True}, {}]
                            if explicit else []
                        )},
                    )])
                    tools.search_logs = AsyncMock(return_value=[Evidence(
                        kind=EvidenceKind.LOG, source="loki",
                        title="empty logs", observation="0 matching events",
                        observed_at=datetime.now(UTC), service="payment-service",
                        score=0.2, attributes={"events": (
                            [{}, {"message": ""}] if explicit else []
                        )},
                    )])
                    tools.rollback_release = AsyncMock()
                    tools.restart_deployment = AsyncMock()
                    generator = AsyncMock()
                    workflow = LangGraphIncidentWorkflow(
                        tools=tools, checkpointer=InMemorySaver(),
                        approval_signer=ApprovalReceiptSigner("local-approval-secret-change-me"),
                        hypothesis_generator=generator,
                    )
                    state = await workflow.start(IncidentQuestion(question=(
                        "Investigate payment-service database pool exhaustion"
                        if explicit else "Why is payment-service slow today?"
                    )))
                    self.assertEqual(state.status, IncidentStatus.INSUFFICIENT_EVIDENCE)
                    self.assertEqual(state.current_step, WorkflowStep.COMPLETE)
                    self.assertIsNone(state.incident_type)
                    self.assertEqual(state.hypotheses, [])
                    self.assertEqual(state.remediation.tool, "none")
                    self.assertFalse(state.remediation.requires_approval)
                    self.assertFalse(state.remediation.dry_run_passed)
                    self.assertTrue(any(e.event == "diagnosis_abstained" for e in state.trace))
                    self.assertTrue(any(e.kind == EvidenceKind.RUNBOOK for e in state.evidence))
                    self.assertTrue(any(e.kind == EvidenceKind.TRACE for e in state.evidence))
                    generator.generate.assert_not_awaited()
                    tools.rollback_release.assert_not_awaited()
                    tools.restart_deployment.assert_not_awaited()
                    stored = await workflow.get(state.incident_id)
                    self.assertEqual(stored.status, IncidentStatus.INSUFFICIENT_EVIDENCE)
                    self.assertEqual(stored.hypotheses, [])

        asyncio.run(scenario())

    def build_workflow(self) -> LangGraphIncidentWorkflow:
        return LangGraphIncidentWorkflow(
            tools=DemoTelemetryTools(),
            checkpointer=InMemorySaver(),
            approval_signer=ApprovalReceiptSigner("local-approval-secret-change-me"),
        )

    def test_graph_persists_interrupt_and_resumes_with_same_thread(self) -> None:
        async def scenario() -> None:
            workflow = self.build_workflow()
            paused = await workflow.start(
                IncidentQuestion(question="为什么 payment-service 今天延迟突然升高？")
            )
            self.assertEqual(paused.status, IncidentStatus.WAITING_APPROVAL)
            self.assertEqual(paused.current_step, WorkflowStep.HUMAN_APPROVAL)
            self.assertIsNone(paused.approval_decision)
            self.assertTrue(
                any(event.event == "workflow_interrupted" for event in paused.trace)
            )

            stored = await workflow.get(paused.incident_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.trace_id, paused.trace_id)

            resumed = await workflow.resume_with_approval(
                paused.incident_id,
                ApprovalRequest(
                    decision=ApprovalDecision.APPROVE,
                    actor="oncall@example.com",
                    reason="Dry-run and cited evidence reviewed.",
                    idempotency_key="langgraph-approval-0001",
                ),
            )
            self.assertEqual(resumed.status, IncidentStatus.REMEDIATION_PENDING)
            self.assertEqual(resumed.recovery["status"], "pending")
            self.assertEqual(resumed.current_step, WorkflowStep.COMPLETE)
            self.assertEqual(resumed.approval_actor, "oncall@example.com")
            self.assertTrue(
                any(event.event == "human_decision" for event in resumed.trace)
            )
            self.assertTrue(
                any(event.event == "workflow_completed" for event in resumed.trace)
            )

            history = await workflow.history(paused.incident_id)
            self.assertGreaterEqual(len(history), 10)

        asyncio.run(scenario())

    def test_rejection_completes_without_execution(self) -> None:
        async def scenario() -> None:
            workflow = self.build_workflow()
            paused = await workflow.start(
                IncidentQuestion(
                    question="Investigate payment-service latency regression."
                )
            )
            rejected = await workflow.resume_with_approval(
                paused.incident_id,
                ApprovalRequest(
                    decision=ApprovalDecision.REJECT,
                    actor="incident-commander@example.com",
                    reason="Use traffic shifting instead.",
                    idempotency_key="langgraph-reject-0001",
                ),
            )
            self.assertEqual(rejected.status, IncidentStatus.REJECTED)
            self.assertFalse(
                any(event.step == WorkflowStep.EXECUTE for event in rejected.trace)
            )

        asyncio.run(scenario())

    def test_optional_model_generator_is_recorded_in_trace(self) -> None:
        class StubGenerator:
            async def generate(self, *, question, incident_type, evidence):
                metric = next(item for item in evidence if item.kind.value == "metric")
                log = next(item for item in evidence if item.kind.value == "log")
                return (
                    [
                        Hypothesis(
                            rank=1,
                            cause="Model-generated evidence-bound diagnosis",
                            confidence=0.82,
                            evidence_ids=[metric.id, log.id],
                            validation_notes=["Two retained evidence kinds agree."],
                        )
                    ],
                    {
                        "provider": "openai",
                        "model": "configured-test-model",
                        "response_id": "resp_test_workflow",
                        "total_tokens": 144,
                    },
                )

            async def close(self):
                return None

        async def scenario() -> None:
            workflow = LangGraphIncidentWorkflow(
                tools=DemoTelemetryTools(),
                checkpointer=InMemorySaver(),
                approval_signer=ApprovalReceiptSigner(
                    "local-approval-secret-change-me"
                ),
                hypothesis_generator=StubGenerator(),
            )
            paused = await workflow.start(
                IncidentQuestion(question="Why did payment-service latency regress?")
            )
            self.assertEqual(
                paused.hypotheses[0].cause,
                "Model-generated evidence-bound diagnosis",
            )
            model_events = [
                event for event in paused.trace if event.event == "model_generation"
            ]
            self.assertEqual(len(model_events), 1)
            self.assertEqual(
                model_events[0].payload["response_id"],
                "resp_test_workflow",
            )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
