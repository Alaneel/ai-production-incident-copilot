import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = ROOT / "deploy/kubernetes/experiments/real-pool"


@pytest.fixture(scope="module")
def compose():
    # Render only; no daemon mutations or credentials. Never print config output.
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI required for merge rendering; static contracts still run")
    environment = {**os.environ, "FAULT_INJECTION_TOKEN": "packaging-test-token",
                   "INCIDENT_LAB_POSTGRES_PASSWORD": "packaging-test-password", "OPENAI_API_KEY": "", "OPENAI_MODEL": ""}
    result = subprocess.run(["docker", "compose", "--env-file", "/dev/null",
        "-f", "docker-compose.yml", "-f", "compose.fault-lab.yml", "-f", "compose.real-pool.yml",
        "config", "--format", "json"], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, "Compose rendering failed (config/credentials intentionally omitted)"
    return json.loads(result.stdout)


def test_compose_real_pool_has_dedicated_socket_only_database(compose):
    database = compose["services"]["lab-pool-postgres"]
    payment = compose["services"]["payment-service"]
    assert "listen_addresses=" in database["command"]
    assert not database.get("ports")
    assert database["environment"]["POSTGRES_DB"] == "incident_pool_lab"
    assert payment["environment"]["INCIDENT_LAB_DB_POOL_BACKEND"] == "postgres"
    assert "host=/tmp/incident-lab-postgres" in payment["environment"]["INCIDENT_LAB_POSTGRES_URL"]
    assert payment["command"] == ["python", "-m", "incident_lab.start"]
    assert payment["user"] == "10001:70"
    sockets = [mount for mount in payment["volumes"] if mount["target"] == "/tmp/incident-lab-postgres"]
    assert len(sockets) == 1 and sockets[0]["source"] == "lab-pool-socket" and sockets[0]["read_only"] is True
    assert payment["depends_on"]["lab-pool-postgres"]["condition"] == "service_healthy"
    assert "ledger-service" in payment["depends_on"]  # Overlay preserves original dependencies.
    assert payment["environment"]["LOKI_PUSH_URL"].startswith("http://loki:")
    assert payment["environment"]["DOWNSTREAM_URL"].startswith("http://ledger-service:")
    for service in ("checkout-api", "ledger-service", "risk-engine"):
        assert "INCIDENT_LAB_POSTGRES_URL" not in compose["services"][service]["environment"]
    assert compose["services"]["postgres"]["environment"]["POSTGRES_DB"] == "incident_copilot"


def assert_kubernetes_contract(objects):
    assert {obj["metadata"].get("namespace", "incident-pool-lab") for obj in objects} == {"incident-pool-lab"}
    assert not any(obj["kind"] in {"Secret", "StatefulSet", "PersistentVolumeClaim"} for obj in objects)
    services = [obj for obj in objects if obj["kind"] == "Service"]
    assert len(services) == 1 and services[0]["spec"]["ports"][0]["port"] == 8090
    deployment = next(obj for obj in objects if obj["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert deployment["spec"]["replicas"] == 1 and deployment["spec"]["strategy"]["type"] == "Recreate"
    assert pod["automountServiceAccountToken"] is False and pod["securityContext"]["fsGroup"] == 70
    app, database = pod["containers"]
    assert app["command"] == ["python", "-m", "incident_lab.start"]
    assert app["securityContext"]["runAsUser"] == 10001 and app["securityContext"]["readOnlyRootFilesystem"]
    assert "listen_addresses=" in database["args"] and not database.get("ports")
    assert database["securityContext"]["runAsUser"] == 70
    env = {item["name"]: item for item in app["env"]}
    assert env["FAULT_INJECTION_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "incident-pool-lab-secrets"
    assert env["INCIDENT_LAB_DB_POOL_BACKEND"]["value"] == "postgres"
    assert env["LOKI_PUSH_URL"]["value"] == env["OTEL_EXPORTER_OTLP_ENDPOINT"]["value"] == ""
    # This is an isolated packaging experiment, not the full telemetry stack.
    assert all("emptyDir" in volume for volume in pod["volumes"])


def test_static_kubernetes_experiment_contract_without_external_tools():
    objects = list(yaml.safe_load_all((EXPERIMENT / "namespace.yaml").read_text()))
    objects += list(yaml.safe_load_all((EXPERIMENT / "payment.yaml").read_text()))
    assert_kubernetes_contract(objects)


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl required for actual Kustomize rendering")
def test_kubernetes_experiment_renders_without_secrets_or_database_service():
    result = subprocess.run(["kubectl", "kustomize", str(EXPERIMENT)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, "Experiment render failed"
    assert_kubernetes_contract(list(yaml.safe_load_all(result.stdout)))


def test_static_compose_overlay_contract_without_external_tools():
    overlay = yaml.safe_load((ROOT / "compose.real-pool.yml").read_text())
    services = overlay["services"]
    assert set(services) == {"payment-service", "lab-pool-postgres"}
    assert "listen_addresses=" in services["lab-pool-postgres"]["command"]
    assert not services["lab-pool-postgres"].get("ports")
    assert services["payment-service"]["environment"]["INCIDENT_LAB_DB_POOL_BACKEND"] == "postgres"
    assert ":?" in services["lab-pool-postgres"]["environment"]["POSTGRES_PASSWORD"]
    assert ":?" in services["payment-service"]["environment"]["FAULT_INJECTION_TOKEN"]
