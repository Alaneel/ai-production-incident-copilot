"""Run a canonical fault in a newly owned Kind cluster, retain trace, then clean it."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml

from render_fault_lab import NAMESPACE, ROOT, STORES, render

SCENARIOS = json.loads((ROOT / "fault-lab/scenarios.json").read_text())
EXPECTED = {
    "db-pool-exhaustion": ("database_connection_pool_exhaustion", "none"),
    "downstream-timeout": ("downstream_timeout", "none"),
    "cpu-spike": ("cpu_saturation", "restart_deployment"),
    "memory-leak": ("memory_leak", "none"),
    "deployment-regression": ("deployment_regression", "none"),
    "kafka-consumer-lag": ("kafka_consumer_lag", "restart_deployment"),
    "retry-storm": ("retry_storm", "none"),
    "cache-penetration": ("cache_penetration", "none"),
}
FAULT_SCOPES = {
    "db-pool-exhaustion": "actual four-session PostgreSQL application-pool exhaustion",
    "downstream-timeout": "bounded injected downstream HTTP delay and timeout",
    "cpu-spike": "bounded application busy loop and lab CPU gauge, not host CPU measurement",
    "memory-leak": "bounded retained application allocation, not an unbounded production leak",
    "deployment-regression": "injected response errors with synthetic release catalog, not an actual rollout regression",
    "kafka-consumer-lag": "synthetic consumer-lag counter, no Kafka broker or consumer",
    "retry-storm": "bounded repeated downstream HTTP calls, not an unbounded retry loop",
    "cache-penetration": "synthetic cache-miss gauge, no actual cache backend",
}


class OperationFailure(RuntimeError):
    def __init__(self, program, result):
        super().__init__("Isolated test operation failed: " + program)
        self.returncode = result.returncode
        self.stderr = result.stderr


def execute(args, *, data=None, timeout=45, check=True):
    result = subprocess.run(args, cwd=ROOT, input=data, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise OperationFailure(args[0], result)
    return result


def names():
    return execute(["kind", "get", "clusters"]).stdout.splitlines()


def advisory_only(state):
    remediation = state.get("remediation") or {}
    return (state.get("status") == "diagnosed" and remediation.get("tool") == "none"
            and remediation.get("requires_approval") is False
            and remediation.get("dry_run_passed") is False
            and state.get("approval_decision") is None)


def safe_scenario_boundary(state, scenario):
    action = EXPECTED[scenario][1]
    if action == "none":
        return advisory_only(state)
    remediation = state.get("remediation") or {}
    return (state.get("status") == "waiting_approval" and state.get("approval_decision") is None
            and remediation.get("tool") == action and remediation.get("requires_approval") is True
            and remediation.get("dry_run_passed") is True
            and live_diagnostic_context(state, scenario) is not None)


def _nonempty(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and re.fullmatch(r"\S+", value) is not None


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def valid_target_context(context, tool, service, namespace, revision=None):
    fields = {"tool", "service", "namespace", "cluster_context", "deployment_uid", "resource_version",
              "deployment_template_sha256", "revision", "replicaset_uid", "rollback_template_sha256"}
    if (not isinstance(context, dict) or set(context) != fields or context.get("tool") != tool
            or context.get("service") != service or context.get("namespace") != namespace
            or not _hash(context.get("cluster_context")) or not _hash(context.get("deployment_template_sha256"))
            or not _nonempty(context.get("deployment_uid")) or not _nonempty(context.get("resource_version"))
            or any(not isinstance(value, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", value) is None
                   for value in (service, namespace))):
        return False
    if tool == "restart_deployment":
        return all(context.get(field) is None for field in ("revision", "replicaset_uid", "rollback_template_sha256"))
    return (tool == "rollback_release" and isinstance(revision, str)
            and re.fullmatch(r"[1-9][0-9]{0,11}", revision) is not None
            and context.get("revision") == revision and _nonempty(context.get("replicaset_uid"))
            and _hash(context.get("rollback_template_sha256")))


def live_diagnostic_context(state, scenario):
    remediation = state.get("remediation") or {}
    arguments = remediation.get("arguments") or {}
    tool, service = EXPECTED[scenario][1], SCENARIOS[scenario]["service"]
    context = arguments.get("target_context")
    if (remediation.get("preflight_mode") != "kubernetes-server-dry-run"
            or arguments.get("service") != service or arguments.get("namespace") != NAMESPACE
            or not valid_target_context(context, tool, service, NAMESPACE, arguments.get("revision"))):
        return None
    records = [event.get("payload", {}) for event in state.get("trace", [])
               if isinstance(event, dict) and event.get("event") == "tool_result"
               and isinstance(event.get("payload"), dict)
               and event["payload"].get("tool") == tool and event["payload"].get("dry_run") is True]
    if (not records or records[-1].get("accepted") is not True
            or records[-1].get("preflight_mode") != "kubernetes-server-dry-run"
            or records[-1].get("target_context") != context):
        return None
    return context


def deployment_snapshot(deployment):
    metadata = deployment["metadata"]
    template = deployment["spec"]["template"]
    annotations = template.get("metadata", {}).get("annotations", {})
    return {"service": metadata["name"], "namespace": metadata["namespace"],
            "deployment_uid": metadata["uid"], "resource_version": metadata["resourceVersion"],
            "generation": metadata["generation"],
            "deployment_template_sha256": hashlib.sha256(json.dumps(template, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
            "operation_annotations": {key: value for key, value in annotations.items() if key.startswith("incident-copilot.openai/")}}


def valid_snapshot(snapshot, service, namespace):
    fields = {"service", "namespace", "deployment_uid", "resource_version", "generation",
              "deployment_template_sha256", "operation_annotations"}
    if (not isinstance(snapshot, dict) or set(snapshot) != fields
            or snapshot.get("service") != service or snapshot.get("namespace") != namespace
            or not _nonempty(snapshot.get("deployment_uid")) or not _nonempty(snapshot.get("resource_version"))
            or not _hash(snapshot.get("deployment_template_sha256"))
            or not isinstance(snapshot.get("generation"), int) or isinstance(snapshot.get("generation"), bool)
            or snapshot["generation"] < 1 or not isinstance(snapshot.get("operation_annotations"), dict)):
        return False
    return all(isinstance(key, str) and key.startswith("incident-copilot.openai/") and _nonempty(value)
               for key, value in snapshot["operation_annotations"].items())


def unchanged_target(before, after):
    return all(before.get(field) == after.get(field) for field in (
        "service", "namespace", "deployment_uid", "generation", "deployment_template_sha256", "operation_annotations"))


def server_preflight_checks(probe, service, namespace):
    before, after = probe.get("before"), probe.get("after")
    results = [probe.get("result"), probe.get("retry_result")]
    context_ok = all(isinstance(result, dict)
                     and valid_target_context(result.get("target_context"), "restart_deployment", service, namespace)
                     for result in results)
    snapshots_ok = valid_snapshot(before, service, namespace) and valid_snapshot(after, service, namespace)
    accepted = context_ok and _hash(probe.get("expected_cluster_context")) and all(
        result.get("accepted") is True and result.get("executed") is False and result.get("dry_run") is True
        and result.get("mode") == "dry-run" and result.get("backend") == "kubernetes"
        and result.get("preflight_mode") == "kubernetes-server-dry-run"
        and result.get("service") == service and result.get("namespace") == namespace
        and result["target_context"]["cluster_context"] == probe["expected_cluster_context"]
        for result in results)
    matched = context_ok and snapshots_ok and all(
        result["target_context"][field] == snapshot[field]
        for result in results for snapshot in (before, after)
        for field in ("service", "namespace", "deployment_uid", "deployment_template_sha256"))
    negatives = probe.get("negative_results") or {}
    def rejected(name):
        value = negatives.get(name)
        return isinstance(value, dict) and value.get("rejected") is True and value.get("error_type") == "McpToolError"
    return {"server_preflight_validated": bool(accepted),
            "preflight_target_matches_snapshot": bool(matched),
            "preflight_did_not_persist": bool(snapshots_ok and unchanged_target(before, after)),
            "preflight_fresh_retry": bool(accepted and _nonempty(results[0].get("restarted_at"))
                                          and _nonempty(results[1].get("restarted_at"))
                                          and results[0]["restarted_at"] != results[1]["restarted_at"]),
            "preflight_forbidden_namespace_rejected": rejected("forbidden_namespace"),
            "preflight_missing_deployment_rejected": rejected("missing_deployment")}


def diagnostic_preflight_binding(state, scenario, before, after):
    service = SCENARIOS[scenario]["service"]
    if (not valid_snapshot(before, service, NAMESPACE) or not valid_snapshot(after, service, NAMESPACE)
            or not unchanged_target(before, after)):
        return False
    if EXPECTED[scenario][1] == "none":
        return advisory_only(state)
    context = live_diagnostic_context(state, scenario)
    return context is not None and all(context[field] == snapshot[field]
        for snapshot in (before, after) for field in ("service", "namespace", "deployment_uid", "deployment_template_sha256"))


def preflight_program(service, namespace, key, missing_service):
    """Only read-token dry runs, never an execute or approval request."""
    return "\n".join([
        "import asyncio,json,os",
        "from incident_copilot.mcp_client import McpTelemetryTools,McpToolError",
        "async def main():",
        " tools=McpTelemetryTools(url='http://mcp-server:8082/mcp',read_token=os.environ['MCP_READ_TOKEN'],operator_token='',timeout_seconds=8,max_attempts=1)",
        " output={'negative_results':{}}",
        " try:",
        "  args=" + repr({"service": service, "namespace": namespace, "idempotency_key": key, "dry_run": True}),
        "  output['result']=await tools.restart_deployment(**args)",
        "  output['retry_result']=await tools.restart_deployment(**args)",
        "  for name,scope in " + repr([("forbidden_namespace", {"service": service, "namespace": "kube-system"}),
                                     ("missing_deployment", {"service": missing_service, "namespace": namespace})]) + ":",
        "   try:",
        "    await tools.restart_deployment(**{**args,**scope,'idempotency_key':args['idempotency_key']+':'+name})",
        "    output['negative_results'][name]={'rejected':False}",
        "   except McpToolError as error: output['negative_results'][name]={'rejected':True,'error_type':type(error).__name__}",
        " finally: await tools.close()",
        " print(json.dumps(output))",
        "asyncio.run(main())",
    ])


def cluster_context_program():
    return "\n".join([
        "import hashlib,json,pathlib,urllib.parse",
        "from incident_mcp.kubernetes import in_cluster_api_url",
        "url=urllib.parse.urlsplit(in_cluster_api_url())",
        "ca=pathlib.Path('/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')",
        "payload={'api_origin':url.scheme+'://'+url.netloc.lower(),'ca_file_sha256':hashlib.sha256(ca.read_bytes()).hexdigest() if ca.exists() else None}",
        "print(hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest())",
    ])


def no_execution(state):
    return state.get("approval_decision") is None and not any(
        event.get("step") == "execute_or_simulate" or event.get("payload", {}).get("executed") is True
        for event in state.get("trace", []))


def checkpoint_boundary(probe, state, scenario):
    """A visible waiting status alone does not prove a durable graph interrupt."""
    if EXPECTED[scenario][1] == "none":
        return probe.get("next") == [] and probe.get("interrupts") == []
    interrupts = probe.get("interrupts", [])
    if probe.get("next") != ["human_approval"] or len(interrupts) != 1:
        return False
    value = interrupts[0].get("value") or {}
    remediation = state.get("remediation") or {}
    return (bool(interrupts[0].get("id"))
            and value.get("incident_id") == state.get("incident_id")
            and value.get("remediation_id") == remediation.get("id")
            and value.get("tool") == EXPECTED[scenario][1]
            and value.get("arguments") == remediation.get("arguments")
            and value.get("dry_run_passed") is True
            and value.get("preflight_mode") == remediation.get("preflight_mode") == "kubernetes-server-dry-run"
            and live_diagnostic_context(state, scenario) is not None
            and bool(value.get("evidence_ids"))
            and value["evidence_ids"] == (state.get("hypotheses") or [{}])[0].get("evidence_ids"))


def checkpoint_program(incident_id):
    """Load the actual graph with a DB-enforced read-only connection, no tools."""
    return "\n".join([
        "import asyncio,json,os",
        "from psycopg import AsyncConnection",
        "from psycopg.rows import dict_row",
        "from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver",
        "from incident_copilot.approval import ApprovalReceiptSigner",
        "from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow",
        "from incident_copilot.models import IncidentState",
        "class NoTools:",
        " def __getattr__(self,name): raise AssertionError('Checkpoint inspection must not call tools')",
        "async def main():",
        " async with await AsyncConnection.connect(os.environ['DATABASE_URL'],autocommit=True,prepare_threshold=0,row_factory=dict_row,options='-c default_transaction_read_only=on') as connection:",
        "  cursor=await connection.execute('SHOW transaction_read_only')",
        "  mode=(await cursor.fetchone())['transaction_read_only']",
        "  workflow=LangGraphIncidentWorkflow(tools=NoTools(),checkpointer=AsyncPostgresSaver(connection),approval_signer=ApprovalReceiptSigner('read-only-inspection-never-signs'))",
        "  snapshot=await workflow.graph.aget_state({'configurable':{'thread_id':" + repr(incident_id) + "}})",
        "  state=IncidentState.model_validate(snapshot.values['incident']).model_dump(mode='json')",
        "  interrupts=[{'id':item.id,'value':item.value} for task in snapshot.tasks for item in task.interrupts]",
        "  print(json.dumps({'read_only':mode=='on','checkpoint_id':snapshot.config['configurable']['checkpoint_id'],'state':state,'next':list(snapshot.next),'interrupts':interrupts}))",
        "asyncio.run(main())",
    ])


def telemetry_evidence_present(state):
    """An empty typed evidence envelope is not a telemetry observation."""
    evidence = state.get("evidence", [])
    return all(any(item.get("kind") == kind and item.get("attributes", {}).get(field)
                   for item in evidence) for kind, field in (
                       ("metric", "series"), ("log", "events"), ("trace", "traces")))


def correlation_checks(probe, trace_id, request_id):
    calls = [call for call in probe.get("calls", [])
             if call.get("trace_id") == trace_id and call.get("source") == "checkout-api"
             and call.get("target") == "payment-service" and call.get("server_span_id")
             and call.get("client_span_id")]
    events = [event for event in probe.get("events", [])
              if event.get("trace_id") == trace_id and event.get("request_id") == request_id]
    return {
        "controlled_failure_parentage": bool(calls),
        "controlled_failure_server_log_identity": any(
            event.get("message") == "request completed"
            and event.get("span_id") == call["server_span_id"]
            for event in events for call in calls),
        "controlled_failure_pool_log_identity": any(
            event.get("message") == "database connection acquisition timed out"
            and event.get("level") == "ERROR" and event.get("active") == 4
            and event.get("max") == 4 for event in events),
        "controlled_failure_trace_search": any(
            trace.get("trace_id") == trace_id for trace in probe.get("traces", [])),
    }


def correlation_program(trace_id, request_id):
    # Runs the installed typed MCP client, not a direct Loki/Tempo shortcut.
    return "\n".join([
        "import asyncio,json,os,time",
        "from incident_copilot.mcp_client import McpTelemetryTools",
        "async def main():",
        " tools=McpTelemetryTools(url='http://mcp-server:8082/mcp',read_token=os.environ['MCP_READ_TOKEN'],operator_token='',timeout_seconds=3,max_attempts=1)",
        " history=[]; probe={}",
        " try:",
        "  started=time.monotonic(); deadline=started+40; attempt=0",
        "  while time.monotonic()<deadline:",
        "   attempt+=1",
        "   try:",
        "    topology=await tools.get_service_topology('payment-service')",
        "    logs=await tools.search_logs('payment-service'," + repr(request_id) + ")",
        "    traces=await tools.query_traces('payment-service')",
        "    probe={'calls':[c.model_dump(mode='json') for c in topology.observed.calls] if topology.observed else [],'events':[e for item in logs for e in item.attributes.get('events',[])],'traces':[t for item in traces for t in item.attributes.get('traces',[])]}",
        "    calls=[c for c in probe['calls'] if c['trace_id']==" + repr(trace_id) + " and c['source']=='checkout-api' and c['target']=='payment-service']",
        "    history.append({'attempt':attempt,'elapsed_seconds':round(time.monotonic()-started,3),'calls':len(probe['calls']),'events':len(probe['events']),'traces':len(probe['traces']),'target_parentage_found':bool(calls),'target_trace_found':any(t.get('trace_id')==" + repr(trace_id) + " for t in probe['traces'])})",
        "    if calls and any(t.get('trace_id')==" + repr(trace_id) + " for t in probe['traces']) and any(e.get('span_id')==c['server_span_id'] and e.get('trace_id')==" + repr(trace_id) + " and e.get('request_id')==" + repr(request_id) + " for e in probe['events'] for c in calls): break",
        "   except Exception as error: history.append({'attempt':attempt,'elapsed_seconds':round(time.monotonic()-started,3),'error_type':type(error).__name__})",
        "   if time.monotonic()>=deadline: break",
        # A round makes at most four gateway requests (topology includes two).
        # Two-second cadence bounds the probe below the 120/minute query limit.
        "   await asyncio.sleep(min(2,max(0,deadline-time.monotonic())))",
        " finally: await tools.close()",
        " print(json.dumps({**probe,'attempts':history}))",
        "asyncio.run(main())",
    ])


def ready_new_pod(pods, old_uids):
    candidates = [pod for pod in pods["items"] if pod["metadata"].get("uid")
                  and pod["metadata"]["uid"] not in old_uids
                  and not pod["metadata"].get("deletionTimestamp")
                  and pod.get("status", {}).get("phase") == "Running"
                  and any(condition.get("type") == "Ready" and condition.get("status") == "True"
                          for condition in pod.get("status", {}).get("conditions", []))]
    if len(candidates) != 1:
        raise ValueError("Expected exactly one ready replacement API Pod")
    return candidates[0]["metadata"]


def run(images, scenario="db-pool-exhaustion"):
    if scenario not in SCENARIOS or scenario not in EXPECTED:
        raise ValueError("Unknown canonical fault scenario")
    definition = SCENARIOS[scenario]
    primary = definition["service"]
    is_pool = scenario == "db-pool-exhaustion"
    cluster = "copilot-live-" + uuid.uuid4().hex[:20]
    tag = "kind-" + cluster
    if cluster in names():
        raise ValueError("Test cluster already exists")
    resolved = {role: execute(["docker", "image", "inspect", "--format", "{{.Id}}", image]).stdout.strip()
                for role, image in images.items()}
    cached = [item[0] for item in STORES.values()] + json.loads((ROOT / "container-images.lock.json").read_text())["compose_service_images"]["docker-compose.yml"]
    for image in cached:
        execute(["docker", "image", "inspect", image])
    raw = execute(["kubectl", "kustomize", str(ROOT / "deploy/kubernetes/base")]).stdout
    manifest = render(list(yaml.safe_load_all(raw)), tag=tag, connect_risk=not is_pool)
    directory = Path(tempfile.mkdtemp(prefix="copilot-live-kubeconfig-", dir="/private/tmp"))
    config = directory / "config"
    kubectl = ["kubectl", "--kubeconfig", str(config), "--context", "kind-" + cluster]
    scoped = [*kubectl, "--namespace", NAMESPACE]
    tokens = {key: secrets.token_urlsafe(32) for key in (
        "postgres-password", "telemetry-gateway-token", "mcp-read-token", "mcp-operator-token",
        "orchestrator-read-token", "orchestrator-operator-token", "mcp-approval-secret", "fault-token", "pool-password")}
    tokens.update({"postgres-user": "incident", "orchestrator-operator-actor": "kind-live-test-operator"})
    tokens["database-url"] = "postgresql://incident:" + tokens["postgres-password"] + "@postgres:5432/incident_copilot"
    checks = {}
    report = {"contract": "kind-live-fault-acceptance-v2", "scenario": scenario,
              "primary_service": primary, "cluster": cluster, "image_tag": tag,
              "started_at": datetime.now(UTC).isoformat(), "images": resolved, "checks": checks,
              "model_calls": 0, "production_actions": 0, "full_fault_matrix": False,
              "cleanup_complete": False,
              "fault_scope": FAULT_SCOPES[scenario],
              "controlled_log_span_correlation_attempted": is_pool}
    sources = [Path(__file__), ROOT / "deploy/kubernetes/render_fault_lab.py", ROOT / "fault-lab/scenarios.json",
               *sorted((ROOT / "deploy/kubernetes/base").glob("*.yaml")),
               ROOT / "deploy/kubernetes/experiments/real-pool/payment.yaml",
               *sorted((ROOT / "fault-lab/observability").glob("*.yml")),
               *sorted((ROOT / "services/telemetry-gateway").rglob("*.go")),
               ROOT / "services/telemetry-gateway/go.mod", ROOT / "services/telemetry-gateway/Dockerfile", ROOT / 'container-images.lock.json', ROOT / 'requirements-runtime.lock', ROOT / 'requirements-build.lock', ROOT / 'requirements-fault-lab.lock']
    for folder, package in (("services/orchestrator", "incident_copilot"),
                            ("services/mcp-server", "incident_mcp"), ("fault-lab/app", "incident_lab")):
        sources += [*sorted((ROOT / folder / package).glob("*.py")),
                    ROOT / folder / "Dockerfile", ROOT / folder / "pyproject.toml"]
    sources.append(ROOT / "services/mcp-server/incident_mcp/knowledge_corpus.json")
    sources += [p for p in sorted((ROOT / "deploy/images").rglob("*")) if p.is_file() and p.name not in {"README.md", "provenance.json", "dependency-lock-provenance.json"}]
    report["source_sha256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    report["manifest_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    node_image = json.loads((ROOT / "container-images.lock.json").read_text())["kind_node_image"]
    report["kind_node_image"] = node_image
    created = False
    started = time.monotonic()
    phase = "create_cluster"

    def request(service, path, *, method="GET", body=None, authenticated=False, timeout=10, pod=None, extra_headers=None):
        port = 8080 if service == "orchestrator" else 8090
        headers = {}
        if authenticated:
            headers = {"Authorization": "Bearer " + tokens["orchestrator-read-token"]} if service == "orchestrator" else {"x-fault-token": tokens["fault-token"]}
        headers.update(extra_headers or {})
        code = ("import httpx,json; r=httpx.request(" + repr(method) + "," + repr(f"http://127.0.0.1:{port}" + path)
                + ",headers=" + repr(headers) + ",json=" + repr(body) + ",timeout=" + repr(timeout)
                + "); print(json.dumps({'status':r.status_code,'body':r.json()}))")
        args = [*scoped, "exec", "pod/" + pod if pod else "deployment/" + service]
        if service == "payment-service":
            args += ["--container", "payment-service"]
        return json.loads(execute([*args, "--", "python", "-c", code], timeout=timeout + 15).stdout)

    try:
        # A failing create can still allocate the already reserved UUID cluster.
        # Ownership was proved by the preflight; clean partial creation too.
        created = True
        execute(["kind", "create", "cluster", "--name", cluster, "--kubeconfig", str(config), "--image", node_image, "--wait", "120s"], timeout=210)
        phase = "load_images"
        tagged = []
        for role, image_id in resolved.items():
            target = "incident-copilot/" + role + ":" + tag
            execute(["docker", "tag", image_id, target])
            tagged.append(target)
        execute(["kind", "load", "docker-image", *tagged, *cached, "--name", cluster], timeout=240)
        phase = "apply"
        execute([*kubectl, "apply", "-f", "-"], data=json.dumps({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}}))
        for name, values in (("incident-copilot-secrets", {key: value for key, value in tokens.items() if key not in {"fault-token", "pool-password"}}),
                             ("incident-pool-lab-secrets", {"fault-token": tokens["fault-token"], "postgres-password": tokens["pool-password"]})):
            secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name}, "stringData": values}
            execute([*scoped, "apply", "-f", "-"], data=json.dumps(secret))
        execute([*kubectl, "apply", "-f", "-"], data=json.dumps(manifest))
        for obj in manifest["items"]:
            if obj["kind"] not in {"Deployment", "StatefulSet"}:
                continue
            target = obj["kind"].lower() + "/" + obj["metadata"]["name"]
            phase = "rollout:" + target
            execute([*scoped, "rollout", "status", target, "--timeout=120s"], timeout=135)
        phase = "baseline"
        health = request("orchestrator", "/health")["body"]
        report["runtime_modes"] = health
        checks["durable_live_runtime"] = (health["checkpoint"] == "postgres" and health["tools"] == "mcp"
                                          and health["task_state"] == "redis" and health["reasoning"] == "deterministic")
        checks["required_auth"] = health["api_auth"] == "required" and request("orchestrator", "/v1/incidents/missing")["status"] == 401
        baseline = request("payment-service", "/admin/pool", authenticated=True)["body"]
        report["pool_baseline"] = baseline
        checks["real_four_session_pool"] = baseline["backend"] == "postgres" and baseline["maximum"] == 4 and len(set(baseline["backend_pids"])) == 4
        query = "SELECT pid FROM pg_stat_activity WHERE application_name='incident-fault-lab-pool' ORDER BY pid"
        pids = execute([*scoped, "exec", "deployment/payment-service", "--container", "pool-postgres", "--", "psql", "-h", "/var/run/postgresql", "-U", "incident_lab", "-d", "incident_pool_lab", "-At", "-c", query]).stdout
        checks["independent_pg_session_identity"] = sorted(baseline["backend_pids"]) == [int(pid) for pid in pids.split()]
        for workload, package, folder in (("orchestrator", "incident_copilot", "services/orchestrator"),
                                          ("mcp-server", "incident_mcp", "services/mcp-server"),
                                          ("payment-service", "incident_lab", "fault-lab/app")):
            code = "import hashlib,json,pathlib; print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('/app/" + package + "').glob('*.py')}))"
            args = [*scoped, "exec", "deployment/" + workload]
            if workload == "payment-service":
                args += ["--container", "payment-service"]
            installed = json.loads(execute([*args, "--", "python", "-c", code]).stdout)
            expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (ROOT / folder / package).glob("*.py")}
            checks[workload + "_image_sources_match"] = installed == expected
        checks["baseline_checkout_healthy"] = request("checkout-api", "/v1/work")["status"] == 200
        phase = "server_preflight"
        def snapshot():
            return deployment_snapshot(json.loads(execute([*scoped, "get", "deployment/" + primary, "-o", "json"]).stdout))
        before_preflight = snapshot()
        preflight = json.loads(execute([*scoped, "exec", "deployment/orchestrator", "--", "python", "-c",
            preflight_program(primary, NAMESPACE, "preflight-" + uuid.uuid4().hex, "missing-preflight-" + uuid.uuid4().hex[:8])], timeout=65).stdout)
        preflight.update(before=before_preflight, after=snapshot(), expected_cluster_context=execute(
            [*scoped, "exec", "deployment/mcp-server", "--", "python", "-c", cluster_context_program()]).stdout.strip())
        report["server_preflight"] = preflight
        checks.update(server_preflight_checks(preflight, primary, NAMESPACE))
        phase = "inject"
        checks["fault_injected"] = request(primary, "/admin/fault", method="POST", body={
            "mode": definition["mode"], "duration_seconds": 180, "intensity": 0.95}, authenticated=True)["status"] == 200
        if is_pool:
            checks["four_leases_exhausted"] = request("payment-service", "/admin/pool", authenticated=True)["body"]["active"] == 4
            checks["real_timeout"] = request("payment-service", "/v1/work")["status"] == 503
            phase = "correlate_failure"
            # Pause only owned traffic so the fixed probe stays in the bounded sample.
            execute([*scoped, "scale", "deployment/traffic-generator", "--replicas=0"])
            execute([*scoped, "wait", "--for=delete", "pod", "--selector", "app.kubernetes.io/name=traffic-generator", "--timeout=45s"], timeout=55)
            trace_id, request_id = "01" + secrets.token_hex(15), "correlation-" + secrets.token_hex(8)
            failure = request("checkout-api", "/v1/work", extra_headers={
                "traceparent": "00-" + trace_id + "-" + secrets.token_hex(8) + "-01", "x-request-id": request_id})
            report["controlled_failure"] = {"trace_id": trace_id, "request_id": request_id, "response": failure}
            checks["controlled_checkout_dependency_failed"] = failure["body"].get("downstream", {}).get("status") == "failed"
            probe = json.loads(execute([*scoped, "exec", "deployment/orchestrator", "--", "python", "-c",
                                        correlation_program(trace_id, request_id)], timeout=65).stdout)
            report["failure_correlation"] = probe
            checks.update(correlation_checks(probe, trace_id, request_id))
            if not all(checks.values()):
                for workload in ("otel-collector", "tempo", "payment-service"):
                    args = [*scoped, "logs", "deployment/" + workload, "--tail=40"]
                    if workload == "payment-service":
                        args += ["--container", "payment-service"]
                    detail = execute(args, check=False).stdout
                    for value in sorted(tokens.values(), key=len, reverse=True):
                        detail = detail.replace(value, "[redacted]")
                    report.setdefault("telemetry_diagnostics", {})[workload] = detail[-6000:]
            execute([*scoped, "scale", "deployment/traffic-generator", "--replicas=1"])
            execute([*scoped, "rollout", "status", "deployment/traffic-generator", "--timeout=30s"], timeout=40)
        # Several 5-second metric scrapes, with checkout traffic generating logs/spans.
        time.sleep(20 if is_pool else 45)
        phase = "diagnose"
        question = f"Why is {primary} suddenly slow?"
        report["generic_question"] = question
        report["diagnosis_target_before"] = snapshot()
        result = request("orchestrator", "/v1/incidents", method="POST", body={"question": question, "environment": NAMESPACE}, authenticated=True, timeout=45)
        report["diagnosis_http_status"] = result["status"]
        report["diagnosis"] = state = result["body"]
        report["diagnosis_target_after"] = snapshot()
        checks["created_diagnosis"] = result["status"] == 201
        checks["service_identified"] = bool(state.get("services")) and state["services"][0] == primary
        checks["incident_classification"] = state.get("incident_type") == EXPECTED[scenario][0]
        checks["hypotheses_retained"] = bool(state.get("hypotheses"))
        checks["metric_log_trace_evidence"] = telemetry_evidence_present(state)
        checks["safe_remediation_boundary"] = safe_scenario_boundary(state, scenario)
        checks["not_executed"] = no_execution(state)
        checks["diagnostic_preflight_binding"] = diagnostic_preflight_binding(state, scenario,
            report["diagnosis_target_before"], report["diagnosis_target_after"]) and (
                EXPECTED[scenario][1] == "none" or live_diagnostic_context(state, scenario)["cluster_context"] == preflight["expected_cluster_context"])
        incident_id = state["incident_id"]
        checks["readback_exact"] = request("orchestrator", "/v1/incidents/" + incident_id, authenticated=True)["body"] == state
        phase = "read_graph_checkpoint"
        checkpoint_before = json.loads(execute([*scoped, "exec", "deployment/orchestrator", "--", "python", "-c",
                                                checkpoint_program(incident_id)]).stdout)
        report["checkpoint_before_restart"] = checkpoint_before
        checks["checkpoint_read_only"] = checkpoint_before.get("read_only") is True
        checks["checkpoint_state_exact"] = checkpoint_before.get("state") == state
        checks["checkpoint_flow_boundary"] = checkpoint_boundary(checkpoint_before, state, scenario)
        phase = "clear"
        checks["fault_cleared"] = request(primary, "/admin/fault", method="DELETE", authenticated=True)["status"] == 200
        checks["fault_service_recovered"] = request(primary, "/v1/work")["status"] == 200
        checks["checkout_recovered"] = request("checkout-api", "/v1/work")["status"] == 200
        recovered = request("payment-service", "/admin/pool", authenticated=True)["body"]
        checks["same_sessions_after_recovery"] = recovered["backend_pids"] == baseline["backend_pids"]
        old_pods = json.loads(execute([*scoped, "get", "pods", "--selector", "app.kubernetes.io/name=orchestrator", "-o", "json"]).stdout)
        old_uids = {pod["metadata"]["uid"] for pod in old_pods["items"]}
        report["pre_restart_api_pod_uids"] = sorted(old_uids)
        phase = "restart_api_command"
        execute([*scoped, "rollout", "restart", "deployment/orchestrator"])
        phase = "restart_api_rollout"
        execute([*scoped, "rollout", "status", "deployment/orchestrator", "--timeout=90s"], timeout=105)
        phase = "read_new_api_process"
        pods = json.loads(execute([*scoped, "get", "pods", "--selector", "app.kubernetes.io/name=orchestrator", "-o", "json"]).stdout)
        new_pod = ready_new_pod(pods, old_uids)
        report["post_restart_api_pod"] = {key: new_pod[key] for key in ("name", "uid")}
        checks["api_process_replaced"] = new_pod["uid"] not in old_uids
        restored = request("orchestrator", "/v1/incidents/" + incident_id, authenticated=True, pod=new_pod["name"])
        checks["exact_postgres_recovery"] = restored["status"] == 200 and restored["body"] == state
        phase = "read_restored_graph_checkpoint"
        checkpoint_after = json.loads(execute([*scoped, "exec", "pod/" + new_pod["name"], "--", "python", "-c",
                                               checkpoint_program(incident_id)]).stdout)
        report["checkpoint_after_restart"] = checkpoint_after
        checks["exact_graph_checkpoint_recovery"] = checkpoint_after == checkpoint_before
        checks["restored_checkpoint_flow_boundary"] = checkpoint_boundary(checkpoint_after, state, scenario)
        phase = "complete"
    except Exception as error:
        report["error_type"] = type(error).__name__
        def scrub_owned_diagnostics(detail):
            for value in sorted(tokens.values(), key=len, reverse=True):
                detail = detail.replace(value, "[redacted]")
            return detail
        if isinstance(error, OperationFailure):
            report["operation_error"] = {"returncode": error.returncode, "stderr": scrub_owned_diagnostics(error.stderr)[:2000]}
        try:
            pods = json.loads(execute([*scoped, "get", "pods", "-o", "json"], check=False).stdout)
            report["pod_status"] = [{"name": pod["metadata"]["name"], "phase": pod["status"].get("phase"),
                                      "containers": [{"name": c["name"], "ready": c.get("ready"), "restarts": c.get("restartCount"),
                                                      "state": {key: {field: value for field, value in state.items()
                                                                      if field in {"reason", "exitCode", "signal", "startedAt", "finishedAt"}}
                                                                for key, state in c.get("state", {}).items()}}
                                                     for c in pod["status"].get("containerStatuses", [])]}
                                     for pod in pods["items"]]
            logs = []
            for pod in pods["items"][:20]:
                for container in pod["status"].get("containerStatuses", []):
                    if container.get("ready") and not container.get("restartCount"):
                        continue
                    args = [*scoped, "logs", "pod/" + pod["metadata"]["name"], "--container", container["name"], "--tail=40"]
                    if container.get("restartCount"):
                        args.append("--previous=true")
                    result = execute(args, check=False, timeout=10)
                    logs.append({"pod": pod["metadata"]["name"], "container": container["name"],
                                 "returncode": getattr(result, "returncode", 0),
                                 "text": scrub_owned_diagnostics(result.stdout + getattr(result, "stderr", ""))[:4000]})
            report["owned_failure_logs"] = logs
        except Exception:
            report["pod_status_unavailable"] = True
    finally:
        report["last_phase"] = phase
        if created:
            try:
                execute(["kind", "delete", "cluster", "--name", cluster, "--kubeconfig", str(config)], timeout=90)
                report["cleanup_complete"] = cluster not in names()
            except Exception:
                report["cleanup_complete"] = False
        if report["cleanup_complete"]:
            config.unlink(missing_ok=True)
            directory.rmdir()
        else:
            report["retained_private_kubeconfig"] = str(config)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["ok"] = bool(checks) and all(checks.values()) and "error_type" not in report and report["cleanup_complete"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local-integration", action="store_true", required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="db-pool-exhaustion")
    for role in ("orchestrator", "mcp-server", "telemetry-gateway", "fault-lab"):
        parser.add_argument("--" + role + "-image", required=True)
    args = parser.parse_args()
    images = {role: getattr(args, role.replace("-", "_") + "_image") for role in ("orchestrator", "mcp-server", "telemetry-gateway", "fault-lab")}
    report = run(images, args.scenario)
    path = ROOT / "deploy/kubernetes/results" / ("kind_live_" + args.scenario + "_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8] + ".json")
    with path.open("x") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(path), "ok": report["ok"], "phase": report["last_phase"], "checks": report["checks"], "cleanup_complete": report["cleanup_complete"]}))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
