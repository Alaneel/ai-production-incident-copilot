"""Human approval authorizes a namespaced operation, never just a release number."""
import asyncio
import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import ApprovalRequest, IncidentQuestion
from incident_copilot.tools import DemoTelemetryTools


def decoded(receipt):
    encoded, signature = receipt[4:].split(".")
    return encoded, signature, json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


@pytest.mark.parametrize("tool,target", [("rollback_release", "141"), ("restart_deployment", "prod-sg")])
def test_signer_binds_namespace_in_signed_v3_payload(tool, target):
    secret = "approval-scope-test-only-secret"
    receipt = ApprovalReceiptSigner(secret).issue(
        incident_id="INC-SCOPE", actor="named-operator", tool=tool,
        service="payment-service", target=target, namespace="prod-sg",
    )
    encoded, signature, payload = decoded(receipt)
    expected = base64.urlsafe_b64encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    assert hmac.compare_digest(signature, expected)
    assert payload["version"] == 3 and isinstance(payload["version"], int) and not isinstance(payload["version"], bool)
    assert "target_context_sha256" in payload and payload["target_context_sha256"] is None
    assert payload["namespace"] == "prod-sg"
    assert payload["tool"] == tool and payload["target"] == target
    assert payload["actor"] == "named-operator" and payload["incident_id"] == "INC-SCOPE"


@pytest.mark.parametrize("namespace", [None, "", " ", "prod-sg ", "PROD-SG", "prod/sg", 123, True, "x" * 64])
def test_signer_rejects_invalid_namespace_instead_of_minting_ambiguous_receipt(namespace):
    with pytest.raises(ValueError, match="namespace"):
        ApprovalReceiptSigner("approval-scope-test-only-secret").issue(
            incident_id="INC-SCOPE", actor="operator", tool="rollback_release",
            service="payment-service", target="141", namespace=namespace,
        )


def test_namespace_argument_is_required_without_a_legacy_fallback():
    with pytest.raises(TypeError):
        ApprovalReceiptSigner("approval-scope-test-only-secret").issue(
            incident_id="INC-SCOPE", actor="operator", tool="rollback_release",
            service="payment-service", target="141",
        )


@pytest.mark.parametrize("digest", ["", "a" * 63, "a" * 65, "A" * 64, 123, True, {}, []])
def test_signer_refuses_invalid_target_context_digest(digest):
    with pytest.raises(ValueError, match="digest"):
        ApprovalReceiptSigner("approval-scope-test-only-secret").issue(
            incident_id="INC-SCOPE", actor="operator", tool="restart_deployment",
            service="payment-service", target="prod-sg", namespace="prod-sg",
            target_context_sha256=digest,
        )


def test_signer_binds_supplied_context_digest_without_an_unsigned_side_channel():
    receipt = ApprovalReceiptSigner("approval-scope-test-only-secret").issue(
        incident_id="INC-SCOPE", actor="operator", tool="restart_deployment",
        service="payment-service", target="prod-sg", namespace="prod-sg",
        target_context_sha256="a" * 64,
    )
    assert decoded(receipt)[2]["target_context_sha256"] == "a" * 64


def test_compiled_workflow_signs_the_exact_approved_remediation_namespace():
    async def run():
        tools = DemoTelemetryTools()
        tools.rollback_release = AsyncMock(wraps=tools.rollback_release)
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
            approval_signer=ApprovalReceiptSigner("approval-scope-test-only-secret"))
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow?", environment="prod-sg"))
        assert state.status == "waiting_approval" and state.remediation.tool == "rollback_release"
        approved = await workflow.resume_with_approval(state.incident_id, ApprovalRequest(
            decision="approve", actor="scope-test-operator", reason="Offline simulation only",
            idempotency_key="approval-scope-simulation-key",
        ))
        execution = next(call.kwargs for call in tools.rollback_release.await_args_list if call.kwargs["dry_run"] is False)
        _, _, payload = decoded(execution["approval_receipt"])
        assert payload["version"] == 3
        assert payload["target_context_sha256"] is None
        assert payload["namespace"] == execution["namespace"] == state.remediation.arguments["namespace"]
        assert payload["service"] == state.remediation.arguments["service"]
        assert payload["target"] == state.remediation.arguments["revision"]
        assert payload["incident_id"] == state.incident_id
        assert payload["actor"] == approved.approval_actor == "scope-test-operator"
    asyncio.run(run())
