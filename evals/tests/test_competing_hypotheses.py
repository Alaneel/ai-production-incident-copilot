import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_workflow_benchmark import SnapshotTools  # noqa: E402
from incident_mcp.knowledge import KnowledgeIndex  # noqa: E402
from incident_copilot.approval import ApprovalReceiptSigner  # noqa: E402
from incident_copilot.incident_profiles import infer_profiles_from_evidence  # noqa: E402
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow  # noqa: E402
from incident_copilot.models import Hypothesis, IncidentQuestion  # noqa: E402


def tools():
    return SnapshotTools(service="payment-service", index=KnowledgeIndex.load_default(), logs=["request completed"],
                         metrics={"cpu_utilization": [0.95]*5, "db_pool_active_ratio": [1.0]*5,
                                  "request_latency_p95": [2400]*5})


def test_multiple_supported_patterns_are_not_collapsed_into_one_cause():
    async def scenario():
        provider = tools()
        evidence = await provider.query_metrics("payment-service", tuple(provider.metrics))
        assert {profile.key for profile in infer_profiles_from_evidence(evidence)} == {"cpu_saturation", "database_connection_pool_exhaustion"}
    asyncio.run(scenario())


def test_competing_hypotheses_are_checkpointed_and_never_propose_mutation():
    async def scenario():
        provider = tools()
        provider.restart_deployment = AsyncMock()
        provider.rollback_release = AsyncMock()
        workflow = LangGraphIncidentWorkflow(tools=provider, checkpointer=InMemorySaver(), approval_signer=ApprovalReceiptSigner("benchmark-only-approval-secret"))
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
        assert state.status == "diagnosed"
        assert state.incident_type is None
        assert len(state.candidate_incident_types) == len(state.hypotheses) == 2
        assert state.remediation.tool == "none"
        by_id = {item.id: item for item in state.evidence}
        metrics = {next(by_id[id].query for id in hypothesis.evidence_ids if by_id[id].kind == "metric") for hypothesis in state.hypotheses}
        assert metrics == {"cpu_utilization", "db_pool_active_ratio"}
        retained = await workflow.get(state.incident_id)
        assert retained.candidate_incident_types == state.candidate_incident_types
        assert any(event.event == "competing_hypotheses" for event in retained.trace)
        validation = next(event for event in retained.trace if event.event == "hypothesis_citations_validated")
        assert len(validation.payload["hypotheses"]) == 2
        assert validation.payload["semantic_entailment_verified"] is False
        provider.restart_deployment.assert_not_awaited()
        provider.rollback_release.assert_not_awaited()
    asyncio.run(scenario())


def test_invalid_model_alternative_stops_graph_before_remediation():
    class Generator:
        async def generate(self, **arguments):
            metric = next(item for item in arguments["evidence"] if item.kind == "metric")
            log = next(item for item in arguments["evidence"] if item.kind == "log")
            return [
                Hypothesis(rank=1, cause="observed contention", confidence=0.8, evidence_ids=[metric.id, log.id]),
                Hypothesis(rank=2, cause="unsupported alternative", confidence=0.3, evidence_ids=[metric.id, "missing"]),
            ], {"model": "fixture"}
    async def scenario():
        provider = tools()
        provider.restart_deployment = AsyncMock()
        provider.rollback_release = AsyncMock()
        workflow = LangGraphIncidentWorkflow(tools=provider, checkpointer=InMemorySaver(),
                                             approval_signer=ApprovalReceiptSigner("benchmark-only-approval-secret"), hypothesis_generator=Generator())
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
        assert state.status == "insufficient_evidence"
        assert state.hypotheses == []
        assert state.remediation.tool == "none"
        retained = await workflow.get(state.incident_id)
        assert retained.status == "insufficient_evidence"
        rejection = next(event for event in retained.trace if event.event == "hypothesis_citations_rejected")
        assert "rank 2" in rejection.payload["reason"]
        assert rejection.payload["hypotheses"][1]["evidence_ids"][-1] == "missing"
        assert not any(event.step == "propose_remediation" for event in retained.trace)
        provider.restart_deployment.assert_not_awaited()
        provider.rollback_release.assert_not_awaited()
    asyncio.run(scenario())


def test_configured_model_may_explain_ambiguity_but_cannot_clear_action_gate():
    class Generator:
        async def generate(self, **arguments):
            assert arguments["incident_type"] is None
            metrics = [item for item in arguments["evidence"] if item.kind == "metric"]
            log = next(item for item in arguments["evidence"] if item.kind == "log")
            return [Hypothesis(rank=1, cause="Possible resource contention", confidence=0.8,
                               evidence_ids=[metrics[0].id, log.id])], {"model": "fixture"}
    async def scenario():
        provider = tools()
        provider.rollback_release = AsyncMock()
        provider.restart_deployment = AsyncMock()
        workflow = LangGraphIncidentWorkflow(tools=provider, checkpointer=InMemorySaver(),
                                             approval_signer=ApprovalReceiptSigner("benchmark-only-approval-secret"), hypothesis_generator=Generator())
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
        assert state.hypotheses[0].cause == "Possible resource contention"
        assert len(state.candidate_incident_types) == 2
        assert state.remediation.tool == "none"
        provider.restart_deployment.assert_not_awaited()
        provider.rollback_release.assert_not_awaited()
    asyncio.run(scenario())
