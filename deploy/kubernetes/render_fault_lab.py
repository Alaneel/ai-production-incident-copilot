"""Render the existing backend plus live fault telemetry; never apply or create secrets."""
from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "incident-copilot"
STORES = {
    "prometheus": ("opspilot-prometheus:3.15.0-security-20261014", 9090, "prometheus.yml", "/etc/prometheus/prometheus.yml", ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.retention.time=1h"], "/prometheus"),
    "loki": ("opspilot-loki:3.7.8-security-20261014", 3100, "loki.yml", "/etc/loki/local-config.yaml", ["-config.file=/etc/loki/local-config.yaml"], "/tmp/loki"),
    "tempo": ("opspilot-tempo:2.10.8-security-20261014", 3200, "tempo.yml", "/etc/tempo.yml", ["-config.file=/etc/tempo.yml"], "/var/tempo"),
    "otel-collector": ("opspilot-collector:0.162.0-security-20261014", 4318, "otel-collector.yml", "/etc/otelcol/config.yml", ["--config=/etc/otelcol/config.yml"], None),
}


def environment(container, **values):
    rows = {row["name"]: row for row in container.get("env", [])}
    rows.update({key: {"name": key, "value": value} for key, value in values.items()})
    container["env"] = list(rows.values())


def service(name, port, labels):
    return {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name},
            "spec": {"selector": labels, "ports": [{"name": "http", "port": port, "targetPort": port}]}}


def deployment(name, container, *, volumes=None):
    labels = {"app.kubernetes.io/name": name}
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name},
            "spec": {"replicas": 1, "selector": {"matchLabels": labels}, "template": {
                "metadata": {"labels": labels}, "spec": {"automountServiceAccountToken": False,
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                                        "fsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [container], "volumes": volumes or []}}}}


def render(base, *, tag, connect_risk=False):
    if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}", tag):
        raise ValueError("Explicit valid local image tag required")
    objects = copy.deepcopy(base)
    for obj in objects:
        if obj["kind"] != "Deployment":
            continue
        obj["spec"]["replicas"] = 1
        for container in obj["spec"]["template"]["spec"]["containers"]:
            if container["image"].startswith("incident-copilot/"):
                container["image"] = container["image"].split(":")[0] + ":" + tag
                container["imagePullPolicy"] = "Never"
            if container["name"] == "telemetry-gateway":
                environment(container, PROMETHEUS_URL="http://prometheus:9090", LOKI_URL="http://loki:3100", TEMPO_URL="http://tempo:3200")
            elif container["name"] == "orchestrator":
                environment(container, OPENAI_API_KEY="", OPENAI_MODEL="")
            elif container["name"] == "mcp-server":
                environment(container, MCP_ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000",
                            KUBERNETES_REMEDIATION_ENABLED="true", MCP_ALLOWED_NAMESPACES=NAMESPACE)

    payment = list(yaml.safe_load_all((ROOT / "deploy/kubernetes/experiments/real-pool/payment.yaml").read_text()))
    for obj in payment:
        if obj["kind"] == "Deployment":
            pod = obj["spec"]["template"]["spec"]
            app = pod["containers"][0]
            app["image"] = "incident-copilot/fault-lab:" + tag
            app["imagePullPolicy"] = "Never"
            environment(app, DOWNSTREAM_URL="http://ledger-service:8090", LOKI_PUSH_URL="http://loki:3100/loki/api/v1/push",
                        OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector:4318")
            pod["containers"][1]["imagePullPolicy"] = "Never"
        objects.append(obj)

    for name, downstream in (("checkout-api", "http://payment-service:8090"),
                             ("ledger-service", "http://risk-engine:8090" if connect_risk else ""), ("risk-engine", "")):
        container = {"name": name, "image": "incident-copilot/fault-lab:" + tag, "imagePullPolicy": "Never",
                     "ports": [{"containerPort": 8090}], "env": [{"name": "FAULT_INJECTION_TOKEN", "valueFrom": {
                         "secretKeyRef": {"name": "incident-pool-lab-secrets", "key": "fault-token"}}}],
                     "readinessProbe": {"httpGet": {"path": "/health", "port": 8090}},
                     "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                     "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "1", "memory": "256Mi"}}}
        environment(container, SERVICE_NAME=name, DOWNSTREAM_URL=downstream,
                    LOKI_PUSH_URL="http://loki:3100/loki/api/v1/push", OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector:4318")
        objects.extend([deployment(name, container), service(name, 8090, {"app.kubernetes.io/name": name})])

    traffic = {"name": "traffic-generator", "image": "incident-copilot/fault-lab:" + tag,
               "imagePullPolicy": "Never", "command": ["python", "-m", "incident_lab.traffic"],
               "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "500m", "memory": "256Mi"}},
               "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}
    environment(traffic, TRAFFIC_TARGET="http://checkout-api:8090/v1/work", TRAFFIC_RPS="2")
    objects.append(deployment("traffic-generator", traffic))

    for name, (image, port, file, destination, args, data) in STORES.items():
        config = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name + "-config"},
                  "data": {file: (ROOT / "fault-lab/observability" / file).read_text()}}
        container = {"name": name, "image": image, "imagePullPolicy": "Never", "args": args,
                     "ports": [{"containerPort": port}], "volumeMounts": [{"name": "config", "mountPath": destination, "subPath": file, "readOnly": True}],
                     "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "768Mi"}},
                     "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}
        volumes = [{"name": "config", "configMap": {"name": name + "-config"}}]
        if data:
            container["volumeMounts"].append({"name": "data", "mountPath": data})
            volumes.append({"name": "data", "emptyDir": {"sizeLimit": "512Mi"}})
        endpoint = service(name, port, {"app.kubernetes.io/name": name})
        if name == "tempo":
            endpoint["spec"]["ports"].append({"name": "otlp-http", "port": 4318, "targetPort": 4318})
        objects.extend([config, deployment(name, container, volumes=volumes), endpoint])
    for obj in objects:
        if obj["kind"] != "Namespace":
            obj["metadata"]["namespace"] = NAMESPACE
    if any(obj["kind"] == "Secret" for obj in objects):
        raise ValueError("Secrets must be supplied separately to the isolated cluster")
    return {"apiVersion": "v1", "kind": "List", "items": objects}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-tag", required=True)
    args = parser.parse_args()
    result = subprocess.run(["kubectl", "kustomize", str(ROOT / "deploy/kubernetes/base")],
                            capture_output=True, text=True, check=True, timeout=15)
    print(json.dumps(render(list(yaml.safe_load_all(result.stdout)), tag=args.image_tag)))


if __name__ == "__main__":
    main()
