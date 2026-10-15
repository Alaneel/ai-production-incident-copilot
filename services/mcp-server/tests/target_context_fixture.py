"""Explicit bounded target identities for MCP contract tests, not live proof."""
from incident_mcp.target_context import target_context_digest


def target_context(*, tool="rollback_release", service="payment-service", namespace="prod-sg",
                   revision="141", deployment_uid="deployment-test-uid"):
    rollback = tool == "rollback_release"
    return {"tool": tool, "service": service, "namespace": namespace,
            "cluster_context": "a" * 64, "deployment_uid": deployment_uid,
            "resource_version": "17", "deployment_template_sha256": "b" * 64,
            "revision": revision if rollback else None,
            "replicaset_uid": "replicaset-test-uid" if rollback else None,
            "rollback_template_sha256": "c" * 64 if rollback else None}


def preflight_response(context=None):
    return {"accepted": True, "executed": False, "dry_run": True,
            "preflight_mode": "kubernetes-server-dry-run", "backend": "kubernetes",
            "target_context": context if context is not None else target_context()}


def context_digest(context=None):
    return target_context_digest(context if context is not None else target_context())
