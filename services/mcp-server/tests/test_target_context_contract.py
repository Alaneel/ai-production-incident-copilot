import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from incident_mcp.security import ApprovalReceiptVerifier, AuthorizationError, Principal
from incident_mcp.tools import ToolRegistry
from target_context_fixture import context_digest, preflight_response, target_context


def setup():
    verifier = ApprovalReceiptVerifier("target-context-contract-secret")
    backend = AsyncMock()
    backend.restart_deployment.return_value = {"kubernetes_resource_version": "18"}
    context = target_context(tool="restart_deployment")
    args = {"service": "payment-service", "namespace": "prod-sg", "dry_run": False,
            "idempotency_key": "approved-target-key", "target_context": context,
            "approval_receipt": verifier.issue(incident_id="INC-TARGET", actor="test-oncall",
                tool="restart_deployment", service="payment-service", namespace="prod-sg", target="prod-sg",
                target_context_sha256=context_digest(context))}
    return ToolRegistry(verifier, remediator=backend), verifier, backend, args


@pytest.mark.parametrize("digest", [False, 1, [], {}, "", "A" * 64, "f" * 63, "g" * 64])
def test_issuer_rejects_noncanonical_target_context_digests(digest):
    with pytest.raises(ValueError, match="target context digest"):
        ApprovalReceiptVerifier("target-context-contract-secret").issue(
            incident_id="INC-TARGET", actor="test-oncall", tool="restart_deployment",
            service="payment-service", namespace="prod-sg", target="prod-sg", target_context_sha256=digest)


@pytest.mark.parametrize("field,value", [
    ("deployment_uid", "replacement-target"), ("cluster_context", "d" * 64),
    ("resource_version", "999"), ("deployment_template_sha256", "e" * 64),
])
def test_signed_target_context_cannot_be_tampered_with_even_on_fresh_key(field, value):
    async def scenario():
        tools, _, backend, args = setup()
        args["target_context"] = {**args["target_context"], field: value}
        args["idempotency_key"] = "fresh-tampering-key"
        with pytest.raises(AuthorizationError, match="target_context_sha256"):
            await tools.call("restart_deployment", args, Principal("operator", frozenset({"production:execute"})))
        backend.restart_deployment.assert_not_awaited()
        assert not tools._execution_bindings
    asyncio.run(scenario())


@pytest.mark.parametrize("context", [None, "missing"])
def test_null_or_missing_target_context_cannot_execute_a_live_backend(context):
    async def scenario():
        tools, verifier, backend, args = setup()
        if context == "missing":
            args.pop("target_context")
        else:
            args["target_context"] = None
        args["approval_receipt"] = verifier.issue(incident_id="INC-TARGET", actor="test-oncall",
            tool="restart_deployment", service="payment-service", namespace="prod-sg", target="prod-sg")
        with pytest.raises(AuthorizationError, match="verified Kubernetes target context"):
            await tools.call("restart_deployment", args, Principal("operator", frozenset({"production:execute"})))
        backend.restart_deployment.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("tool", "rollback_release"), ("service", "ledger-service"), ("namespace", "other-ns")])
def test_context_must_match_outer_tool_arguments(field, value):
    async def scenario():
        tools, verifier, backend, args = setup()
        if field == "tool":
            args["target_context"] = target_context(tool=value)
        else:
            args["target_context"][field] = value
        args["approval_receipt"] = verifier.issue(incident_id="INC-TARGET", actor="test-oncall",
            tool="restart_deployment", service="payment-service", namespace="prod-sg", target="prod-sg",
            target_context_sha256=context_digest(args["target_context"]))
        with pytest.raises(AuthorizationError, match=f"requested {field}"):
            await tools.call("restart_deployment", args, Principal("operator", frozenset({"production:execute"})))
        backend.restart_deployment.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"preflight_mode": "simulation"}, {"backend": "simulation"}, {"target_context": None},
    {"executed": True}, {"accepted": False}, {"dry_run": False},
    {"target_context": {"deployment_uid": "ambiguous"}},
])
def test_ambiguous_or_simulated_backend_preflight_never_looks_like_cluster_verification(change):
    async def scenario():
        tools, _, backend, args = setup()
        args.update(dry_run=True, target_context=None)
        backend.preflight.return_value = {**preflight_response(target_context(tool="restart_deployment")), **change}
        with pytest.raises((AuthorizationError, ValueError)):
            await tools.call("restart_deployment", args, Principal("reader", frozenset({"telemetry:read"})))
        backend.restart_deployment.assert_not_awaited()
        assert tools._executions == {}
    asyncio.run(scenario())


def test_backend_preflight_failure_is_not_replaced_with_simulation_acceptance():
    async def scenario():
        tools, _, backend, args = setup()
        args.update(dry_run=True, target_context=None)
        backend.preflight.side_effect = ValueError("Cluster preflight unavailable")
        with pytest.raises(ValueError, match="Cluster preflight unavailable"):
            await tools.call("restart_deployment", args, Principal("reader", frozenset({"telemetry:read"})))
        assert not tools._executions
    asyncio.run(scenario())


def test_simulation_is_explicit_and_cannot_adopt_a_live_context():
    async def scenario():
        _, verifier, _, args = setup()
        tools = ToolRegistry(verifier)
        args.update(dry_run=True, target_context=None)
        result = await tools.call("restart_deployment", args, Principal("reader", frozenset({"telemetry:read"})))
        assert result["preflight_mode"] == "simulation" and result["target_context"] is None
        assert result["backend"] == "simulation" and result["executed"] is False
        args["target_context"] = target_context(tool="restart_deployment")
        args["idempotency_key"] = "fresh-fake-context-key"
        with pytest.raises(AuthorizationError, match="simulation backend"):
            await tools.call("restart_deployment", args, Principal("reader", frozenset({"telemetry:read"})))
    asyncio.run(scenario())


def test_live_replay_cannot_hide_recreated_target_behind_process_cache():
    async def scenario():
        tools, _, backend, args = setup()
        operator = Principal("operator", frozenset({"production:execute"}))
        await tools.call("restart_deployment", args, operator)
        backend.restart_deployment.side_effect = ValueError("Approved deployment UID changed")
        with pytest.raises(ValueError, match="UID changed"):
            await tools.call("restart_deployment", deepcopy(args), operator)
        assert backend.restart_deployment.await_count == 2
    asyncio.run(scenario())


def test_context_is_in_tool_schema_and_operation_fingerprint_without_receipt_bytes():
    async def scenario():
        tools, _, backend, args = setup()
        await tools.call("restart_deployment", args, Principal("operator", frozenset({"production:execute"})))
        binding = tools._execution_bindings[args["idempotency_key"]]
        assert binding["arguments"]["target_context"] == args["target_context"]
        assert "approval_receipt" not in str(binding)
        assert backend.restart_deployment.await_args.kwargs["target_context"] == args["target_context"]
        for tool in ("restart_deployment", "rollback_release", "query_operation_status"):
            definition = next(item for item in tools.list_definitions() if item["name"] == tool)
            assert "target_context" in definition["inputSchema"]["properties"]
    asyncio.run(scenario())
