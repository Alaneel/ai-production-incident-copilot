"""Compiled LangGraph candidate localization over strict multi-service snapshots.

No model, network, database, human approval or destructive preflight is used.
Fixture labels belong only to the scorer. Catalog/call direction is observation,
not verified failure propagation; backend origin labels here are synthetic.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import statistics
import sys
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/orchestrator"))
sys.path.insert(0, str(ROOT / "services/mcp-server"))

# Offline benchmark entry points must not inherit provider SDK exporters.
for setting in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_OTEL_ENABLED"):
    os.environ[setting] = "false"
from langsmith import tracing_context  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from incident_copilot.approval import ApprovalReceiptSigner  # noqa: E402
from incident_copilot.dependency_topology import legacy_topology, parse_topology  # noqa: E402
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow  # noqa: E402
from incident_copilot.mcp_client import McpToolError  # noqa: E402
from incident_copilot.models import Evidence, IncidentQuestion  # noqa: E402
from incident_mcp.knowledge import KnowledgeIndex  # noqa: E402
from evals.release_provenance import runtime_provenance  # noqa: E402

CONTRACT = "synthetic-cross-service-compiled-langgraph-v1"
OFFSETS = [-60, -45, -30, -15, 0]
NORMAL = {"request_latency_p95": 100, "request_error_rate": 0.001,
          "db_pool_active_ratio": 0.3, "cpu_utilization": 0.1,
          "memory_working_set": 32 * 1024 * 1024, "kafka_consumer_lag": 0,
          "retry_rate": 0, "cache_miss_rate": 0}
UNITS = {"request_latency_p95": "milliseconds", "request_error_rate": "ratio",
         "db_pool_active_ratio": "ratio", "cpu_utilization": "ratio", "memory_working_set": "bytes",
         "kafka_consumer_lag": "messages", "retry_rate": "retries_per_second", "cache_miss_rate": "misses_per_second"}
# Independent operator-authored snapshot values, not imported from PROFILES or
# the candidate assessor. Family names below never enter the provider input.
FAULTS = {
    "cpu_saturation": ("cpu_utilization", [0.95] * 5, "request completed"),
    "database_connection_pool_exhaustion": ("db_pool_active_ratio", [1.0] * 5, "database connection acquisition timed out"),
    "memory_leak": ("memory_working_set", [(32 + 4 * index) * 1024 * 1024 for index in range(5)], "request completed"),
    "deployment_regression": ("request_error_rate", [0.3] * 5, "invalid ledger response shape"),
    "kafka_consumer_lag": ("kafka_consumer_lag", [300] * 5, "request completed"),
    "retry_storm": ("retry_rate", [8] * 5, "retry budget exhausted"),
    "cache_penetration": ("cache_miss_rate", [8] * 5, "cache misses reached origin"),
}
# Deliberately independent scorer contract for these operator-authored lab
# observations. Do not import the assessor, its profile inference or windows:
# a workflow's own green flags must not validate the workflow's attribution.
SCORING_THRESHOLDS = {"request_latency_p95": 1000, "request_error_rate": 0.05,
                      "db_pool_active_ratio": 0.9, "cpu_utilization": 0.8,
                      "kafka_consumer_lag": 100, "retry_rate": 1, "cache_miss_rate": 1}
SCORING_METRICS = {"cpu_saturation": "cpu_utilization",
                   "database_connection_pool_exhaustion": "db_pool_active_ratio",
                   "memory_leak": "memory_working_set", "kafka_consumer_lag": "kafka_consumer_lag",
                   "retry_storm": "retry_rate", "cache_penetration": "cache_miss_rate"}
SCORING_LOG_SIGNATURES = {"database_connection_pool_exhaustion": "acquisition timed out",
                          "deployment_regression": "invalid ledger response shape"}


def snapshot(*, offset=0, symptom=False):
    metrics = {metric: [value] * 5 for metric, value in NORMAL.items()}
    if symptom:
        metrics["request_latency_p95"] = [2400] * 5
    return {"metrics": metrics, "sample_offsets_seconds": [offset + value for value in OFFSETS],
            "logs": [{"message": "downstream request timed out" if symptom else "request completed",
                      "offset_seconds": offset}], "errors": {}}


def fault_snapshot(family, *, offset=0):
    value = snapshot(offset=offset)
    metric, samples, message = FAULTS[family]
    value["metrics"][metric] = samples.copy()
    value["metrics"]["request_latency_p95"] = [2400] * 5
    value["logs"] = [{"message": message, "offset_seconds": offset}]
    return value


def fixture(case_id, complaint, snapshots, *, catalog=(), observed=(), legacy=None,
            pairs=(), diagnosis=None, local_type="downstream_timeout", structure):
    snapshots = deepcopy(snapshots)
    snapshots.setdefault(complaint, snapshot(symptom=True))
    for source, target in [*catalog, *(call[:2] for call in observed)]:
        snapshots.setdefault(source, snapshot())
        snapshots.setdefault(target, snapshot())
    pairs = [list(pair) for pair in pairs]
    return {"id": case_id, "structure": structure,
            "input": {"question": f"Why is {complaint} slow today?", "environment": "benchmark",
                      "snapshots": snapshots,
                      "catalog_edges": [{"source": source, "target": target} for source, target in catalog],
                      "observed_calls": [{"source": call[0], "target": call[1], "offset_seconds": call[2] if len(call) == 3 else 0}
                                         for call in observed], "legacy_dependencies": legacy or {}},
            "expectations": {"complaint_service": complaint, "complaint_incident_type": local_type,
                             "ground_truth_fault_service": pairs[0][0] if len(pairs) == 1 else None,
                             "ground_truth_fault_family": pairs[0][1] if len(pairs) == 1 else None,
                             "remote_candidate_pairs": pairs, "diagnosis_service": diagnosis,
                             "check_diagnosis_service": bool(pairs)}}


def fixtures():
    """Distinct structure/observation challenges, not seed copies to pad count."""
    cases = []
    family_pairs = [
        ("cpu_saturation", "payment-service", "risk-engine", "catalog_plus_observed"),
        ("database_connection_pool_exhaustion", "checkout-api", "payment-service", "catalog_only"),
        ("memory_leak", "payment-service", "ledger-service", "observed_only"),
        ("deployment_regression", "checkout-api", "payment-service", "release_log_and_error_window"),
        ("kafka_consumer_lag", "payment-service", "stream-worker", "lag_specific_local_window"),
        ("retry_storm", "checkout-api", "payment-service", "retry_specific_local_window"),
        ("cache_penetration", "payment-service", "cache-service", "cache_specific_local_window"),
    ]
    for family, complaint, target, structure in family_pairs:
        observed = [(complaint, target)] if structure in {"catalog_plus_observed", "observed_only"} else []
        catalog = [] if structure == "observed_only" else [(complaint, target)]
        cases.append(fixture(f"direct-{family}", complaint, {target: fault_snapshot(family)},
            catalog=catalog, observed=observed, pairs=[(target, family)], diagnosis=target, structure=structure))
    cases.extend([
        fixture("two-hop-mixed", "checkout-api", {"payment-service": snapshot(symptom=True), "risk-engine": fault_snapshot("cpu_saturation")},
            catalog=[("checkout-api", "payment-service")], observed=[("payment-service", "risk-engine")],
            pairs=[("risk-engine", "cpu_saturation")], diagnosis="risk-engine", structure="two_hop_mixed_catalog_trace"),
        fixture("two-hop-observed", "checkout-api", {"payment-service": snapshot(symptom=True), "risk-engine": fault_snapshot("retry_storm")},
            observed=[("checkout-api", "payment-service"), ("payment-service", "risk-engine")],
            pairs=[("risk-engine", "retry_storm")], diagnosis="risk-engine", structure="two_hop_trace_only"),
        fixture("fanout-healthy-decoys", "payment-service", {"risk-engine": fault_snapshot("cpu_saturation")},
            catalog=[("payment-service", "ledger-db"), ("payment-service", "risk-engine"), ("payment-service", "cache-service")],
            pairs=[("risk-engine", "cpu_saturation")], diagnosis="risk-engine", structure="fanout_normal_siblings"),
        fixture("upstream-specific-distractor", "checkout-api", {"payment-service": fault_snapshot("database_connection_pool_exhaustion"), "risk-engine": fault_snapshot("cpu_saturation")},
            catalog=[("risk-engine", "checkout-api"), ("checkout-api", "payment-service")],
            pairs=[("payment-service", "database_connection_pool_exhaustion")], diagnosis="payment-service", structure="anomalous_upstream_excluded"),
        fixture("two-branch-competing-causes", "payment-service", {"risk-engine": fault_snapshot("cpu_saturation"), "ledger-db": fault_snapshot("database_connection_pool_exhaustion")},
            catalog=[("payment-service", "risk-engine"), ("payment-service", "ledger-db")],
            pairs=[("risk-engine", "cpu_saturation"), ("ledger-db", "database_connection_pool_exhaustion")], structure="ambiguous_branch_sources"),
        fixture("two-chain-competing-causes", "checkout-api", {"payment-service": fault_snapshot("cpu_saturation"), "risk-engine": fault_snapshot("database_connection_pool_exhaustion")},
            catalog=[("checkout-api", "payment-service"), ("payment-service", "risk-engine")],
            pairs=[("payment-service", "cpu_saturation"), ("risk-engine", "database_connection_pool_exhaustion")], structure="ambiguous_faults_on_chain"),
    ])
    ambiguous = fault_snapshot("cpu_saturation")
    ambiguous["metrics"]["db_pool_active_ratio"] = [1.0] * 5
    cases.append(fixture("two-local-families-one-service", "payment-service", {"risk-engine": ambiguous},
        catalog=[("payment-service", "risk-engine")], pairs=[("risk-engine", "cpu_saturation"), ("risk-engine", "database_connection_pool_exhaustion")],
        structure="same_service_family_ambiguity"))
    unclassified = snapshot(symptom=True)
    unclassified["logs"] = [{"message": "request completed", "offset_seconds": 0}]
    cases.append(fixture("unclassified-complaint-specific-dependency", "payment-service",
        {"payment-service": unclassified, "risk-engine": fault_snapshot("cpu_saturation")}, catalog=[("payment-service", "risk-engine")],
        pairs=[("risk-engine", "cpu_saturation")], diagnosis="risk-engine", local_type=None, structure="symptom_window_without_local_family"))
    cases.extend([
        fixture("reverse-upstream-only", "payment-service", {"risk-engine": fault_snapshot("cpu_saturation")}, catalog=[("risk-engine", "payment-service")], structure="direction_reversed"),
        fixture("legacy-directionless", "payment-service", {"risk-engine": fault_snapshot("cpu_saturation")}, legacy={"payment-service": ["risk-engine"], "risk-engine": ["payment-service"]}, structure="name_only_legacy_adapter"),
        fixture("two-node-loop", "payment-service", {"risk-engine": fault_snapshot("cpu_saturation")}, catalog=[("payment-service", "risk-engine"), ("risk-engine", "payment-service")], structure="two_node_directed_cycle"),
        fixture("three-node-loop", "checkout-api", {"risk-engine": fault_snapshot("cpu_saturation")}, catalog=[("checkout-api", "payment-service"), ("payment-service", "risk-engine"), ("risk-engine", "checkout-api")], structure="three_node_directed_cycle"),
        fixture("third-hop-outside-budget", "checkout-api", {"risk-engine": fault_snapshot("cpu_saturation")}, catalog=[("checkout-api", "payment-service"), ("payment-service", "ledger-service"), ("ledger-service", "risk-engine")], structure="three_hop_source_outside_scope"),
        fixture("stale-local-resource", "payment-service", {"risk-engine": fault_snapshot("cpu_saturation", offset=-900)}, catalog=[("payment-service", "risk-engine")], structure="stale_local_signal"),
        fixture("future-local-resource", "payment-service", {"risk-engine": fault_snapshot("cpu_saturation", offset=120)}, catalog=[("payment-service", "risk-engine")], structure="future_local_signal"),
        fixture("nonoverlapping-local-resource", "payment-service", {"risk-engine": fault_snapshot("cpu_saturation", offset=-180)}, catalog=[("payment-service", "risk-engine")], structure="current_but_disjoint_windows"),
        fixture("healthy-complaint", "payment-service", {"payment-service": snapshot(), "risk-engine": fault_snapshot("cpu_saturation")}, catalog=[("payment-service", "risk-engine")], local_type=None, structure="no_primary_symptom_window"),
        fixture("latency-only-downstream", "payment-service", {"risk-engine": snapshot(symptom=True)}, catalog=[("payment-service", "risk-engine")], structure="downstream_timeout_is_symptom_not_source"),
        fixture("empty-downstream", "payment-service", {"risk-engine": {"metrics": {}, "logs": [], "sample_offsets_seconds": OFFSETS.copy(), "errors": {}}}, catalog=[("payment-service", "risk-engine")], structure="missing_all_local_observations"),
    ])
    for failure in ("transport", "timeout"):
        unavailable = snapshot()
        unavailable["errors"] = {"query_metrics": failure, "search_logs": failure, "query_traces": failure}
        cases.append(fixture(f"local-query-{failure}", "payment-service", {"risk-engine": unavailable},
            catalog=[("payment-service", "risk-engine")], structure=f"bounded_provider_raised_{failure}"))
    transient = snapshot(symptom=True)
    transient["metrics"]["cpu_utilization"] = [0.1] * 4 + [0.95]
    transient["logs"] = [{"message": "request completed", "offset_seconds": 0}]
    cases.extend([
        fixture("single-local-spike", "payment-service", {"risk-engine": transient}, catalog=[("payment-service", "risk-engine")], structure="one_sample_not_sustained"),
        fixture("catalog-trace-direction-conflict", "payment-service", {"risk-engine": fault_snapshot("cpu_saturation")}, catalog=[("payment-service", "risk-engine")], observed=[("risk-engine", "payment-service")], structure="bidirectional_conflicting_sources"),
        fixture("stale-observed-only-path", "payment-service", {"risk-engine": fault_snapshot("cpu_saturation")}, observed=[("payment-service", "risk-engine", -900)], structure="old_trace_cannot_establish_current_path"),
        fixture("ninth-related-service-omitted", "payment-service", {"zz-risk": fault_snapshot("cpu_saturation")},
            catalog=[("payment-service", f"dep-{number:02d}") for number in range(1, 9)] + [("payment-service", "zz-risk")], structure="wide_catalog_exceeds_related_and_deep_budgets"),
    ])
    return cases


class MultiServiceSnapshotTools:
    """Only fixture observations enter this strict provider; never scorer labels."""

    def __init__(self, observation_input, index):
        self.observations = deepcopy(observation_input["snapshots"])
        self.catalog = deepcopy(observation_input["catalog_edges"])
        self.observed = deepcopy(observation_input["observed_calls"])
        self.legacy = deepcopy(observation_input["legacy_dependencies"])
        self.index = index
        self.now = datetime.now(UTC)
        self.calls = []
        self.mutation_attempts = 0

    def record(self, tool, service, arguments, result=None, error_type=None):
        def serialize(value):
            if hasattr(value, "model_dump"):
                return value.model_dump(mode="json")
            if isinstance(value, list):
                return [serialize(item) for item in value]
            return value
        self.calls.append({"sequence": len(self.calls) + 1, "tool": tool, "service": service,
                           "arguments": arguments, "read_only": tool not in {"restart_deployment", "rollback_release"},
                           "result": serialize(result), "error_type": error_type})

    def require_service(self, service, tool, arguments):
        if service not in self.observations:
            self.record(tool, service, arguments, error_type="McpToolError")
            raise McpToolError("Service is outside the synthetic fixture.")
        failure = self.observations[service]["errors"].get(tool)
        if failure:
            self.record(tool, service, arguments, error_type="TimeoutError" if failure == "timeout" else "McpToolError")
            if failure == "timeout":
                raise TimeoutError("Synthetic bounded provider timeout.")
            raise McpToolError("Synthetic bounded provider unavailable.")

    def evidence(self, service, kind, title, observation, *, query=None, source="synthetic-multi-service-snapshot", attributes=None, observed_at=None):
        return Evidence(kind=kind, service=service, source=source, title=title, observation=observation,
                        query=query, score=0.9, observed_at=observed_at or self.now,
                        attributes={**(attributes or {}), "data_origin": "synthetic"})

    async def query_metrics(self, service, metrics, **kwargs):
        arguments = {"service": service, "metrics": list(metrics)}
        self.require_service(service, "query_metrics", arguments)
        if any(metric not in NORMAL for metric in metrics) or len(metrics) > 8:
            self.record("query_metrics", service, arguments, error_type="McpToolError")
            raise McpToolError("Metric query is outside the bounded synthetic contract.")
        observed = self.observations[service]
        result = []
        for metric in metrics:
            if metric not in observed["metrics"]:
                continue
            values = observed["metrics"][metric]
            offsets = observed["sample_offsets_seconds"]
            if len(values) != len(offsets):
                raise ValueError("Fixture sample/value lengths differ.")
            series = [{"timestamp": (self.now + timedelta(seconds=offset)).isoformat(), "value": value}
                      for offset, value in zip(offsets, values, strict=True)]
            result.append(self.evidence(service, "metric", metric, f"{metric} retained synthetic samples", query=metric,
                attributes={"series": series, "unit": UNITS[metric]},
                observed_at=datetime.fromisoformat(series[-1]["timestamp"]) if series else self.now))
        self.record("query_metrics", service, arguments, result)
        return result

    async def search_logs(self, service, query):
        arguments = {"service": service, "query": query}
        self.require_service(service, "search_logs", arguments)
        events = [{"message": entry["message"], "timestamp": (self.now + timedelta(seconds=entry["offset_seconds"])).isoformat()}
                  for entry in self.observations[service]["logs"]]
        result = [self.evidence(service, "log", "synthetic service logs", " | ".join(event["message"] for event in events),
                  query=query, attributes={"events": events})] if events else []
        self.record("search_logs", service, arguments, result)
        return result

    async def query_traces(self, service):
        self.require_service(service, "query_traces", {"service": service})
        self.record("query_traces", service, {"service": service}, [])
        return []

    async def get_service_topology(self, service):
        self.require_service(service, "get_service_topology", {"service": service})
        if self.legacy:
            result = legacy_topology(service, self.legacy.get(service, []))
        else:
            edges = [edge for edge in self.catalog if service in (edge["source"], edge["target"])]
            calls = []
            for number, call in enumerate(self.observed):
                if service not in (call["source"], call["target"]):
                    continue
                identity = hashlib.sha256(f"{number}:{call['source']}:{call['target']}".encode()).hexdigest()
                calls.append({"source": call["source"], "target": call["target"], "trace_id": identity[:32],
                              "client_span_id": identity[32:48], "server_span_id": identity[48:64],
                              "observed_at": (self.now + timedelta(seconds=call["offset_seconds"])).isoformat()})
            observed = {"status": "available", "data": {"service": service, "source": "tempo_client_server_parentage",
                "start": (self.now - timedelta(minutes=30)).isoformat(), "end": self.now.isoformat(),
                "coverage": "sampled_not_exhaustive", "traces_read": min(len(calls), 8), "traces_failed": 0,
                "calls_omitted": 0, "calls": calls}} if calls else {"status": "not_supported"}
            result = parse_topology(service, {"service": service, "edges": edges,
                "source": "service-catalog-via-telemetry-gateway", "topology_kind": "static_fixture_catalog",
                "catalog_status": "available", "observed": observed})
        self.record("get_service_topology", service, {"service": service}, result)
        return result

    async def get_service_dependencies(self, service):
        return (await self.get_service_topology(service)).dependencies

    async def search_incident_knowledge(self, service, query):
        self.require_service(service, "search_incident_knowledge", {"service": service, "query": query})
        result = [self.evidence(service, item["document_type"], item["title"], item["excerpt"], query=query,
                  source=item["uri"], attributes=item) for item in self.index.search(service=service, query=query,
                  content_types={"runbook", "past_incident", "technical_doc"}, limit=5)]
        self.record("search_incident_knowledge", service, {"service": service, "query": query}, result)
        return result

    async def get_recent_deployments(self, service, namespace="benchmark"):
        self.require_service(service, "get_recent_deployments", {"service": service, "namespace": namespace})
        result = [self.evidence(service, "deployment", "synthetic deployment history", "Snapshot revision, not a live rollback target",
                  attributes={"revision": "42", "previous_revision": "41", "namespace": namespace})]
        self.record("get_recent_deployments", service, {"service": service, "namespace": namespace}, result)
        return result

    async def rollback_release(self, **arguments):
        self.mutation_attempts += 1
        self.record("rollback_release", arguments.get("service"), arguments, error_type="RuntimeError")
        raise RuntimeError("Synthetic benchmark forbids destructive preflight, approval and execution.")

    async def restart_deployment(self, **arguments):
        self.mutation_attempts += 1
        self.record("restart_deployment", arguments.get("service"), arguments, error_type="RuntimeError")
        raise RuntimeError("Synthetic benchmark forbids destructive preflight, approval and execution.")


def scoring_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def metric_support_window(item, *, anchor, metric):
    """Independently verify a recent, sustained cited metric signature."""
    if (anchor is None or metric not in UNITS or item.get("kind") != "metric" or item.get("query") != metric
            or item.get("attributes", {}).get("unit") != UNITS.get(metric)):
        return []
    raw = item.get("attributes", {}).get("series")
    if not isinstance(raw, list) or len(raw) > 2048:
        return []
    samples = {}
    for point in raw:
        if not isinstance(point, dict):
            continue
        stamp, value = scoring_time(point.get("timestamp")), point.get("value")
        if (stamp is None or not anchor - timedelta(minutes=5) <= stamp <= anchor
                or isinstance(value, bool) or not isinstance(value, (int, float))):
            continue
        try:
            numeric = float(value)
        except (OverflowError, ValueError):
            continue
        if not isfinite(numeric) or numeric < 0 or (UNITS[metric] == "ratio" and numeric > 1):
            continue
        if stamp in samples and samples[stamp] != numeric:
            return []
        samples[stamp] = numeric
    ordered = sorted(samples.items())
    if metric == "memory_working_set":
        recent = ordered[-5:]
        if len(recent) < 3:
            return []
        values = [value for _, value in recent]
        differences = [right - left for left, right in zip(values, values[1:])]
        if (any(delta < 0 for delta in differences) or sum(delta > 0 for delta in differences) < 2
                or values[-1] - values[0] < max(1024 * 1024, values[0] * 0.2)):
            return []
    else:
        threshold = SCORING_THRESHOLDS.get(metric)
        if threshold is None or len(ordered) < 2 or any(value <= threshold for _, value in ordered[-2:]):
            return []
        recent = ordered[-2:]
        for point in reversed(ordered[:-2]):
            if point[1] <= threshold or recent[0][0] - point[0] > timedelta(minutes=2):
                break
            recent.insert(0, point)
    if any(right[0] - left[0] > timedelta(minutes=2) for left, right in zip(recent, recent[1:])):
        return []
    return [(recent[0][0], recent[-1][0])]


def local_support_windows(item, *, anchor, family):
    if item.get("kind") == "metric" and family in SCORING_METRICS:
        return metric_support_window(item, anchor=anchor, metric=SCORING_METRICS[family])
    signature = SCORING_LOG_SIGNATURES.get(family)
    events = item.get("attributes", {}).get("events")
    if anchor is None or item.get("kind") != "log" or signature is None or not isinstance(events, list):
        return []
    result = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("message"), str):
            continue
        stamp = scoring_time(event.get("timestamp"))
        if (stamp is not None and anchor - timedelta(minutes=5) <= stamp <= anchor
                and signature in event["message"].lower()):
            result.append((stamp, stamp))
    return result


def independently_supported_candidate(candidate, evidence, *, anchor, excluded_ids):
    local_ids, symptom_ids = candidate.get("local_evidence_ids", []), candidate.get("symptom_evidence_ids", [])
    if (not local_ids or not symptom_ids or any(identifier not in evidence or identifier in excluded_ids
                                              for identifier in [*local_ids, *symptom_ids])):
        return False
    local = [local_support_windows(evidence[identifier], anchor=anchor, family=candidate["incident_type"])
             for identifier in local_ids]
    symptoms = [metric_support_window(evidence[identifier], anchor=anchor, metric=evidence[identifier].get("query"))
                for identifier in symptom_ids]
    # Every purported local/symptom citation must actually support its role.
    # A true temporal_overlap flag cannot repair healthy or expired evidence.
    if not all(local) or not all(symptoms):
        return False
    return any(left[0] <= right[1] and right[0] <= left[1]
               for group in local for left in group for symptom in symptoms for right in symptom)


def score_record(case, record):
    expected = case["expectations"]
    state = record.get("state") or {}
    complaint = expected["complaint_service"]
    candidates = state.get("root_cause_candidates", [])
    remote = sorted((candidate for candidate in candidates if candidate["service"] != complaint), key=lambda candidate: candidate["rank"])
    pairs = [(candidate["service"], candidate["incident_type"]) for candidate in remote]
    target_pairs = [tuple(pair) for pair in expected["remote_candidate_pairs"]]
    evidence = {item["id"]: item for item in state.get("evidence", [])}
    catalog_edges = {(edge["source"], edge["target"]) for edge in case["input"]["catalog_edges"]}
    observed_edges = {(call["source"], call["target"]) for call in case["input"]["observed_calls"]
                      if -300 <= call["offset_seconds"] <= 0}
    directed = catalog_edges | observed_edges
    anchor = scoring_time(record.get("snapshot_anchor"))
    excluded_ids = set(state.get("diagnostic_excluded_evidence_ids", []))
    remote_valid = all(
        candidate.get("causal_direction_verified") is False and candidate.get("temporal_overlap") is True
        and 2 <= len(candidate["propagation_path"]) <= 3
        and candidate["propagation_path"][0] == complaint and candidate["propagation_path"][-1] == candidate["service"]
        and len(set(candidate["propagation_path"])) == len(candidate["propagation_path"])
        and all(edge in directed for edge in zip(candidate["propagation_path"], candidate["propagation_path"][1:]))
        and bool(candidate["local_evidence_ids"]) and bool(candidate["symptom_evidence_ids"])
        and all(identifier in evidence and evidence[identifier]["service"] == candidate["service"]
                and evidence[identifier]["kind"] in {"metric", "log"}
                for identifier in candidate["local_evidence_ids"])
        and all(identifier in evidence and evidence[identifier]["service"] == complaint
                and evidence[identifier]["kind"] == "metric"
                and evidence[identifier]["query"] in {"request_latency_p95", "request_error_rate"}
                for identifier in candidate["symptom_evidence_ids"])
        for candidate in remote)
    retained_support_valid = all(independently_supported_candidate(
        candidate, evidence, anchor=anchor, excluded_ids=excluded_ids) for candidate in remote)
    basis_valid = True
    for candidate in remote:
        path = candidate.get("propagation_path", [])
        edges = list(zip(path, path[1:]))
        observed_count = sum(edge in observed_edges for edge in edges)
        expected_basis = ("observed_trace" if edges and observed_count == len(edges)
                          else "mixed" if observed_count else "catalog")
        basis_valid = basis_valid and bool(edges) and candidate.get("path_basis") == expected_basis
    deep_services = {call["service"] for call in record["tool_calls"] if call["service"] != complaint
                     and (call["tool"] in {"search_logs", "query_traces"}
                          or call["tool"] == "query_metrics" and any(metric not in {"request_latency_p95", "request_error_rate"}
                             for metric in call["arguments"]["metrics"]))}
    no_causal_claim = all(item.get("causal_direction_verified") is False for item in [*candidates, *state.get("hypotheses", [])])
    remote_hypotheses = [item for item in state.get("hypotheses", []) if item.get("cause_service") not in {None, complaint}]
    scoped_hypotheses = (not remote or bool(remote_hypotheses)) and all(
        any(candidate["service"] == hypothesis["cause_service"] and candidate["propagation_path"] == hypothesis["propagation_path"]
            for candidate in remote) and hypothesis["evidence_ids"]
        and all(identifier in evidence for identifier in hypothesis["evidence_ids"])
        for hypothesis in remote_hypotheses)
    checks = {
        "complaint_identity_preserved": bool(state.get("services")) and state["services"][0] == complaint and state.get("complaint_service") == complaint,
        "complaint_classification_preserved": state.get("incident_type") == expected["complaint_incident_type"],
        "remote_candidates_match": set(pairs) == set(target_pairs) and len(pairs) == len(set(pairs)),
        "diagnosis_candidate_not_overstated": not expected["check_diagnosis_service"] or state.get("diagnosis_service") == expected["diagnosis_service"],
        "remote_scope_paths_and_citations_valid": remote_valid,
        "retained_local_and_symptom_support_overlap": retained_support_valid,
        "path_basis_matches_independent_observations": basis_valid,
        "retained_evidence_ids_unique": len(evidence) == len(state.get("evidence", [])),
        "no_causal_direction_claim": no_causal_claim,
        "scoped_hypotheses_match_candidates": scoped_hypotheses,
        "bounded_services_and_deep_queries": len(set(state.get("services", []))) <= 9 and len(deep_services) <= 4,
        "advisory_only": bool(state.get("remediation")) and state["remediation"]["tool"] == "none"
                         and state["remediation"]["requires_approval"] is False and record["mutation_attempts"] == 0,
        "checkpoint_roundtrip": record["checkpoint_roundtrip"],
        "compiled_workflow_completed": record.get("error_type") is None and bool(state.get("trace")),
    }
    return {"checks": checks, "ok": all(checks.values()), "predicted_remote_pairs": [list(pair) for pair in pairs],
            "localization_top1": pairs[:1] == target_pairs if len(target_pairs) == 1 else None,
            "localization_top3": bool(set(target_pairs) <= set(pairs[:3])) if target_pairs else None,
            "unique_diagnosis_localization": state.get("diagnosis_service") == expected["ground_truth_fault_service"]
                                                if expected["ground_truth_fault_service"] is not None else None,
            "remote_false_positive": bool(remote) and not target_pairs, "deep_services": sorted(deep_services)}


async def run_case(case, index):
    # Passing only observations is deliberate. No truth labels or fixture IDs
    # are available through this object, IncidentQuestion or model prompting.
    tools = MultiServiceSnapshotTools(case["input"], index)
    runtime = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
        approval_signer=ApprovalReceiptSigner("cross-service-benchmark-no-execution-secret"), hypothesis_generator=None)
    started = time.perf_counter()
    state, checkpoint_roundtrip, error_type = None, False, None
    try:
        diagnosed = await runtime.start(IncidentQuestion(question=case["input"]["question"], environment=case["input"]["environment"]))
        retained = await runtime.get(diagnosed.incident_id)
        state = diagnosed.model_dump(mode="json")
        checkpoint_roundtrip = retained is not None and retained.model_dump() == diagnosed.model_dump()
    except Exception as error:
        error_type = type(error).__name__
    record = {"case_id": case["id"], "structure": case["structure"], "expectations": deepcopy(case["expectations"]),
              "input": deepcopy(case["input"]), "snapshot_anchor": tools.now.isoformat(), "state": state,
              "tool_calls": tools.calls, "mutation_attempts": tools.mutation_attempts,
              "checkpoint_roundtrip": checkpoint_roundtrip, "error_type": error_type,
              "latency_ms": (time.perf_counter() - started) * 1000,
              "model_calls": 0, "new_api_cost_usd": 0}
    record.update(score_record(case, record))
    return record


def fingerprints():
    sources = [Path(__file__)]
    for folder in ("services/orchestrator/incident_copilot", "services/mcp-server/incident_mcp"):
        sources.extend(path for path in (ROOT / folder).rglob("*") if path.is_file() and path.suffix in {".py", ".json"})
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(set(sources))}


def summarize(cases, records, sources):
    def rate(field):
        values = [record[field] for record in records if record[field] is not None]
        return statistics.mean(values) if values else None
    negatives = [record for record in records if not record["expectations"]["remote_candidate_pairs"]]
    return {"contract": CONTRACT, "metric_scope": "synthetic candidate service localization, not model causal accuracy or faithfulness",
            "measured_at": datetime.now(UTC).isoformat(), "cases": len(records),
            "independent_fixture_structures": len({case["structure"] for case in cases}),
            "fixtures_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True, allow_nan=False).encode()).hexdigest(),
            "source_fingerprints": sources,
            "source_tree_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
            "synthetic_remote_candidate_service_family_top1": rate("localization_top1"),
            "synthetic_remote_candidate_service_family_top3": rate("localization_top3"),
            "synthetic_unique_diagnosis_service_localization": rate("unique_diagnosis_localization"),
            "negative_remote_candidate_abstention": statistics.mean(record["checks"]["compiled_workflow_completed"] and not record["remote_false_positive"] for record in negatives) if negatives else None,
            "metric_case_counts": {"service_family_top1": sum(record["localization_top1"] is not None for record in records),
                                   "service_family_top3": sum(record["localization_top3"] is not None for record in records),
                                   "unique_diagnosis_service": sum(record["unique_diagnosis_localization"] is not None for record in records),
                                   "negative_remote_abstention": len(negatives), "all_contract_checks": len(records)},
            "all_contract_checks_pass_rate": statistics.mean(record["ok"] for record in records) if records else None,
            "mean_snapshot_workflow_latency_ms": statistics.mean(record["latency_ms"] for record in records) if records else None,
            "mutation_attempts": sum(record["mutation_attempts"] for record in records),
            "model_calls": 0, "new_api_cost_usd": 0, "live_root_cause_accuracy": None,
            "model_causal_accuracy": None, "semantic_faithfulness": None, "causal_action_efficacy": None,
            "failures": [{"case_id": record["case_id"], "error_type": record["error_type"],
                          "failed_checks": [key for key, value in record["checks"].items() if not value]} for record in records if not record["ok"]],
            "limitations": ["Distinct synthetic structures, not independently observed production incidents",
                            "Compiled LangGraph and BM25 executed, but no model, live MCP, database or Kubernetes acceptance",
                            "Source and time/path/citation scope validation does not prove semantic entailment or causal propagation",
                            "A unique diagnosis_service is an investigation candidate, never a confirmed root cause",
                            "Timeout fixtures raise immediately; cancellation wall-clock performance is not measured"]}


def save_report(records, summary, destination, *, run_id=None):
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    if not isinstance(run_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id) is None:
        raise ValueError("Invalid benchmark run ID.")
    destination.mkdir(parents=True, exist_ok=True)
    cases_path = destination / f"cross_service_benchmark_{run_id}_cases.jsonl"
    summary_path = destination / f"cross_service_benchmark_{run_id}.json"
    if cases_path.exists() or summary_path.exists():
        raise FileExistsError("Benchmark artifacts already exist.")
    payload = "".join(json.dumps(record, sort_keys=True, allow_nan=False) + "\n" for record in records)
    summary = {**summary, "run_id": run_id, "case_records_file": cases_path.name,
               "case_records_sha256": hashlib.sha256(payload.encode()).hexdigest()}
    with cases_path.open("x") as handle:
        handle.write(payload)
    with summary_path.open("x") as handle:
        handle.write(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return summary_path, cases_path


async def _main():
    cases = fixtures()
    sources = fingerprints()
    index = KnowledgeIndex.load_default()
    records = [await run_case(case, index) for case in cases]
    summary = summarize(cases, records, sources)
    summary["runtime_provenance"] = runtime_provenance()
    after_sources = fingerprints()
    summary["source_stable_during_run"] = after_sources == sources
    summary["source_fingerprints_after"] = after_sources
    if after_sources != sources:
        summary["failures"].append({"case_id": None, "error_type": "SourceChangedDuringRun",
                                    "failed_checks": ["source_stable_during_run"]})
    paths = save_report(records, summary, ROOT / "evals/results")
    print(json.dumps({**summary, "artifact_paths": [str(path.relative_to(ROOT)) for path in paths]}, indent=2, sort_keys=True))
    if summary["failures"]:
        raise SystemExit(1)


async def main():
    with tracing_context(enabled=False):
        await _main()


if __name__ == "__main__":
    asyncio.run(main())
