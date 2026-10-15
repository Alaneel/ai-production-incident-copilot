"""Real in-process API/MCP transport, fake Kubernetes with response loss."""
import asyncio
import json
from collections import defaultdict, deque
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from incident_copilot.api import app
from incident_copilot.mcp_client import McpTelemetryTools, McpToolError
from incident_mcp import server as mcp_server
from incident_mcp.kubernetes import KubernetesRemediator
from incident_mcp.security import ApprovalReceiptVerifier, TokenAuthenticator
from incident_mcp.tools import ToolRegistry


@pytest.mark.parametrize("marker_change,expected", [
    ("unchanged", "marker_matches"), ("overwrite", "not_observed"),
    ("fingerprint", "conflicting_marker"), ("legacy", "unverified_marker"),
])
def test_pending_api_inspects_through_fresh_mcp_registry_without_second_patch(monkeypatch, caplog, marker_change, expected):
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "DATABASE_URL", "REDIS_URL", "MCP_SERVER_URL"):
        monkeypatch.delenv(name, raising=False)
    secret = "transport-test-only-secret"
    read_token, operator_token = "transport-read", "transport-operator"
    api_read, api_operator = "reader-" + "r" * 32, "operator-" + "o" * 32
    monkeypatch.setenv("ORCHESTRATOR_AUTH_MODE", "required")
    monkeypatch.setenv("ORCHESTRATOR_READ_TOKEN", api_read)
    monkeypatch.setenv("ORCHESTRATOR_OPERATOR_TOKEN", api_operator)
    monkeypatch.setenv("ORCHESTRATOR_OPERATOR_ACTOR", "bound-transport-operator")
    monkeypatch.setenv("MCP_APPROVAL_SECRET", secret)
    monkeypatch.setattr(mcp_server, "authenticator", TokenAuthenticator(read_token, operator_token))
    monkeypatch.setattr(mcp_server, "_rate_windows", defaultdict(deque))
    monkeypatch.setattr(mcp_server, "_rate_lock", asyncio.Lock())
    caplog.set_level("INFO", logger="incident-mcp")
    calls, annotations = [], {}
    deployment = {"metadata": {"name": "payment-service", "namespace": "prod-sg",
                              "resourceVersion": "5", "uid": "transport-test-uid"},
                  "spec": {"template": {"metadata": {"annotations": annotations},
                      "spec": {"containers": [{"name": "payment", "image": "test/payment:142"}]}}}}

    def kubernetes(request):
        calls.append((request.method, request.url.path, request.url.params.get("dryRun")))
        if request.method == "PATCH":
            patch = json.loads(request.content)
            candidate = deepcopy(deployment)
            candidate["spec"]["template"]["metadata"]["annotations"].update(patch["spec"]["template"]["metadata"]["annotations"])
            candidate["spec"]["template"]["spec"].update(patch["spec"]["template"].get("spec", {}))
            candidate["metadata"]["resourceVersion"] = "6"
            if request.url.params.get("dryRun") == "All":
                # Admission can return a proposed object, but dryRun must not
                # persist markers or trigger the response-loss failure mode.
                return httpx.Response(200, json=candidate)
            annotations.update(candidate["spec"]["template"]["metadata"]["annotations"])
            deployment["spec"]["template"]["spec"].update(candidate["spec"]["template"]["spec"])
            deployment["metadata"]["resourceVersion"] = "6"
            # The fake API applies the patch, but its response is lost.
            raise httpx.ReadTimeout("private fake Kubernetes timeout", request=request)
        if request.url.path.endswith("/replicasets"):
            return httpx.Response(200, json={"metadata": {}, "items": [{
                "metadata": {"name": "payment-r141", "namespace": "prod-sg", "uid": "transport-rs-141",
                    "ownerReferences": [{"apiVersion": "apps/v1",
                    "kind": "Deployment", "name": "payment-service", "uid": "transport-test-uid", "controller": True}],
                    "annotations": {"deployment.kubernetes.io/revision": "141"}},
                "spec": {"template": {"spec": {"containers": [{"name": "payment", "image": "test/payment:141"}]}}},
            }]})
        return httpx.Response(200, json=deployment)

    kube_client = httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(kubernetes))
    rpc_errors = []
    async def record_error(response):
        await response.aread()
        if "error" in response.json():
            rpc_errors.append(response.json()["error"]["message"])
    mcp_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_server.app),
                                 event_hooks={"response": [record_error]})
    backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={"prod-sg"}, client=kube_client)

    def fresh_registry():
        return ToolRegistry(ApprovalReceiptVerifier(secret), remediator=backend)

    monkeypatch.setattr(mcp_server, "registry", fresh_registry())
    reader_headers = {"Authorization": f"Bearer {api_read}"}
    operator_headers = {"Authorization": f"Bearer {api_operator}"}
    with TestClient(app) as client:
        adapter = McpTelemetryTools(url="http://mcp.test/mcp", read_token=read_token,
                                    operator_token=operator_token, client=mcp_client)
        try:
            tools = app.state.workflow.tools
            tools.rollback_release = adapter.rollback_release
            tools.restart_deployment = adapter.restart_deployment
            tools.query_operation_status = adapter.query_operation_status
            created = client.post("/v1/incidents", headers=reader_headers,
                                  json={"question": "Why is payment-service slow today?"})
            assert created.status_code == 201
            incident = created.json()
            assert incident["status"] == "waiting_approval", rpc_errors
            assert incident["remediation"]["preflight_mode"] == "kubernetes-server-dry-run"
            target = incident["remediation"]["arguments"]
            assert target["target_context"]["deployment_uid"] == "transport-test-uid"
            assert target["target_context"]["replicaset_uid"] == "transport-rs-141"
            assert not annotations and deployment["metadata"]["resourceVersion"] == "5"
            assert sum(method == "PATCH" and dry_run == "All" for method, _, dry_run in calls) == 1
            assert not any(method == "PATCH" and dry_run is None for method, _, dry_run in calls)
            approved = client.post(f"/v1/incidents/{incident['incident_id']}/approval", headers=operator_headers, json={
                "decision": "approve", "actor": "bound-transport-operator", "reason": "Synthetic transport verification",
                "idempotency_key": "transport-original-operation",
            })
            assert approved.status_code == 200
            assert approved.json()["status"] == "remediation_pending"
            assert sum(method == "PATCH" and dry_run is None for method, _, dry_run in calls) == 1, rpc_errors
            assert annotations["incident-copilot.openai/idempotency-key"] == "transport-original-operation"
            if marker_change == "overwrite":
                annotations["incident-copilot.openai/idempotency-key"] = "later-operation"
            elif marker_change == "fingerprint":
                annotations["incident-copilot.openai/operation-fingerprint"] = "another-binding"
            elif marker_change == "legacy":
                del annotations["incident-copilot.openai/operation-fingerprint"]
            # Mimic a restarted MCP server: no prior execution/binding cache.
            fresh = fresh_registry()
            monkeypatch.setattr(mcp_server, "registry", fresh)
            assert fresh._executions == fresh._execution_bindings == {}
            path = f"/v1/incidents/{incident['incident_id']}/operation-status"
            before = len(calls)
            assert client.get(path, headers=reader_headers).status_code == 403
            assert len(calls) == before
            inspected = client.get(path, headers=operator_headers)
            assert inspected.status_code == 200
            assert inspected.json()["observation"]["status"] == expected
            assert inspected.json()["incident_status"] == "remediation_pending"
            assert inspected.json()["observation"]["rollout_health_verified"] is False
            assert calls[before:] == [("GET", "/apis/apps/v1/namespaces/prod-sg/deployments/payment-service", None)]
            assert client.get(f"/v1/incidents/{incident['incident_id']}", headers=reader_headers).json() == approved.json()
            audit = [json.loads(record.message) for record in caplog.records if record.name == "incident-mcp"]
            query = next(event for event in audit if event["tool"] == "query_operation_status")
            assert query["read_only"] is True
            assert query["principal"] == "incident-operator"
            assert query["outcome"] == "success"
            # Also test the real MCP HTTP authorization boundary, not just API auth.
            wrong_scope = McpTelemetryTools(url="http://mcp.test/mcp", read_token=read_token,
                                           operator_token=read_token, client=mcp_client)
            before = len(calls)
            with pytest.raises(McpToolError):
                client.portal.call(lambda: wrong_scope.query_operation_status(
                    service=target["service"], namespace=target["namespace"],
                    tool="rollback_release", revision=target["revision"],
                    approval_actor="bound-transport-operator", incident_id=incident["incident_id"],
                    idempotency_key="transport-original-operation",
                    target_context=target["target_context"],
                ))
            assert len(calls) == before
            assert sum(method == "PATCH" and dry_run is None for method, _, dry_run in calls) == 1
            assert not any("private" in error for error in rpc_errors)
        finally:
            client.portal.call(mcp_client.aclose)
            client.portal.call(kube_client.aclose)
