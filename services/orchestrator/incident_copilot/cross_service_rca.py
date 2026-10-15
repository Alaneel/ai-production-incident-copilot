"""Bounded, service-local cause candidates; call direction is not causation.

This module performs no I/O and never chooses or authorizes a remediation.
Even an observed trace path plus overlapping failures is only a hypothesis.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from .action_signal_time import current_action_signals, timestamp
from .dependency_topology import ObservedCall, ServiceTopology
from .incident_profiles import has_memory_growth, infer_profiles_from_evidence
from .models import Evidence, RootCauseCandidate
from .signal_thresholds import METRIC_UNITS, THRESHOLDS

WINDOW = timedelta(minutes=5)
MAX_GAP = timedelta(minutes=2)
MAX_TOPOLOGIES = 32
MAX_EVIDENCE = 512
MAX_POINTS = 2048
SERVICE = re.compile(r"[a-z0-9][a-z0-9-]{1,79}")
CATALOG_SOURCES = {"service-catalog", "service-catalog-via-telemetry-gateway"}
PROFILE_METRICS = {
    "database_connection_pool_exhaustion": "db_pool_active_ratio",
    "cpu_saturation": "cpu_utilization",
    "memory_leak": "memory_working_set",
    "kafka_consumer_lag": "kafka_consumer_lag",
    "retry_storm": "retry_rate",
    "cache_penetration": "cache_miss_rate",
}
PROFILE_LOGS = {
    "database_connection_pool_exhaustion": "acquisition timed out",
    "deployment_regression": "invalid ledger response shape",
    "downstream_timeout": "downstream request timed out",
}
SLI_METRICS = {"request_latency_p95", "request_error_rate"}
BASIS_ORDER = {"primary": 0, "observed_trace": 0, "mixed": 1, "catalog": 2}


def _clock(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Cross-service analysis requires an aware clock.")
    return now.astimezone(UTC)


def _name(value: Any) -> bool:
    return isinstance(value, str) and SERVICE.fullmatch(value) is not None


def _count(value: Any, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def _observed_calls(topology: ServiceTopology, now: datetime) -> list[ObservedCall]:
    observed = topology.observed
    if (observed.status != "available" or observed.source != "tempo_client_server_parentage"
            or observed.coverage not in {"sampled_not_exhaustive", "partial_sample_not_exhaustive"}
            or not _count(observed.traces_read, 8) or observed.traces_read == 0
            or not _count(observed.traces_failed, 8)
            or observed.traces_read + observed.traces_failed > 8
            or not _count(observed.calls_omitted, 16384)
            or not _count(observed.calls_discarded, 16384)
            or (observed.traces_failed > 0) != (observed.coverage == "partial_sample_not_exhaustive")
            or not isinstance(observed.start, datetime) or observed.start.tzinfo is None
            or not isinstance(observed.end, datetime) or observed.end.tzinfo is None
            or not timedelta(0) < observed.end - observed.start <= timedelta(minutes=30)
            or observed.end > now + timedelta(seconds=5)
            or observed.end < now - WINDOW
            or len(observed.calls) > 64):
        return []
    identities = Counter((call.trace_id, call.client_span_id, call.server_span_id)
                         for call in observed.calls)
    calls = []
    for call in observed.calls:
        identity = (call.trace_id, call.client_span_id, call.server_span_id)
        if (not all(_name(name) for name in (call.source, call.target))
                or call.source == call.target or topology.service not in (call.source, call.target)
                or identities[identity] != 1 or call.client_span_id == call.server_span_id
                or any(set(identifier) == {"0"} for identifier in identity)
                or re.fullmatch(r"[0-9a-f]{32}", call.trace_id) is None
                or any(re.fullmatch(r"[0-9a-f]{16}", value) is None
                       for value in (call.client_span_id, call.server_span_id))
                or not isinstance(call.observed_at, datetime) or call.observed_at.tzinfo is None
                or not observed.start <= call.observed_at <= observed.end
                or not now - WINDOW <= call.observed_at <= now):
            continue
        calls.append(call)
    return calls if len({call.trace_id for call in calls}) <= observed.traces_read else []


def _cyclic_nodes(graph: dict[str, set[str]]) -> set[str]:
    """Tarjan SCCs exclude feedback/contradictory directions, not just revisits."""
    index = 0
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()
    cyclic: set[str] = set()

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        active.add(node)
        for target in sorted(graph.get(node, set())):
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            component = []
            while True:
                member = stack.pop()
                active.remove(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                cyclic.update(component)

    for node in sorted(set(graph) | {target for targets in graph.values() for target in targets}):
        if node not in indices:
            visit(node)
    return cyclic


def dependency_paths(
    complaint_service: str, topologies: list[ServiceTopology], *, now: datetime, max_hops: int = 2,
) -> dict[str, dict]:
    """Return explicit, non-cyclic downstream call paths of at most two hops.

    Catalog paths remain catalog candidates. Opposing catalog/observed edges
    are deliberately not resolved by guessing which direction is causal.
    """
    now = _clock(now)
    if not _name(complaint_service):
        raise ValueError("Invalid complaint service.")
    if not isinstance(max_hops, int) or isinstance(max_hops, bool) or not 0 <= max_hops <= 2:
        raise ValueError("Dependency paths support zero to two hops only.")
    result = {complaint_service: {"propagation_path": [complaint_service], "path_basis": "primary"}}
    if not isinstance(topologies, (list, tuple)) or len(topologies) > MAX_TOPOLOGIES:
        return result
    edges: dict[tuple[str, str], dict] = {}
    call_identities: dict[tuple[str, str, str], set[tuple[str, str, str]]] = defaultdict(set)

    def retain(source: str, target: str, *, catalog: bool = False, call: ObservedCall | None = None) -> None:
        edge = edges.setdefault((source, target), {"catalog": False, "calls": {}})
        edge["catalog"] = edge["catalog"] or catalog
        if call is not None:
            identity = (call.trace_id, call.client_span_id, call.server_span_id)
            normalized = call.model_dump(mode="json")
            normalized["observed_at"] = call.observed_at.astimezone(UTC).isoformat()
            edge["calls"][identity] = normalized
            call_identities[identity].add((source, target, normalized["observed_at"]))

    for topology in topologies:
        if not isinstance(topology, ServiceTopology) or not _name(topology.service):
            continue
        if (topology.source in CATALOG_SOURCES and topology.catalog_status == "available"
                and len(topology.edges) <= 64):
            for edge in topology.edges:
                if (all(_name(name) for name in (edge.source, edge.target)) and edge.source != edge.target
                        and topology.service in (edge.source, edge.target)):
                    retain(edge.source, edge.target, catalog=True)
        for call in _observed_calls(topology, now):
            retain(call.source, call.target, call=call)
    # Re-reading the same physical call from both endpoints is legitimate;
    # conflicting endpoint/time claims for one span identity are not.
    ambiguous = {identity for identity, claims in call_identities.items() if len(claims) != 1}
    graph: dict[str, set[str]] = defaultdict(set)
    for (source, target), edge in edges.items():
        edge["calls"] = {identity: call for identity, call in edge["calls"].items() if identity not in ambiguous}
        if edge["catalog"] or edge["calls"]:
            graph[source].add(target)
    cyclic = _cyclic_nodes(graph)
    if complaint_service in cyclic:
        return result
    frontier = [[complaint_service]]
    for _ in range(max_hops):
        next_frontier = []
        for path in frontier:
            for target in sorted(graph.get(path[-1], set())):
                if target in path or target in cyclic:
                    continue
                candidate_path = [*path, target]
                path_edges = [edges[pair] for pair in zip(candidate_path, candidate_path[1:])]
                observed_count = sum(bool(edge["calls"]) for edge in path_edges)
                basis = ("observed_trace" if observed_count == len(path_edges)
                         else "mixed" if observed_count else "catalog")
                candidate = {"propagation_path": candidate_path, "path_basis": basis}
                calls = [edge["calls"][identity] for edge in path_edges for identity in sorted(edge["calls"])]
                if calls:
                    candidate["observed_calls"] = calls
                old = result.get(target)
                key = (BASIS_ORDER[basis], len(candidate_path), candidate_path)
                if old is None or key < (BASIS_ORDER[old["path_basis"]], len(old["propagation_path"]), old["propagation_path"]):
                    result[target] = candidate
                next_frontier.append(candidate_path)
        frontier = next_frontier
    return result


def _number(value: Any, metric: str) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    if not isfinite(number) or number < 0:
        return None
    if metric in {"request_error_rate", "db_pool_active_ratio", "cpu_utilization"} and number > 1:
        return None
    return number


def _service_signals(items: list[Evidence], now: datetime) -> tuple[list[Evidence], dict, list]:
    """Merge only same-service samples, retaining truthful point provenance."""
    metric_points: dict[str, dict[datetime, tuple[float, set[str]]]] = defaultdict(dict)
    conflicts: dict[str, set[datetime]] = defaultdict(set)
    representatives: dict[str, Evidence] = {}
    logs: list[Evidence] = []
    for item in items:
        if item.kind.value == "metric" and item.query in METRIC_UNITS:
            if "unit" in item.attributes and item.attributes["unit"] != METRIC_UNITS[item.query]:
                continue
            raw = item.attributes.get("series")
            if not isinstance(raw, list) or len(raw) > MAX_POINTS:
                continue
            representatives.setdefault(item.query, item)
            for point in raw:
                if not isinstance(point, dict):
                    continue
                time = timestamp(point.get("timestamp"))
                value = _number(point.get("value"), item.query)
                if time is None or value is None or not now - WINDOW <= time <= now:
                    continue
                previous = metric_points[item.query].get(time)
                if previous is not None and previous[0] != value:
                    conflicts[item.query].add(time)
                else:
                    ids = previous[1] if previous is not None else set()
                    metric_points[item.query][time] = (value, {*ids, item.id})
        elif item.kind.value == "log":
            raw = item.attributes.get("events")
            if not isinstance(raw, list) or len(raw) > MAX_POINTS:
                continue
            events = [event for event in raw if isinstance(event, dict)
                      and isinstance(event.get("message"), str)
                      and (time := timestamp(event.get("timestamp"))) is not None
                      and now - WINDOW <= time <= now]
            logs.append(item.model_copy(update={"attributes": {**item.attributes, "events": events}}))
    merged = []
    timelines = {}
    for metric in sorted(metric_points):
        # In-window contradictory samples cannot be repaired by selecting the
        # remaining green/red subset as an unambiguous local failure signal.
        points = [] if conflicts[metric] else sorted(
            (time, value, ids) for time, (value, ids) in metric_points[metric].items())
        start = 0
        for index in range(1, len(points)):
            if points[index][0] - points[index - 1][0] > MAX_GAP:
                start = index
        points = points[start:]
        timelines[metric] = points
        representative = representatives[metric]
        merged.append(representative.model_copy(update={"attributes": {**representative.attributes,
            "series": [{"timestamp": time.isoformat(), "value": value} for time, value, _ in points]}}))
    current, _ = current_action_signals([*merged, *logs], now=now)
    return current, timelines, logs


def _metric_support(metric: str, timelines: dict) -> tuple[set[str], list[tuple[datetime, datetime]]]:
    points = timelines.get(metric, [])
    if metric == "memory_working_set":
        points = points[-5:]
        if not has_memory_growth([value for _, value, _ in points]):
            return set(), []
    else:
        threshold = THRESHOLDS.get(metric)
        if threshold is None or len(points) < 2 or any(value <= threshold for _, value, _ in points[-2:]):
            return set(), []
        start = len(points) - 2
        while start > 0 and points[start - 1][1] > threshold:
            start -= 1
        points = points[start:]
    return set().union(*(ids for _, _, ids in points)), [(points[0][0], points[-1][0])]


def _profile_support(profile: str, timelines: dict, logs: list[Evidence]) -> tuple[set[str], list]:
    ids, windows = _metric_support(PROFILE_METRICS[profile], timelines) if profile in PROFILE_METRICS else (set(), [])
    signature = PROFILE_LOGS.get(profile)
    if signature:
        for item in logs:
            times = [timestamp(event["timestamp"]) for event in item.attributes["events"]
                     if signature in event["message"].lower()]
            if times:
                ids.add(item.id)
                windows.extend((time, time) for time in times)
    return ids, windows


def _overlaps(left: tuple[datetime, datetime], right: tuple[datetime, datetime]) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]


def assess_diagnostic_coverage(incident, *, now: datetime) -> dict:
    """Missing/failed bounded investigations are unknown, never healthy.

    Recomputed at proposal and execution boundaries; retained trace booleans
    or a truncated candidate list cannot grant action authority.
    """
    now = _clock(now)
    services = getattr(incident, "services", [])
    primary = services[0] if isinstance(services, list) and services else None
    if not _name(primary):
        return {"complete": False, "unresolved_services": [],
                "reasons": [{"service": primary, "reason": "complaint_scope_unavailable"}]}
    topologies = getattr(incident, "service_topologies", [])
    legacy = getattr(incident, "service_topology", None)
    if not topologies and isinstance(legacy, ServiceTopology):
        topologies = [legacy]
    if not isinstance(topologies, (list, tuple)) or len(topologies) > MAX_TOPOLOGIES:
        return {"complete": False, "unresolved_services": [],
                "reasons": [{"service": primary, "reason": "topology_scope_unavailable"}]}
    paths = dependency_paths(primary, topologies, now=now)
    remote = sorted(service for service in paths if service != primary)
    evidence = getattr(incident, "evidence", [])
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
        return {"complete": False, "unresolved_services": remote,
                "reasons": [{"service": primary, "reason": "evidence_scope_unavailable"}]}
    if not remote:
        return {"complete": True, "unresolved_services": [], "reasons": []}
    investigations = getattr(incident, "service_investigations", [])
    excluded = set(getattr(incident, "diagnostic_excluded_evidence_ids", []))
    counts = Counter(item.id for item in evidence if isinstance(item, Evidence))
    candidates = assess_root_candidates(incident, now=now)
    reasons = []
    for service in remote:
        local = [item for item in evidence if isinstance(item, Evidence) and item.service == service
                 and counts[item.id] == 1 and item.id not in excluded and item.score > 0
                 and item.observation.strip() and item.kind.value in {"metric", "log"}]
        records = [item for item in investigations if isinstance(item, dict) and item.get("service") == service]
        if len(records) != 1:
            reason = "downstream_diagnostics_omitted"
        elif records[0].get("query_errors"):
            reason = "downstream_diagnostics_failed"
        elif any(isinstance(item.attributes.get("series"), list)
                 and len(item.attributes["series"]) > MAX_POINTS for item in local):
            reason = "downstream_signal_scope_unavailable"
        elif any(item.kind.value == "metric" and item.query in METRIC_UNITS
                 and "unit" in item.attributes and item.attributes["unit"] != METRIC_UNITS[item.query]
                 for item in local):
            reason = "downstream_metric_unit_invalid"
        else:
            current, timelines, _ = _service_signals(local, now)
            anomalous_sli = any(_metric_support(metric, timelines)[0] for metric in SLI_METRICS)
            specific = [profile for profile in infer_profiles_from_evidence(current, now=now)
                        if profile.key != "downstream_timeout"]
            usable_sli = any(len(timelines.get(metric, [])) >= 2 for metric in SLI_METRICS)
            # An investigated intermediate hop may exhibit only propagated
            # symptoms while a more specific candidate is retained further
            # downstream. This preserves the candidate, not causal authority.
            downstream_candidate = any(service in candidate.propagation_path[1:-1] for candidate in candidates)
            reason = ("downstream_telemetry_unavailable" if not specific and not usable_sli
                      else "downstream_anomaly_unclassified" if anomalous_sli and not specific and not downstream_candidate else None)
        if reason:
            reasons.append({"service": service, "reason": reason})
    return {"complete": not reasons, "unresolved_services": [item["service"] for item in reasons], "reasons": reasons}


def assess_root_candidates(incident, *, now: datetime) -> list[RootCauseCandidate]:
    """Rank local or reachable overlapping fault hypotheses, never remedies."""
    now = _clock(now)
    complaint = getattr(incident, "complaint_service", None)
    services = getattr(incident, "services", [])
    if complaint is not None and (not isinstance(services, list) or not services or services[0] != complaint):
        return []
    if complaint is None:
        complaint = services[0] if isinstance(services, list) and services else None
    if not _name(complaint):
        return []
    topologies = getattr(incident, "service_topologies", [])
    legacy = getattr(incident, "service_topology", None)
    if not topologies and isinstance(legacy, ServiceTopology):
        topologies = [legacy]
    paths = dependency_paths(complaint, topologies, now=now)
    evidence = getattr(incident, "evidence", [])
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
        return []
    excluded = set(getattr(incident, "diagnostic_excluded_evidence_ids", []))
    counts = Counter(item.id for item in evidence if isinstance(item, Evidence))
    grouped: dict[str, list[Evidence]] = defaultdict(list)
    for item in evidence:
        if (isinstance(item, Evidence) and item.id not in excluded
                and item.service in paths and counts[item.id] == 1
                and isinstance(item.id, str) and 1 <= len(item.id) <= 200
                and item.score > 0 and item.observation.strip()
                and item.kind.value in {"metric", "log"}):
            grouped[item.service].append(item)
    observations = {service: _service_signals(items, now) for service, items in grouped.items()}
    _, complaint_metrics, _ = observations.get(complaint, ([], {}, []))
    symptom_ids: set[str] = set()
    symptom_windows = []
    for metric in sorted(SLI_METRICS):
        ids, windows = _metric_support(metric, complaint_metrics)
        symptom_ids.update(ids)
        symptom_windows.extend(windows)
    candidates = []
    for service in sorted(observations):
        current, timelines, logs = observations[service]
        for profile in infer_profiles_from_evidence(current, now=now):
            local_ids, local_windows = _profile_support(profile.key, timelines, logs)
            if not local_ids:
                continue
            primary = service == complaint
            overlap = (any(_overlaps(local, symptom) for local in local_windows for symptom in symptom_windows)
                       if symptom_windows else None)
            if not primary and (profile.key == "downstream_timeout" or not symptom_ids or overlap is not True):
                continue
            path = paths[service]
            notes = [
                "Service-local current failure signals support a candidate, not a confirmed root cause.",
                "Temporal overlap and call direction do not establish causation or authorize remediation.",
            ]
            if path["path_basis"] in {"catalog", "mixed"}:
                notes.append("The path includes static catalog relations; catalog reachability is not observed causal propagation.")
            elif not primary:
                notes.append("Sampled client/server parentage proves a call relationship, not failure propagation or exhaustive coverage.")
            candidates.append({"service": service, "incident_type": profile.key,
                "local_evidence_ids": sorted(local_ids), "symptom_evidence_ids": sorted(symptom_ids),
                "propagation_path": path["propagation_path"], "path_basis": path["path_basis"],
                "temporal_overlap": overlap, "causal_direction_verified": False,
                "validation_notes": notes})
    candidates.sort(key=lambda candidate: (
        candidate["incident_type"] == "downstream_timeout", BASIS_ORDER[candidate["path_basis"]],
        len(candidate["propagation_path"]) - 1, candidate["service"], candidate["incident_type"],
    ))
    return [RootCauseCandidate(rank=rank, **candidate) for rank, candidate in enumerate(candidates, start=1)]
