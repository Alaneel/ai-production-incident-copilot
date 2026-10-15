import copy
import importlib.util
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("fault_renderer", ROOT / "deploy/kubernetes/render_fault_lab.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def objects():
    result = subprocess.run(["kubectl", "kustomize", str(ROOT / "deploy/kubernetes/base")],
                            check=True, text=True, capture_output=True, timeout=15)
    return list(yaml.safe_load_all(result.stdout))


def test_live_stack_retains_base_but_has_real_resource_fault_and_telemetry():
    base = objects()
    before = copy.deepcopy(base)
    result = renderer.render(base, tag="test-live-tag")["items"]
    assert base == before
    assert all(obj["metadata"]["namespace"] == "incident-copilot" for obj in result if obj["kind"] != "Namespace")
    assert not any(obj["kind"] == "Secret" for obj in result)
    deployments = {obj["metadata"]["name"]: obj for obj in result if obj["kind"] == "Deployment"}
    assert {"payment-service", "checkout-api", "ledger-service", "risk-engine", *renderer.STORES} <= deployments.keys()
    traffic = deployments["traffic-generator"]["spec"]["template"]["spec"]["containers"][0]
    assert traffic["command"] == ["python", "-m", "incident_lab.traffic"]
    assert {row["name"]: row["value"] for row in traffic["env"]} == {
        "TRAFFIC_TARGET": "http://checkout-api:8090/v1/work", "TRAFFIC_RPS": "2"}
    payment = deployments["payment-service"]["spec"]["template"]["spec"]
    app, pg = payment["containers"]
    assert app["image"].endswith(":test-live-tag")
    assert app["imagePullPolicy"] == pg["imagePullPolicy"] == "Never"
    assert "listen_addresses=" in pg["args"]
    env = {row["name"]: row for row in app["env"]}
    assert env["INCIDENT_LAB_DB_POOL_BACKEND"]["value"] == "postgres"
    assert env["DOWNSTREAM_URL"]["value"] == "http://ledger-service:8090"
    assert env["LOKI_PUSH_URL"]["value"].startswith("http://loki:3100")
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"]["value"] == "http://otel-collector:4318"
    gateway = deployments["telemetry-gateway"]["spec"]["template"]["spec"]["containers"][0]
    urls = {row["name"]: row.get("value") for row in gateway["env"]}
    assert urls["PROMETHEUS_URL"] == "http://prometheus:9090"
    assert urls["LOKI_URL"] == "http://loki:3100"
    assert urls["TEMPO_URL"] == "http://tempo:3200"
    tempo = next(obj for obj in result if obj["kind"] == "Service" and obj["metadata"]["name"] == "tempo")
    assert {port["port"] for port in tempo["spec"]["ports"]} == {3200, 4318}
    assert not any(port.get("nodePort") for obj in result if obj["kind"] == "Service" for port in obj["spec"]["ports"])


def test_model_credentials_are_empty_and_mcp_origin_matches_client():
    result = renderer.render(objects(), tag="test")["items"]
    for obj in result:
        if obj["kind"] != "Deployment":
            continue
        container = obj["spec"]["template"]["spec"]["containers"][0]
        env = {row["name"]: row for row in container.get("env", [])}
        if container["name"] == "orchestrator":
            assert env["OPENAI_API_KEY"] == {"name": "OPENAI_API_KEY", "value": ""}
            assert env["OPENAI_MODEL"]["value"] == ""
        if container["name"] == "mcp-server":
            assert "http://localhost:3000" in env["MCP_ALLOWED_ORIGINS"]["value"].split(",")
            assert env["KUBERNETES_REMEDIATION_ENABLED"]["value"] == "true"
            assert env["MCP_ALLOWED_NAMESPACES"]["value"] == renderer.NAMESPACE
            assert obj["spec"]["template"]["spec"]["serviceAccountName"] == "mcp-remediator"


def test_renderer_cannot_silently_downgrade_real_preflight_to_simulation():
    base = objects()
    for obj in base:
        if obj["kind"] == "Deployment" and obj["metadata"]["name"] == "mcp-server":
            renderer.environment(obj["spec"]["template"]["spec"]["containers"][0],
                                 KUBERNETES_REMEDIATION_ENABLED="false", MCP_ALLOWED_NAMESPACES="wrong")
    result = renderer.render(base, tag="real-preflight")["items"]
    mcp = next(obj for obj in result if obj["kind"] == "Deployment" and obj["metadata"]["name"] == "mcp-server")
    env = {row["name"]: row for row in mcp["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["KUBERNETES_REMEDIATION_ENABLED"]["value"] == "true"
    assert env["MCP_ALLOWED_NAMESPACES"]["value"] == renderer.NAMESPACE


def test_opt_in_full_chain_sends_checkout_traffic_to_risk_without_mutating_base():
    base = objects()
    before = copy.deepcopy(base)

    def downstreams(rendered):
        return {obj["metadata"]["name"]: {row["name"]: row.get("value") for row in
                obj["spec"]["template"]["spec"]["containers"][0].get("env", [])}.get("DOWNSTREAM_URL")
                for obj in rendered["items"] if obj["kind"] == "Deployment"}

    ordinary = downstreams(renderer.render(base, tag="pool"))
    full = renderer.render(base, tag="cpu", connect_risk=True)
    connected = downstreams(full)
    assert base == before
    assert ordinary["ledger-service"] == ""
    assert connected["checkout-api"] == "http://payment-service:8090"
    assert connected["payment-service"] == "http://ledger-service:8090"
    assert connected["ledger-service"] == "http://risk-engine:8090"
    assert connected["risk-engine"] == ""
    orchestrator = next(obj for obj in full["items"] if obj["kind"] == "Deployment"
                        and obj["metadata"]["name"] == "orchestrator")
    env = {row["name"]: row.get("value") for row in orchestrator["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["OPENAI_API_KEY"] == env["OPENAI_MODEL"] == ""


@pytest.mark.parametrize("tag", ["", "bad tag", "x/y", "x" * 129])
def test_bad_tags_fail_before_output(tag):
    with pytest.raises(ValueError):
        renderer.render([], tag=tag)
