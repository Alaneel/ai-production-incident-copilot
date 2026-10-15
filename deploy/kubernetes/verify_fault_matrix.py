"""Validate explicitly supplied current-revision fault reports; never run integration.

This is retained-evidence consistency checking, not independent runtime attestation.
The source-path helper mirrors verify_live_fault_lab.py and must be kept in sync;
an exact fingerprint key-set comparison fails closed when its contract changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

from verify_live_fault_lab import (
    EXPECTED,
    FAULT_SCOPES,
    ROOT,
    SCENARIOS,
    checkpoint_boundary,
    correlation_checks,
    no_execution,
    safe_scenario_boundary,
)

CONTRACT = "kind-live-fault-acceptance-v2"
IMAGE_ROLES = {"orchestrator", "mcp-server", "telemetry-gateway", "fault-lab"}
COMMON_CHECKS = {
    "durable_live_runtime", "required_auth", "real_four_session_pool",
    "independent_pg_session_identity", "orchestrator_image_sources_match",
    "mcp-server_image_sources_match", "payment-service_image_sources_match",
    "baseline_checkout_healthy", "fault_injected", "created_diagnosis",
    "service_identified", "incident_classification", "hypotheses_retained",
    "metric_log_trace_evidence", "safe_remediation_boundary", "not_executed",
    "readback_exact", "checkpoint_read_only", "checkpoint_state_exact",
    "checkpoint_flow_boundary", "fault_cleared", "fault_service_recovered",
    "checkout_recovered", "same_sessions_after_recovery", "api_process_replaced",
    "exact_postgres_recovery", "exact_graph_checkpoint_recovery",
    "restored_checkpoint_flow_boundary",
    "server_preflight_validated", "preflight_target_matches_snapshot",
    "preflight_did_not_persist", "preflight_fresh_retry",
    "preflight_forbidden_namespace_rejected", "preflight_missing_deployment_rejected",
    "diagnostic_preflight_binding",
}
POOL_CHECKS = {
    "four_leases_exhausted", "real_timeout", "controlled_checkout_dependency_failed",
    "controlled_failure_parentage", "controlled_failure_server_log_identity",
    "controlled_failure_pool_log_identity", "controlled_failure_trace_search",
}
CORPUS = "services/mcp-server/incident_mcp/knowledge_corpus.json"
LIMITATIONS = [
    "Offline consistency check of retained reports, not independent attestation of runtime or report honesty.",
    "Deterministic diagnoses only; no paid model evaluation or semantic root-cause/faithfulness score.",
    "Faults retain their per-case bounded scope; Kafka/cache/deployment faults are not real infrastructure failures.",
    "No approved remediation is executed; recovery clears injected faults and does not prove action efficacy.",
    "Reported auth, rollout, recovery and cleanup assertions cannot be independently re-observed offline.",
    "Manifest hashes are retained individually, not re-rendered or required equal across unique image tags.",
    "Source/image bindings are consistency evidence, not signed build provenance or supply-chain attestation.",
    "Server dry-run admission and unchanged snapshots do not prove approved action efficacy or zero backend PATCH requests without API audit.",
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def current_source_sha256(root=ROOT):
    """Mirror the runner's exact source inputs, including discovery key sets."""
    paths = [root / "deploy/kubernetes/verify_live_fault_lab.py",
             root / "deploy/kubernetes/render_fault_lab.py", root / "fault-lab/scenarios.json",
             *sorted((root / "deploy/kubernetes/base").glob("*.yaml")),
             root / "deploy/kubernetes/experiments/real-pool/payment.yaml",
             *sorted((root / "fault-lab/observability").glob("*.yml")),
             *sorted((root / "services/telemetry-gateway").rglob("*.go")),
             root / "services/telemetry-gateway/go.mod", root / "services/telemetry-gateway/Dockerfile", root / 'container-images.lock.json', root / 'requirements-runtime.lock', root / 'requirements-build.lock', root / 'requirements-fault-lab.lock']
    for folder, package in (("services/orchestrator", "incident_copilot"),
                            ("services/mcp-server", "incident_mcp"), ("fault-lab/app", "incident_lab")):
        paths += [*sorted((root / folder / package).glob("*.py")),
                  root / folder / "Dockerfile", root / folder / "pyproject.toml"]
    paths.append(root / CORPUS)
    paths += [p for p in sorted((root / "deploy/images").rglob("*")) if p.is_file() and p.name not in {"README.md", "provenance.json", "dependency-lock-provenance.json"}]
    return {str(path.relative_to(root)): digest(path.read_bytes()) for path in paths}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def hash_value(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def meaningful_telemetry(kind, item):
    if not isinstance(item, dict):
        return False
    if kind == "metric":
        value = item.get("value")
        return nonempty_string(item.get("timestamp")) and finite_number(value)
    if kind == "log":
        return nonempty_string(item.get("message")) and nonempty_string(item.get("timestamp"))
    return nonempty_string(item.get("trace_id"))


def exact_json(left, right):
    # Python equality would otherwise treat False == 0 and True == 1.
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def identity_string(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and re.fullmatch(r"\S+", value) is not None


def valid_snapshot(snapshot, service, namespace):
    """A typed narrow resource observation, not an arbitrary green-check payload."""
    fields = {"service", "namespace", "deployment_uid", "resource_version", "generation",
              "deployment_template_sha256", "operation_annotations"}
    if (not isinstance(snapshot, dict) or set(snapshot) != fields
            or snapshot["service"] != service or snapshot["namespace"] != namespace
            or not identity_string(snapshot["deployment_uid"])
            or not identity_string(snapshot["resource_version"])
            or not integer(snapshot["generation"]) or snapshot["generation"] < 1
            or not hash_value(snapshot["deployment_template_sha256"])):
        return False
    annotations = snapshot["operation_annotations"]
    return isinstance(annotations, dict) and all(
        isinstance(key, str) and key.startswith("incident-copilot.openai/")
        and identity_string(value) for key, value in annotations.items())


def unchanged_target(before, after):
    # resourceVersion also changes for controller/status observations. It is not
    # an execution counter and need not remain equal across the two snapshots.
    return all(exact_json(before[field], after[field]) for field in (
        "service", "namespace", "deployment_uid", "generation",
        "deployment_template_sha256", "operation_annotations"))


def valid_restart_context(context, service, namespace):
    """Independently enforce the normalized live restart context contract."""
    fields = {"tool", "service", "namespace", "cluster_context", "deployment_uid",
              "resource_version", "deployment_template_sha256", "revision",
              "replicaset_uid", "rollback_template_sha256"}
    return (isinstance(context, dict) and set(context) == fields
            and context["tool"] == "restart_deployment"
            and context["service"] == service and context["namespace"] == namespace
            and hash_value(context["cluster_context"])
            and identity_string(context["deployment_uid"])
            and identity_string(context["resource_version"])
            and hash_value(context["deployment_template_sha256"])
            and all(context[field] is None for field in (
                "revision", "replicaset_uid", "rollback_template_sha256")))


def context_matches_snapshot(context, snapshot):
    return all(context[field] == snapshot[field] for field in (
        "service", "namespace", "deployment_uid", "deployment_template_sha256"))


def preflight_result(result, service, namespace):
    return (isinstance(result, dict) and result.get("accepted") is True
            and result.get("executed") is False and result.get("dry_run") is True
            and result.get("mode") == "dry-run" and result.get("backend") == "kubernetes"
            and result.get("preflight_mode") == "kubernetes-server-dry-run"
            and result.get("service") == service and result.get("namespace") == namespace
            and result.get("action") == "restart"
            and identity_string(result.get("kubernetes_resource_version"))
            and valid_restart_context(result.get("target_context"), service, namespace))


def valid_preflight_time(value):
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def validate_server_preflight(report, scenario):
    primary, namespace = SCENARIOS[scenario]["service"], "incident-copilot"
    probe = report.get("server_preflight")
    require(isinstance(probe, dict), "Missing server preflight observation")
    before, after = probe.get("before"), probe.get("after")
    require(valid_snapshot(before, primary, namespace) and valid_snapshot(after, primary, namespace),
            "Malformed or wrong-scope server preflight snapshot")
    require(unchanged_target(before, after), "Server preflight persisted a target change")
    results = (probe.get("result"), probe.get("retry_result"))
    require(all(preflight_result(result, primary, namespace) for result in results),
            "Unverified server preflight mode, backend, response or target context")
    expected_cluster = probe.get("expected_cluster_context")
    require(hash_value(expected_cluster) and all(result["target_context"]["cluster_context"] == expected_cluster
                                                for result in results),
            "Server preflight cluster context does not match independent configuration observation")
    require(all(context_matches_snapshot(result["target_context"], snapshot)
                for result in results for snapshot in (before, after)),
            "Server preflight target differs from resource snapshots")
    require(all(valid_preflight_time(result.get("restarted_at")) for result in results)
            and results[0]["restarted_at"] != results[1]["restarted_at"],
            "Server preflight retry is not a fresh observation")
    negative = probe.get("negative_results")
    require(isinstance(negative, dict) and set(negative) == {"forbidden_namespace", "missing_deployment"},
            "Missing bounded negative server preflight probes")
    require(all(isinstance(negative[field], dict) and set(negative[field]) == {"rejected", "error_type"}
                and negative[field].get("rejected") is True
                and negative[field].get("error_type") == "McpToolError"
                for field in ("forbidden_namespace", "missing_deployment")),
            "Negative server preflight probe was not rejected")
    return expected_cluster


def validate_diagnostic_preflight(report, state, scenario, expected_cluster):
    primary, namespace = SCENARIOS[scenario]["service"], "incident-copilot"
    before, after = report.get("diagnosis_target_before"), report.get("diagnosis_target_after")
    require(valid_snapshot(before, primary, namespace) and valid_snapshot(after, primary, namespace),
            "Malformed or wrong-scope diagnostic preflight snapshot")
    require(unchanged_target(before, after), "Diagnostic preflight persisted a target change")
    if EXPECTED[scenario][1] == "none":
        return
    remediation = state["remediation"]
    context = remediation["arguments"].get("target_context")
    require(remediation.get("preflight_mode") == "kubernetes-server-dry-run"
            and valid_restart_context(context, primary, namespace),
            "Unverified diagnostic preflight mode or target context")
    require(context["cluster_context"] == expected_cluster
            and all(context_matches_snapshot(context, snapshot) for snapshot in (before, after)),
            "Diagnostic preflight target differs from observed configuration or snapshots")
    records = [event["payload"] for event in state["trace"]
               if event.get("event") == "tool_result" and event["payload"].get("tool") == remediation["tool"]
               and event["payload"].get("dry_run") is True]
    require(records and records[-1].get("accepted") is True
            and records[-1].get("preflight_mode") == "kubernetes-server-dry-run"
            and records[-1].get("error_type") is None
            and exact_json(records[-1].get("target_context"), context),
            "Diagnostic preflight context is not bound to its successful trace record")


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON object key: " + key)
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError("Non-finite JSON number: " + value)


def strict_float(value):
    result = float(value)
    require(math.isfinite(result), "Non-finite JSON number: " + value)
    return result


def validate_diagnosis(report, scenario):
    state = report.get("diagnosis")
    primary = SCENARIOS[scenario]["service"]
    require(isinstance(state, dict), "Missing diagnosis object")
    require(nonempty_string(state.get("incident_id")), "Missing incident identity")
    require(nonempty_string(state.get("trace_id")), "Missing workflow trace identity")
    services = state.get("services")
    require(isinstance(services, list) and services and all(map(nonempty_string, services))
            and services[0] == primary, "Primary service identification mismatch")
    require(state.get("incident_type") == EXPECTED[scenario][0], "Incident classification mismatch")
    question = f"Why is {primary} suddenly slow?"
    require(report.get("generic_question") == state.get("question") == question,
            "Generic question mismatch")
    require(state.get("environment") == "incident-copilot", "Unexpected incident environment")
    require(integer(report.get("diagnosis_http_status")) and report["diagnosis_http_status"] == 201,
            "Diagnosis was not created with HTTP 201")
    require("approval_decision" in state and state["approval_decision"] is None,
            "Missing or non-null approval decision")
    remediation = state.get("remediation")
    require(isinstance(remediation, dict) and nonempty_string(remediation.get("id"))
            and isinstance(remediation.get("arguments"), dict), "Malformed remediation")
    require(safe_scenario_boundary(state, scenario), "Unsafe remediation boundary")
    require(remediation["arguments"].get("service") == primary, "Remediation service mismatch")
    if EXPECTED[scenario][1] != "none":
        require(remediation["arguments"].get("namespace") == "incident-copilot",
                "Remediation namespace mismatch")
    trace = state.get("trace")
    require(isinstance(trace, list) and trace and all(
        isinstance(event, dict) and nonempty_string(event.get("step"))
        and isinstance(event.get("payload"), dict) for event in trace), "Missing or malformed workflow trace")
    require(all("executed" not in event["payload"] or event["payload"]["executed"] is False
                for event in trace) and no_execution(state), "Execution is present in retained trace")
    evidence = state.get("evidence")
    require(isinstance(evidence, list) and evidence and all(
        isinstance(item, dict) and nonempty_string(item.get("id"))
        and isinstance(item.get("attributes"), dict) for item in evidence), "Malformed evidence list")
    ids = [item["id"] for item in evidence]
    require(len(ids) == len(set(ids)), "Duplicate evidence IDs")
    for kind, field in (("metric", "series"), ("log", "events"), ("trace", "traces")):
        payloads = [item["attributes"].get(field) for item in evidence
                    if item.get("kind") == kind and item.get("service") == primary]
        require(any(isinstance(payload, list) and payload and all(
            meaningful_telemetry(kind, item) for item in payload) for payload in payloads),
            "No nonempty " + kind + " payload for primary service")
    hypotheses = state.get("hypotheses")
    require(isinstance(hypotheses, list) and hypotheses and all(
        isinstance(item, dict) and nonempty_string(item.get("cause"))
        and isinstance(item.get("evidence_ids"), list) and item["evidence_ids"]
        and all(nonempty_string(citation) and citation in ids for citation in item["evidence_ids"])
        for item in hypotheses), "Missing or unbound hypothesis evidence")
    return state


def validate_checkpoint(report, state, scenario):
    before, after = report.get("checkpoint_before_restart"), report.get("checkpoint_after_restart")
    for checkpoint in (before, after):
        require(isinstance(checkpoint, dict) and checkpoint.get("read_only") is True
                and nonempty_string(checkpoint.get("checkpoint_id")), "Missing read-only graph checkpoint")
        require(isinstance(checkpoint.get("next"), list) and isinstance(checkpoint.get("interrupts"), list)
                and all(isinstance(item, dict) and nonempty_string(item.get("id"))
                        and isinstance(item.get("value"), dict) for item in checkpoint["interrupts"]),
                "Malformed graph boundary")
        require(exact_json(checkpoint.get("state"), state), "Checkpoint state differs from diagnosis")
        require(checkpoint_boundary(checkpoint, state, scenario), "Checkpoint flow boundary mismatch")
    require(exact_json(before, after), "Exact graph checkpoint recovery drift")
    old_uids, new_pod = report.get("pre_restart_api_pod_uids"), report.get("post_restart_api_pod")
    require(isinstance(old_uids, list) and old_uids and all(map(nonempty_string, old_uids))
            and len(old_uids) == len(set(old_uids)), "Missing original API Pod UIDs")
    require(isinstance(new_pod, dict) and nonempty_string(new_pod.get("name"))
            and nonempty_string(new_pod.get("uid")) and new_pod["uid"] not in old_uids,
            "Missing distinct replacement API Pod UID")


def validate_pool_correlation(report):
    failure, probe = report.get("controlled_failure"), report.get("failure_correlation")
    require(isinstance(failure, dict) and isinstance(probe, dict), "Missing controlled pool failure probe")
    trace_id, request_id = failure.get("trace_id"), failure.get("request_id")
    require(isinstance(trace_id, str) and re.fullmatch(r"01[0-9a-f]{30}", trace_id)
            and nonempty_string(request_id), "Malformed controlled failure identity")
    response = failure.get("response")
    require(isinstance(response, dict) and integer(response.get("status")) and response["status"] == 200
            and isinstance(response.get("body"), dict)
            and isinstance(response["body"].get("downstream"), dict)
            and response["body"]["downstream"].get("status") == "failed", "Controlled checkout did not retain dependency failure")
    require(all(isinstance(probe.get(field), list) and probe[field] and all(
        isinstance(item, dict) for item in probe[field]) for field in ("calls", "events", "traces")),
        "Malformed or empty controlled correlation telemetry")
    calls = [call for call in probe["calls"] if call.get("trace_id") == trace_id
             and call.get("source") == "checkout-api" and call.get("target") == "payment-service"
             and all(isinstance(call.get(field), str) and re.fullmatch(r"[0-9a-f]{16}", call[field])
                     and call[field] != "0" * 16 for field in ("client_span_id", "server_span_id"))
             and call["client_span_id"] != call["server_span_id"]]
    require(calls,
            "Malformed controlled parentage span identities")
    events = [event for event in probe["events"]
              if event.get("message") != "database connection acquisition timed out"
              or (integer(event.get("active")) and integer(event.get("max")))]
    require(all(correlation_checks({**probe, "calls": calls, "events": events}, trace_id, request_id).values()),
            "Controlled pool request correlation mismatch")


def validate_report(report, sources):
    require(isinstance(report, dict), "Report must be a JSON object")
    require(report.get("contract") == CONTRACT, "Unsupported report contract")
    scenario = report.get("scenario")
    require(isinstance(scenario, str) and scenario in SCENARIOS and scenario in EXPECTED,
            "Unknown canonical scenario")
    require(report.get("primary_service") == SCENARIOS[scenario]["service"], "Primary service mismatch")
    require(report.get("fault_scope") == FAULT_SCOPES[scenario], "Fault scope mismatch")
    require(report.get("ok") is True and report.get("cleanup_complete") is True
            and report.get("full_fault_matrix") is False and report.get("last_phase") == "complete"
            and "error_type" not in report and "operation_error" not in report
            and "retained_private_kubeconfig" not in report, "Incomplete or unsuccessful live report")
    for counter in ("model_calls", "production_actions"):
        require(integer(report.get(counter)) and report[counter] == 0,
                "Missing, non-integer or nonzero " + counter)
    is_pool = scenario == "db-pool-exhaustion"
    require(report.get("controlled_log_span_correlation_attempted") is is_pool,
            "Controlled-correlation scope mismatch")
    checks = report.get("checks")
    expected_checks = COMMON_CHECKS | (POOL_CHECKS if is_pool else set())
    require(isinstance(checks, dict) and set(checks) == expected_checks
            and all(value is True for value in checks.values()), "Required checks missing, non-boolean, false or unknown")
    require(report.get("source_sha256") == sources, "Incomplete or stale current-revision source fingerprints")
    images = report.get("images")
    require(isinstance(images, dict) and set(images) == IMAGE_ROLES and all(
        isinstance(value, str) and value.startswith("sha256:") and hash_value(value[7:])
        for value in images.values()), "Incomplete or malformed image IDs")
    require(hash_value(report.get("manifest_sha256")), "Missing manifest fingerprint")
    runtime = report.get("runtime_modes")
    require(isinstance(runtime, dict) and all(runtime.get(key) == value for key, value in {
        "status": "ok", "api_auth": "required", "checkpoint": "postgres",
        "task_state": "redis", "tools": "mcp", "reasoning": "deterministic"}.items()),
        "Runtime modes do not match deterministic durable authenticated MCP contract")
    cluster = report.get("cluster")
    require(isinstance(cluster, str) and re.fullmatch(r"copilot-live-[0-9a-f]{20}", cluster)
            and report.get("image_tag") == "kind-" + cluster, "Missing isolated cluster/image-tag binding")
    started = datetime.fromisoformat(report.get("started_at", ""))
    require(started.tzinfo is not None, "Missing timezone in start time")
    elapsed = report.get("elapsed_seconds")
    require(finite_number(elapsed) and elapsed > 0,
            "Missing positive elapsed time")
    baseline = report.get("pool_baseline")
    require(isinstance(baseline, dict) and baseline.get("backend") == "postgres"
            and integer(baseline.get("maximum")) and baseline["maximum"] == 4
            and isinstance(baseline.get("backend_pids"), list) and len(baseline["backend_pids"]) == 4
            and all(integer(pid) and pid > 0 for pid in baseline["backend_pids"])
            and len(set(baseline["backend_pids"])) == 4, "Missing real four-session pool baseline")
    expected_cluster = validate_server_preflight(report, scenario)
    state = validate_diagnosis(report, scenario)
    validate_diagnostic_preflight(report, state, scenario, expected_cluster)
    validate_checkpoint(report, state, scenario)
    if is_pool:
        validate_pool_correlation(report)
    return scenario


def aggregate(report_paths):
    """Read only explicit paths, retaining immutable input-byte hashes and bindings."""
    result = {"contract": "kind-fault-matrix-consistency-v2", "accepted": False,
              "full_fault_matrix": False, "created_at": datetime.now(UTC).isoformat(),
              "aggregator_sha256": digest(Path(__file__).read_bytes()), "cases": [], "errors": [],
              "limitations": LIMITATIONS,
              "offline_operations": {"model_calls": 0, "production_actions": 0}}
    try:
        sources = current_source_sha256()
        result["source_sha256"] = sources
        result["knowledge_corpus_sha256"] = sources[CORPUS]
    except (OSError, KeyError) as error:
        result["errors"].append("Cannot fingerprint current source inputs: " + type(error).__name__)
        return result
    scenarios, images = set(), None
    for report_path in report_paths:
        reference = {"path": str(Path(report_path).resolve()), "accepted": False}
        result["cases"].append(reference)
        try:
            raw = Path(report_path).read_bytes()
            reference["report_sha256"] = digest(raw)
            require(len(raw) <= 32 * 1024 * 1024, "Report exceeds 32 MiB bound")
            report = json.loads(raw, object_pairs_hook=strict_object,
                                parse_constant=invalid_constant, parse_float=strict_float)
            scenario = validate_report(report, sources)
            require(scenario not in scenarios, "Duplicate canonical scenario: " + scenario)
            require(images is None or images == report["images"], "Image IDs differ across matrix cases")
            scenarios.add(scenario)
            images = report["images"]
            reference.update(accepted=True, scenario=scenario, primary_service=report["primary_service"],
                             incident_type=report["diagnosis"]["incident_type"], fault_scope=report["fault_scope"],
                             manifest_sha256=report["manifest_sha256"], cluster=report["cluster"],
                             image_tag=report["image_tag"], started_at=report["started_at"],
                             elapsed_seconds=report["elapsed_seconds"], checks_count=len(report["checks"]))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            reference["error"] = str(error) if not isinstance(error, OSError) else type(error).__name__
            result["errors"].append({"path": reference["path"], "error": reference["error"]})
    try:
        require(current_source_sha256() == sources, "Current sources changed during aggregation")
    except (OSError, ValueError) as error:
        result["errors"].append("Current-source recheck failed: " + str(error))
    result["images"] = images
    result["coverage"] = {"accepted_scenarios": sorted(scenarios),
                          "missing_scenarios": sorted(set(SCENARIOS) - scenarios),
                          "accepted_count": len(scenarios), "canonical_count": len(SCENARIOS)}
    result["accepted"] = bool(report_paths) and not result["errors"]
    result["full_fault_matrix"] = result["accepted"] and scenarios == set(SCENARIOS)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path, help="Explicit immutable report paths; no discovery")
    parser.add_argument("--output", type=Path, help="Optional new output file, opened exclusively without overwriting")
    args = parser.parse_args(argv)
    result = aggregate(args.reports)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x") as stream:
            stream.write(text)
        print(json.dumps({"report": str(args.output.resolve()), "accepted": result["accepted"],
                          "full_fault_matrix": result["full_fault_matrix"]}))
    else:
        print(text, end="")
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
