"""Signed receipt scope must be checked before idempotent replay or dispatch."""
import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import time
from unittest.mock import AsyncMock

import pytest

from incident_mcp.security import ApprovalReceiptVerifier, AuthorizationError, Principal
from incident_mcp.tools import ToolRegistry
from target_context_fixture import context_digest, target_context


SECRET = "receipt-contract-test-only-secret"
SCOPE = {"tool": "rollback_release", "service": "payment-service", "namespace": "prod-sg", "target": "141"}


def payload():
    return {"version": 3, "incident_id": "INC-CONTRACT", "actor": "test-oncall",
            **SCOPE, "target_context_sha256": context_digest(), "expires_at": int(time.time()) + 300}


def signed(value):
    # Hand-sign malformed payloads to exercise validation after authentication.
    encoded = base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), encoded.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"apr_{encoded}.{signature}"


def execution_arguments(receipt, *, namespace="prod-sg", key="receipt-operation-key"):
    return {"service": "payment-service", "namespace": namespace, "revision": "141",
            "dry_run": False, "approval_receipt": receipt, "idempotency_key": key,
            "target_context": target_context(namespace=namespace)}


def registry():
    backend = AsyncMock()
    backend.rollback_release.return_value = {"kubernetes_resource_version": "17"}
    return ToolRegistry(ApprovalReceiptVerifier(SECRET), remediator=backend), backend


def test_issue_and_verify_require_explicit_namespace_and_preserve_approval_identity():
    verifier = ApprovalReceiptVerifier(SECRET)
    for method in (verifier.issue, verifier.verify):
        assert inspect.signature(method).parameters["namespace"].default is inspect.Parameter.empty
    receipt = verifier.issue(incident_id="INC-CONTRACT", actor="test-oncall", **SCOPE)
    result = verifier.verify(receipt, **SCOPE)
    assert result["version"] == 3
    assert "target_context_sha256" in result and result["target_context_sha256"] is None
    assert result["namespace"] == "prod-sg"
    assert result["incident_id"] == "INC-CONTRACT" and result["actor"] == "test-oncall"


def test_live_receipt_requires_the_exact_canonical_target_digest():
    verifier = ApprovalReceiptVerifier(SECRET)
    digest = context_digest()
    receipt = verifier.issue(incident_id="INC-CONTRACT", actor="test-oncall", **SCOPE,
                             target_context_sha256=digest)
    result = verifier.verify(receipt, **SCOPE, target_context_sha256=digest)
    assert result["target_context_sha256"] == digest and result["version"] == 3
    for requested in (None, "d" * 64):
        with pytest.raises(AuthorizationError, match="not valid for requested target_context_sha256"):
            verifier.verify(receipt, **SCOPE, target_context_sha256=requested)


@pytest.mark.parametrize("digest", [False, 1, [], {}, "", "A" * 64, "g" * 64, "f" * 63])
def test_signed_noncanonical_context_digest_fails_validation_not_only_equality(digest):
    with pytest.raises(AuthorizationError, match="target context digest is malformed"):
        ApprovalReceiptVerifier(SECRET).verify(signed({**payload(), "target_context_sha256": digest}),
                                               **SCOPE, target_context_sha256=context_digest())


@pytest.mark.parametrize("namespace", [None, 1, True, [], {}, "", "Prod-SG", "../prod-sg", "x" * 64])
def test_issuer_rejects_invalid_namespace_without_minting_an_unverifiable_receipt(namespace):
    with pytest.raises(ValueError, match="Approval namespace is invalid"):
        ApprovalReceiptVerifier(SECRET).issue(incident_id="INC-CONTRACT", actor="test-oncall",
                                             **{**SCOPE, "namespace": namespace})


@pytest.mark.parametrize("value", [None, [], "not-an-object", 1, False])
def test_signed_nonobject_payload_is_authorization_error(value):
    receipt = signed(value)
    with pytest.raises(AuthorizationError) as error:
        ApprovalReceiptVerifier(SECRET).verify(receipt, **SCOPE)
    assert receipt not in str(error.value)


