"""Real compiled graph, synthetic scoped tools; no model/network/mutation."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.dependency_topology import DependencyEdge, ServiceTopology
from incident_copilot.cross_service_rca import assess_diagnostic_coverage
from incident_copilot.action_applicability import assess_action_applicability
from incident_copilot.guardrails import GuardrailViolation, validate_execution
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpToolError
from incident_copilot.models import ApprovalRequest, Evidence, Hypothesis, IncidentQuestion, IncidentState, Remediation
from incident_copilot.tools import DemoTelemetryTools


class DirectedTools(DemoTelemetryTools):
    allow_simulated_signal_summaries = False
    allow_simulated_action_summaries = False
    allow_simulated_deployment_targets = False

    def __init__(self, *, edges, metrics, logs=None, failures=()):
        super().__init__()
        self.edges, self.metrics, self.logs = edges, metrics, logs or {}
        self.failures = set(failures)
        self.now = datetime.now(UTC)
        self.calls = []
        self.mutations = []

    async def get_service_topology(self, service):
        self.calls.append(("topology", service))
        edges = [DependencyEdge(source=a, target=b) for a, b in self.edges if service in (a, b)]
        return ServiceTopology(service=service, edges=edges,
            dependencies=list(dict.fromkeys(edge.target if edge.source == service else edge.source for edge in edges)),
            source="service-catalog-via-telemetry-gateway", topology_kind="static_fixture_catalog", catalog_status="available")

    async def query_metrics(self, service, metrics):
        self.calls.append(("metrics", service, tuple(metrics)))
        if service in self.failures:
            raise McpToolError("private upstream diagnostics must not leak")
        return [Evidence(kind="metric", service=service, source="synthetic-scoped-test",
            title=name, observation=f"{name} timestamped observations", observed_at=self.now, score=.9,
            query=name, attributes={"series": [{"timestamp": (self.now - timedelta(seconds=(len(values)-i)*15)).isoformat(), "value": value}
                                               for i, value in enumerate(values)]})
            for name in metrics if (values := self.metrics.get(service, {}).get(name))]

    async def search_logs(self, service, query):
        self.calls.append(("logs", service))
        messages = self.logs.get(service, [])
        return [Evidence(kind="log", service=service, source="synthetic-scoped-test", title="logs",
            observation=" | ".join(messages), observed_at=self.now, score=.9,
            attributes={"events": [{"timestamp": (self.now - timedelta(seconds=15)).isoformat(), "message": text}
                                    for text in messages]})] if messages else []

    async def query_traces(self, service):
        self.calls.append(("traces", service))
        return []

    async def get_recent_deployments(self, service, namespace="prod-sg"):
        self.calls.append(("deployments", service, namespace))
        return []

    async def search_incident_knowledge(self, service, query):
        self.calls.append(("knowledge", service, query))
        return [Evidence(kind="runbook", service=service, source="runbook://synthetic-test",
            title="Investigate retained service signals", observation=f"Investigative context for {query}",
            observed_at=self.now, score=.8)]

    async def restart_deployment(self, **arguments):
        self.mutations.append(arguments)
        raise AssertionError("Cross-service diagnosis must not invoke mutation tools, even dry-run")

    async def rollback_release(self, **arguments):
        return await self.restart_deployment(**arguments)


def workflow(tools, *, saver=None, generator=None):
    return LangGraphIncidentWorkflow(tools=tools, checkpointer=saver or InMemorySaver(),
        approval_signer=ApprovalReceiptSigner("cross-service-only-test-signing-secret"), hypothesis_generator=generator)


def tools_for_cpu(*, edges=None, primary_metrics=None, logs=None):
    return DirectedTools(edges=edges or [("payment-service", "risk-engine")],
        metrics={"payment-service": primary_metrics or {"request_latency_p95": [2500]*4},
                 "risk-engine": {"cpu_utilization": [.96]*4, "request_latency_p95": [2600]*4}}, logs=logs)


def test_generic_latency_investigates_downstream_specific_fault_and_restores_checkpoint():
    async def scenario():
        tools, saver = tools_for_cpu(), InMemorySaver()
        graph = workflow(tools, saver=saver)
        state = await graph.start(IncidentQuestion(question="Why is payment-service suddenly slow?"))
        assert state.complaint_service == state.services[0] == "payment-service"
        assert state.incident_type is None  # complaint-local, not remotely overwritten
        assert state.diagnosis_service == "risk-engine"
        assert state.status == "diagnosed"
        assert state.remediation.tool == "none" and state.remediation.dry_run_passed is False
        assert state.hypotheses[0].cause_service == "risk-engine"
        assert state.hypotheses[0].propagation_path == ["payment-service", "risk-engine"]
        assert all(item.causal_direction_verified is False for item in state.root_cause_candidates)
        assert ("deployments", "risk-engine", "prod-sg") in tools.calls
        assert any(call[0:2] == ("knowledge", "risk-engine") and "cpu saturation" in call[2] for call in tools.calls)
        assert not tools.mutations
        restored = await workflow(tools, saver=saver).get(state.incident_id)
        assert restored.model_dump() == state.model_dump()
        with pytest.raises(GuardrailViolation, match="not waiting"):
            await graph.resume_with_approval(state.incident_id, ApprovalRequest(decision="approve", actor="test-operator",
                reason="Cannot approve a completed advisory", idempotency_key="cross-service-no-approval"))
    asyncio.run(scenario())


def test_downstream_timeout_question_keeps_first_mentioned_complaint():
    async def scenario():
        tools = tools_for_cpu(logs={"payment-service": ["downstream request timed out"]})
        state = await workflow(tools).start(IncidentQuestion(question="Why is payment-service timing out calling risk-engine downstream?"))
        assert state.services[0] == "payment-service"
        assert state.incident_type == "downstream_timeout"
        assert state.diagnosis_service == "risk-engine" and state.remediation.tool == "none"
        assert state.hypotheses[0].cause_service == "risk-engine"
        assert not tools.mutations
    asyncio.run(scenario())


def test_two_hop_discovery_queries_root_without_retargeting_complaint():
    async def scenario():
        tools = DirectedTools(edges=[("checkout-api", "payment-service"), ("payment-service", "risk-engine")],
            metrics={"checkout-api": {"request_latency_p95": [2500]*4},
                     "payment-service": {"request_latency_p95": [2500]*4},
                     "risk-engine": {"cpu_utilization": [.96]*4}})
        state = await workflow(tools).start(IncidentQuestion(question="Why is checkout-api suddenly slow?"))
        assert state.complaint_service == "checkout-api" and state.diagnosis_service == "risk-engine"
        assert state.hypotheses[0].propagation_path == ["checkout-api", "payment-service", "risk-engine"]
        assert [item.service for item in state.service_topologies] == ["checkout-api", "payment-service"]
        assert any(call[0:2] == ("metrics", "risk-engine") and "cpu_utilization" in call[2] for call in tools.calls)
        assert not tools.mutations
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["incoming", "legacy", "failed"])
def test_unproven_path_or_unavailable_local_signals_cannot_become_root(mode):
    async def scenario():
        tools = tools_for_cpu(edges=[("risk-engine", "payment-service")] if mode == "incoming" else None)
        if mode == "legacy":
            tools.get_service_topology = None
            async def dependencies(service):
                return ["risk-engine"]
            tools.get_service_dependencies = dependencies
        if mode == "failed":
            tools.failures.add("risk-engine")
        state = await workflow(tools).start(IncidentQuestion(question="Why is payment-service suddenly slow?"))
        assert not [item for item in state.root_cause_candidates if item.service == "risk-engine"]
        assert state.status == "insufficient_evidence" and state.remediation.tool == "none"
        assert "private upstream diagnostics" not in state.model_dump_json()
        assert not tools.mutations
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["independent_primary", "two_remote_families"])
def test_multiple_specific_candidates_remain_ambiguous_and_advisory(mode):
    async def scenario():
        tools = tools_for_cpu()
        if mode == "independent_primary":
            tools.metrics["payment-service"]["db_pool_active_ratio"] = [1]*4
        else:
            tools.metrics["risk-engine"]["db_pool_active_ratio"] = [1]*4
        state = await workflow(tools).start(IncidentQuestion(question="Why is payment-service suddenly slow?"))
        assert state.diagnosis_service is None
        assert len(state.hypotheses) == 2
        assert state.remediation.tool == "none" and not state.remediation.requires_approval
        assert not tools.mutations
        if mode == "independent_primary":
            assert state.incident_type == "database_connection_pool_exhaustion"
            assert {item.cause_service for item in state.hypotheses} == {"payment-service", "risk-engine"}
        else:
            assert {item.incident_type for item in state.root_cause_candidates} == {"cpu_saturation", "database_connection_pool_exhaustion"}
    asyncio.run(scenario())


def test_model_service_names_are_not_authority_and_forged_execution_is_blocked():
    async def scenario():
        class FakeGenerator:
            async def generate(self, *, question, incident_type, evidence):
                ids = [item.id for item in evidence if item.query in {"cpu_utilization", "request_latency_p95"}]
                ids.append(next(item.id for item in evidence if item.kind == "runbook" and item.service == "risk-engine"))
                return [Hypothesis(rank=1, cause="CPU saturation and request throttling", confidence=.8,
                    evidence_ids=ids, cause_service="invented-service", propagation_path=["payment-service", "invented-service"])], {"provider": "test-double", "model_calls": 0}
        tools = tools_for_cpu()
        state = await workflow(tools, generator=FakeGenerator()).start(IncidentQuestion(question="Why is payment-service suddenly slow?"))
        assert state.hypotheses[0].cause_service == "risk-engine"
        assert state.hypotheses[0].propagation_path == ["payment-service", "risk-engine"]
        assert "invented-service" not in state.model_dump_json()
        assert state.remediation.tool == "none" and not tools.mutations
        forged = state.model_copy(deep=True)
        forged.remediation = Remediation(title="Forged restart", tool="restart_deployment",
            arguments={"service": "payment-service", "namespace": "prod-sg"}, command_preview="restart",
            risk="high", requires_approval=True, dry_run_passed=True, expected_result="Not authorized")
        forged.approval_decision = "approve"
        forged.approval_actor = "test-operator"
        with pytest.raises(GuardrailViolation, match="Cross-service"):
            validate_execution(forged)
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["omitted_anomalous", "omitted_normal", "unclassified", "failed", "empty", "oversized"])
def test_incomplete_downstream_diagnostics_never_authorize_primary_restart(mode):
    async def scenario():
        names = [f"dep-{index}" for index in range(5)] if mode.startswith("omitted") else ["risk-engine"]
        values = [120]*4 if mode == "omitted_normal" else [2500]*4
        metrics = {"payment-service": {"cpu_utilization": [.96]*4, "request_latency_p95": [2500]*4},
                   **{name: {"request_latency_p95": values} for name in names}}
        if mode.startswith("omitted"):
            metrics[names[-1]]["cpu_utilization"] = [.96]*4
        if mode == "empty":
            metrics[names[0]] = {}
        if mode == "oversized":
            metrics[names[0]]["cpu_utilization"] = [.96]*2049
        tools = DirectedTools(edges=[("payment-service", name) for name in names], metrics=metrics,
                              failures=names if mode == "failed" else ())
        graph = workflow(tools)
        state = await graph.start(IncidentQuestion(question="Why is payment-service suddenly slow?"))
        assert state.incident_type == "cpu_saturation"
        assert state.diagnosis_service is None
        assert state.status == "diagnosed" and state.remediation.tool == "none"
        assert not state.remediation.dry_run_passed and not tools.mutations
        coverage = assess_diagnostic_coverage(state, now=datetime.now(UTC))
        assert coverage["complete"] is False and names[-1] in coverage["unresolved_services"]
        assert assess_action_applicability(state, "restart_deployment")["allowed"] is False
        state.remediation = Remediation(title="Forged primary restart", tool="restart_deployment",
            arguments={"service": "payment-service", "namespace": "prod-sg"}, command_preview="restart",
            risk="high", requires_approval=True, dry_run_passed=True, expected_result="Not authorized")
        state.approval_decision, state.approval_actor = "approve", "test-operator"
        with pytest.raises(GuardrailViolation, match="Unresolved downstream"):
            validate_execution(state)
    asyncio.run(scenario())


def test_primary_wrong_unit_is_not_a_classification_or_action_signal():
    async def scenario():
        tools = DirectedTools(edges=[], metrics={"payment-service": {
            "cpu_utilization": [.96]*4, "request_latency_p95": [2500]*4}})
        reader = tools.query_metrics
        async def wrong_unit(service, metrics):
            items = await reader(service, metrics)
            for item in items:
                if item.query == "cpu_utilization":
                    item.attributes["unit"] = "seconds"
            return items
        tools.query_metrics = wrong_unit
        state = await workflow(tools).start(IncidentQuestion(question="Why is payment-service suddenly slow?"))
        assert state.incident_type is None and state.root_cause_candidates == []
        assert state.status == "insufficient_evidence" and state.remediation.tool == "none"
        assert not tools.mutations
    asyncio.run(scenario())


def test_wrong_tool_deployment_cannot_supply_a_rollback_revision():
    async def scenario():
        tools = DirectedTools(edges=[], metrics={"payment-service": {
            "db_pool_active_ratio": [.96]*4, "request_latency_p95": [2500]*4}})
        foreign = Evidence(kind="deployment", service="payment-service", source="synthetic-scope-negative",
            title="Wrong-tool deployment", observation="Must not supply live target metadata", observed_at=tools.now, score=.99,
            attributes={"data_origin": "kubernetes_api", "namespace": "prod-sg", "revision": "2", "previous_revision": "1"})
        reader = tools.query_metrics
        async def wrong_tool(service, metrics):
            return [*await reader(service, metrics), foreign] if service == "payment-service" else await reader(service, metrics)
        tools.query_metrics = wrong_tool
        graph = workflow(tools)
        state = await graph.start(IncidentQuestion(question="Why is payment-service suddenly slow?"))
        assert foreign.id in state.diagnostic_excluded_evidence_ids
        assert state.incident_type == "database_connection_pool_exhaustion"
        assert state.status == "diagnosed" and state.remediation.tool == "none"
        assert graph._previous_revision(state) is None and not tools.mutations
        state.remediation = Remediation(title="Forged rollback", tool="rollback_release",
            arguments={"service": "payment-service", "namespace": "prod-sg", "revision": "1"}, command_preview="rollback",
            risk="high", requires_approval=True, dry_run_passed=True, expected_result="Not authorized")
        with pytest.raises(GuardrailViolation, match="verified deployment evidence"):
            graph._validate_rollback_target(state)
    asyncio.run(scenario())


def test_oversized_evidence_scope_is_unavailable_not_a_healthy_dependency():
    tools = tools_for_cpu()
    async def scenario():
        state = await workflow(tools).start(IncidentQuestion(question="Why is payment-service suddenly slow?"))
        state.evidence.extend(state.evidence[0].model_copy(update={"id": f"padding-{index}"}) for index in range(513))
        coverage = assess_diagnostic_coverage(state, now=datetime.now(UTC))
        assert coverage["complete"] is False
        assert coverage["reasons"][0]["reason"] == "evidence_scope_unavailable"
        assert assess_action_applicability(state, "restart_deployment")["allowed"] is False
    asyncio.run(scenario())


def test_unrequested_resource_metric_cannot_bypass_deep_diagnostic_budget():
    async def scenario():
        names = [f"dep-{index}" for index in range(5)]
        tools = DirectedTools(edges=[("payment-service", name) for name in names],
            metrics={"payment-service": {"request_latency_p95": [2500]*4},
                     **{name: {"request_latency_p95": [2500]*4} for name in names}})
        original = tools.query_metrics
        injected = Evidence(kind="metric", service="dep-4", source="synthetic-test",
            title="Unrequested CPU", observation="Unrequested local resource data", observed_at=tools.now,
            score=.9, query="cpu_utilization", attributes={"series": [
                {"timestamp": (tools.now - timedelta(seconds=offset)).isoformat(), "value": .96}
                for offset in (45, 30, 15)]})
        async def poisoned(service, metrics):
            result = await original(service, metrics)
            if service == "dep-4":
                result.append(injected)
            return result
        tools.query_metrics = poisoned
        state = await workflow(tools).start(IncidentQuestion(question="Why is payment-service suddenly slow?"))
        assert injected in state.evidence and injected.id in state.diagnostic_excluded_evidence_ids
        assert not [item for item in state.root_cause_candidates if item.service == "dep-4"]
        assessment = next(event.payload for event in state.trace if event.event == "cross_service_candidates_assessed")
        assert "dep-4" in assessment["omitted_diagnostic_services"]
        assert len(assessment["investigated_services"]) == 5  # primary plus four
        assert state.status == "insufficient_evidence" and not tools.mutations
    asyncio.run(scenario())


def test_foreign_primary_response_is_audited_but_not_forwarded_to_reasoning():
    async def scenario():
        tools = tools_for_cpu()
        tools.metrics["risk-engine"] = {}
        original = tools.query_metrics
        foreign = Evidence(kind="metric", service="risk-engine", source="synthetic-test", title="Wrong scope",
            observation="Foreign CPU sample", observed_at=tools.now, score=.99, query="cpu_utilization",
            attributes={"series": [{"timestamp": (tools.now - timedelta(seconds=offset)).isoformat(), "value": .96}
                                    for offset in (45, 30, 15)]})
        async def poisoned(service, metrics):
            result = await original(service, metrics)
            return [*result, foreign] if service == "payment-service" else result
        tools.query_metrics = poisoned
        class FakeGenerator:
            async def generate(self, *, question, incident_type, evidence):
                assert foreign.id not in {item.id for item in evidence}
                return [], {"provider": "test-double", "model_calls": 0}
        state = await workflow(tools, generator=FakeGenerator()).start(IncidentQuestion(question="Why is payment-service suddenly slow?"))
        assert foreign in state.evidence and foreign.id in state.diagnostic_excluded_evidence_ids
        assert not state.root_cause_candidates and not tools.mutations
    asyncio.run(scenario())


@pytest.mark.parametrize("tool", ["restart_deployment", "rollback_release"])
@pytest.mark.parametrize("field,value", [("service", "risk-engine"), ("namespace", "other-prod"),
                                         ("service", None), ("namespace", None)])
def test_even_single_service_execution_cannot_silently_change_scope(tool, field, value):
    arguments = {"service": "payment-service", "namespace": "prod-sg"}
    if value is None:
        arguments.pop(field)
    else:
        arguments[field] = value
    state = IncidentState(question="Test scoped execution", environment="prod-sg", services=["payment-service"],
        approval_decision="approve", approval_actor="test-operator", remediation=Remediation(
            title="Scope validation only", tool=tool, arguments=arguments, command_preview="Not run",
            risk="high", requires_approval=True, dry_run_passed=True, expected_result="Not run"))
    with pytest.raises(GuardrailViolation, match="complaint service and incident namespace"):
        validate_execution(state)
