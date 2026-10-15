import asyncio
from unittest.mock import AsyncMock

import pytest

from incident_mcp.security import ApprovalReceiptVerifier, AuthorizationError, Principal
from incident_mcp.tools import ToolRegistry
from target_context_fixture import context_digest, preflight_response, target_context


def setup():
    verifier = ApprovalReceiptVerifier("test-cache-only-secret")
    backend = AsyncMock()
    backend.rollback_release.return_value = {"kubernetes_resource_version": "1"}
    backend.restart_deployment.return_value = {"kubernetes_resource_version": "1"}
    backend.preflight.return_value = preflight_response()
    registry = ToolRegistry(verifier, remediator=backend)
    operator = Principal("operator", frozenset({"telemetry:read", "production:execute"}))
    reader = Principal("reader", frozenset({"telemetry:read"}))
    return registry, verifier, backend, operator, reader


def arguments(verifier, *, actor="test-operator", incident="INC-TEST", service="payment-service", revision="141", namespace="prod-sg"):
    context = target_context(service=service, namespace=namespace, revision=revision)
    return {"service": service, "namespace": namespace, "revision": revision, "dry_run": False,
            "idempotency_key": "same-operation-key",
            "target_context": context,
            "approval_receipt": verifier.issue(incident_id=incident, actor=actor, tool="rollback_release",
                                                service=service, namespace=namespace, target=revision,
                                                target_context_sha256=context_digest(context))}


@pytest.mark.parametrize("attack", ["reader", "missing_receipt", "expired_receipt"])
def test_cached_success_cannot_bypass_authorization_or_receipt(attack):
    async def scenario():
        registry, verifier, backend, operator, reader = setup()
        body = arguments(verifier)
        await registry.call("rollback_release", body, operator)
        principal = operator
        if attack == "reader":
            principal = reader
        elif attack == "missing_receipt":
            body["approval_receipt"] = None
        else:
            body["approval_receipt"] = verifier.issue(incident_id="INC-TEST", actor="test-operator",
                                                       tool="rollback_release", service="payment-service", namespace="prod-sg", target="141", ttl_seconds=-1,
                                                       target_context_sha256=context_digest())
        with pytest.raises(AuthorizationError):
            await registry.call("rollback_release", body, principal)
        backend.rollback_release.assert_awaited_once()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["actor", "incident", "service", "revision", "namespace", "mode", "tool"])
def test_cached_key_cannot_be_rebound_to_another_operation(change):
    async def scenario():
        registry, verifier, backend, operator, _ = setup()
        await registry.call("rollback_release", arguments(verifier), operator)
        body = arguments(verifier, **({change: "142" if change == "revision" else "different-value"}
                                     if change in {"actor", "incident", "service", "revision"} else {}))
        tool = "rollback_release"
        if change == "namespace":
            # A newly authorized namespace still cannot reuse another operation's key.
            body = arguments(verifier, namespace="other-namespace")
        elif change == "mode":
            body["dry_run"] = True
        elif change == "tool":
            tool = "restart_deployment"
            del body["revision"]
            body["target_context"] = target_context(tool=tool)
            body["approval_receipt"] = verifier.issue(incident_id="INC-TEST", actor="test-operator", tool=tool,
                                                       service="payment-service", namespace="prod-sg", target="prod-sg",
                                                       target_context_sha256=context_digest(body["target_context"]))
        with pytest.raises(ValueError, match="bound to another"):
            await registry.call(tool, body, operator)
        backend.rollback_release.assert_awaited_once()
        backend.restart_deployment.assert_not_awaited()
    asyncio.run(scenario())


def test_identical_live_replay_rechecks_backend_instead_of_using_process_cache():
    async def scenario():
        registry, verifier, backend, operator, _ = setup()
        first = await registry.call("rollback_release", arguments(verifier), operator)
        # A refreshed receipt may have a new expiration, but must bind the same
        # operation and approval identity, not its raw signed token bytes.
        second = await registry.call("rollback_release", arguments(verifier), operator)
        assert second == first
        assert backend.rollback_release.await_count == 2
        assert backend.rollback_release.await_args.kwargs["target_context"] == arguments(verifier)["target_context"]
        assert "approval_receipt" not in str(registry._execution_bindings)
    asyncio.run(scenario())


def test_identical_simulation_replay_still_uses_its_non_live_cache():
    async def scenario():
        verifier = ApprovalReceiptVerifier("test-cache-only-secret")
        registry = ToolRegistry(verifier)
        body = arguments(verifier)
        body["target_context"] = None
        body["approval_receipt"] = verifier.issue(incident_id="INC-TEST", actor="test-operator", tool="rollback_release",
                                                  service="payment-service", namespace="prod-sg", target="141")
        principal = Principal("operator", frozenset({"production:execute"}))
        first = await registry.call("rollback_release", body, principal)
        second = await registry.call("rollback_release", body, principal)
        assert first is second and first["execution_backend"] == "simulation"
    asyncio.run(scenario())


def test_live_dryrun_replay_always_requests_fresh_preflight():
    async def scenario():
        registry, verifier, backend, operator, _ = setup()
        body = {**arguments(verifier), "dry_run": True, "target_context": None}
        original = preflight_response()
        replacement = preflight_response(target_context(deployment_uid="replacement-deployment"))
        backend.preflight.side_effect = [original, replacement]
        first = await registry.call("rollback_release", body, operator)
        second = await registry.call("rollback_release", body, operator)
        assert first["target_context"] != second["target_context"]
        assert backend.preflight.await_count == 2
        backend.rollback_release.assert_not_awaited()
    asyncio.run(scenario())


def test_dry_run_requires_read_scope_even_on_cached_response():
    async def scenario():
        registry, verifier, backend, operator, _ = setup()
        body = {**arguments(verifier), "dry_run": True}
        await registry.call("rollback_release", body, operator)
        with pytest.raises(AuthorizationError):
            await registry.call("rollback_release", body, Principal("operator", frozenset()))
        backend.rollback_release.assert_not_awaited()
    asyncio.run(scenario())