@pytest.mark.parametrize("field,value", [
    ("version", None), ("version", 1), ("version", 2), ("version", 4), ("version", "3"),
    ("version", 3.0), ("version", True), ("version", []), ("version", {}),
    ("namespace", None), ("namespace", 1), ("namespace", True), ("namespace", []),
    ("namespace", {}), ("namespace", ""), ("namespace", " "), ("namespace", "Prod-SG"),
    ("namespace", "x" * 64), ("namespace", "../prod-sg"),
    ("expires_at", None), ("expires_at", True), ("expires_at", False),
    ("expires_at", "9999999999"), ("expires_at", 9999999999.0),
    ("expires_at", []), ("expires_at", {}),
    ("actor", None), ("actor", []), ("actor", ""),
    ("incident_id", None), ("incident_id", {}), ("incident_id", " "),
])
def test_signed_malformed_version_namespace_expiry_and_identity_fail_closed(field, value):
    body = {**payload(), field: value}
    receipt = signed(body)
    with pytest.raises(AuthorizationError) as error:
        ApprovalReceiptVerifier(SECRET).verify(receipt, **SCOPE)
    assert receipt not in str(error.value)


@pytest.mark.parametrize("field", ["version", "namespace", "expires_at", "incident_id", "actor", "tool", "service", "target", "target_context_sha256"])
def test_every_required_signed_field_must_be_present(field):
    body = payload()
    del body[field]
    with pytest.raises(AuthorizationError):
        ApprovalReceiptVerifier(SECRET).verify(signed(body), **SCOPE)


@pytest.mark.parametrize("receipt", [None, 123, True, [], {}, "apr_missing-separator", "apr_data.非ascii"])
def test_malformed_receipt_container_and_encoding_do_not_escape_as_type_errors(receipt):
    with pytest.raises(AuthorizationError):
        ApprovalReceiptVerifier(SECRET).verify(receipt, **SCOPE)


def test_same_rollback_receipt_cannot_cross_namespace_with_fresh_idempotency_key():
    async def scenario():
        tools, backend = registry()
        operator = Principal("operator", frozenset({"production:execute"}))
        receipt = signed(payload())
        approved = execution_arguments(receipt)
        await tools.call("rollback_release", approved, operator)
        with pytest.raises(AuthorizationError, match="not valid for requested namespace"):
            await tools.call("rollback_release", execution_arguments(
                receipt, namespace="other-ns", key="fresh-cross-namespace-key"), operator)
        backend.rollback_release.assert_awaited_once()
        assert backend.rollback_release.await_args.kwargs["namespace"] == "prod-sg"
        assert "fresh-cross-namespace-key" not in tools._executions
    asyncio.run(scenario())


@pytest.mark.parametrize("legacy", ["versionless", "namespace_missing", "both_missing", "version_one", "version_two"])
def test_legacy_signed_receipt_cannot_replay_a_cached_execution(legacy):
    async def scenario():
        tools, backend = registry()
        operator = Principal("operator", frozenset({"production:execute"}))
        approved = execution_arguments(signed(payload()))
        await tools.call("rollback_release", approved, operator)
        body = payload()
        if legacy in {"versionless", "both_missing"}:
            del body["version"]
        if legacy in {"namespace_missing", "both_missing"}:
            del body["namespace"]
        if legacy == "version_one":
            body["version"] = 1
        if legacy == "version_two":
            body["version"] = 2
        with pytest.raises(AuthorizationError):
            await tools.call("rollback_release", {**approved, "approval_receipt": signed(body)}, operator)
        backend.rollback_release.assert_awaited_once()
        assert len(tools._executions) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("version", True), ("namespace", []), ("expires_at", True)])
def test_malformed_signed_contract_is_checked_before_backend_or_cached_success(field, value):
    async def scenario():
        tools, backend = registry()
        operator = Principal("operator", frozenset({"production:execute"}))
        invalid = execution_arguments(signed({**payload(), field: value}))
        with pytest.raises(AuthorizationError):
            await tools.call("rollback_release", invalid, operator)
        backend.rollback_release.assert_not_awaited()
        assert tools._executions == {}
        await tools.call("rollback_release", execution_arguments(signed(payload())), operator)
        with pytest.raises(AuthorizationError):
            await tools.call("rollback_release", invalid, operator)
        backend.rollback_release.assert_awaited_once()
    asyncio.run(scenario())
