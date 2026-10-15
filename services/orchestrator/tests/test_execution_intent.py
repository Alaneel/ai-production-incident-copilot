import asyncio
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import ApprovalRequest, IncidentQuestion
from incident_copilot.tools import DemoTelemetryTools


def workflow(saver):
    return LangGraphIncidentWorkflow(tools=DemoTelemetryTools(), checkpointer=saver,
                                     approval_signer=ApprovalReceiptSigner("test-intent-only-secret"))


def approve(state):
    return ApprovalRequest(decision="approve", actor="test-operator", reason="Non-mutating fixture only",
                           idempotency_key=f"{state.incident_id}-intent-check")


def test_intent_checkpoint_precedes_dispatch_and_contains_no_signed_receipt():
    async def scenario():
        runtime = workflow(InMemorySaver())
        paused = await runtime.start(IncidentQuestion(question="Why is payment-service slow today?"))
        runtime.tools.rollback_release = AsyncMock(return_value={"accepted": True, "executed": True})
        final = await runtime.resume_with_approval(paused.incident_id, approve(paused))
        prepared = next(item for item in await runtime.history(paused.incident_id) if item.current_step == "prepare_execution")
        event = next(item for item in prepared.trace if item.event == "execution_intent_prepared")
        assert event.payload["arguments"] == paused.remediation.arguments
        assert event.payload["actor"] == "test-operator"
        assert event.payload["dispatch_status"] == "not_confirmed"
        assert event.payload["execution_outcome"] == "unknown"
        assert "approval_receipt" not in prepared.model_dump_json()
        assert not any(item.step == "execute_or_simulate" for item in prepared.trace)
        assert final.status == "remediation_pending"
        assert final.recovery["status"] == "pending"
        assert final.recovery["execution_acknowledged_at"] is None
        runtime.tools.rollback_release.assert_awaited_once()
    asyncio.run(scenario())


def test_intent_checkpoint_write_failure_prevents_dispatch():
    class FailingSaver(InMemorySaver):
        fail_intent = False

        async def aput(self, config, checkpoint, metadata, new_versions):
            state = checkpoint.get("channel_values", {}).get("incident", {})
            if self.fail_intent and state.get("current_step") == "prepare_execution":
                self.failed_steps.append(state["current_step"])
                raise OSError("injected checkpoint write failure")
            return await super().aput(config, checkpoint, metadata, new_versions)
    async def scenario():
        saver = FailingSaver()
        runtime = workflow(saver)
        paused = await runtime.start(IncidentQuestion(question="Why is payment-service slow today?"))
        # Strict msgpack compilation can shallow-clone the supplied saver to
        # derive its allowlist. Inject failure into the graph's actual saver,
        # not an original object that the runtime may no longer use.
        actual_saver = runtime.graph.checkpointer
        assert isinstance(actual_saver, FailingSaver)
        actual_saver.failed_steps = []
        actual_saver.fail_intent = True
        runtime.tools.rollback_release = AsyncMock()
        with pytest.raises(OSError, match="checkpoint write failure"):
            await runtime.resume_with_approval(paused.incident_id, approve(paused))
        runtime.tools.rollback_release.assert_not_awaited()
        assert actual_saver.failed_steps == ["prepare_execution"]
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["missing", "actor", "arguments", "key"])
def test_dispatch_requires_intent_bound_to_actor_arguments_and_key(change):
    async def scenario():
        runtime = workflow(InMemorySaver())
        paused = await runtime.start(IncidentQuestion(question="Why is payment-service slow today?"))
        runtime.tools.rollback_release = AsyncMock(return_value={"accepted": True, "executed": True})
        await runtime.resume_with_approval(paused.incident_id, approve(paused))
        prepared = next(item for item in await runtime.history(paused.incident_id) if item.current_step == "prepare_execution")
        raw = prepared.model_dump(mode="json")
        raw["_approval_idempotency_key"] = approve(paused).idempotency_key
        if change == "missing":
            raw["trace"] = [event for event in raw["trace"] if event["event"] != "execution_intent_prepared"]
        elif change == "actor":
            raw["approval_actor"] = "another-operator"
        elif change == "arguments":
            raw["remediation"]["arguments"]["revision"] = "999"
        else:
            raw["_approval_idempotency_key"] = "different-key"
        runtime.tools.rollback_release.reset_mock()
        with pytest.raises(ValueError, match="matching durable execution intent"):
            await runtime._execute({"incident": raw})
        runtime.tools.rollback_release.assert_not_awaited()
    asyncio.run(scenario())
