from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
OVERLAY = ROOT / "deploy/kubernetes/overlays/read-only-history"


def test_reader_rbac_is_namespace_scoped_without_write_verbs():
    objects = list(yaml.safe_load_all((OVERLAY / "reader-rbac.yaml").read_text()))
    role = next(obj for obj in objects if obj["kind"] == "Role")
    assert all(set(rule["verbs"]) <= {"get", "list"} for rule in role["rules"])
    assert all(set(rule["resources"]) <= {"deployments", "replicasets", "events"} for rule in role["rules"])
    assert not any(obj["kind"] in {"ClusterRole", "ClusterRoleBinding"} for obj in objects)


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl required for rendering")
def test_readonly_overlay_renders_with_discovery_but_no_mutation_backend():
    result = subprocess.run(["kubectl", "kustomize", str(OVERLAY)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    objects = list(yaml.safe_load_all(result.stdout))
    mcp = next(obj for obj in objects if obj["kind"] == "Deployment" and obj["metadata"]["name"] == "mcp-server")
    pod = mcp["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "mcp-deployment-reader"
    env = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
    assert env["KUBERNETES_DEPLOYMENT_DISCOVERY_ENABLED"] == "true"
    assert env["KUBERNETES_DEPLOYMENT_EVENTS_ENABLED"] == "true"
    assert env["KUBERNETES_REMEDIATION_ENABLED"] == "false"
    binding = next(obj for obj in objects if obj["kind"] == "RoleBinding" and obj["metadata"]["name"] == "mcp-deployment-reader")
    assert binding["subjects"][0]["namespace"] == "incident-copilot"
