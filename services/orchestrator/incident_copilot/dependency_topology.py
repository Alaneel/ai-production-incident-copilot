"""Bounded catalog relations, deliberately separate from causal evidence."""
from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

SERVICE = re.compile(r"[a-z0-9][a-z0-9-]{1,79}")


class DependencyEdge(BaseModel):
    source: str
    target: str


class ObservedCall(DependencyEdge):
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    client_span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    server_span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    observed_at: datetime


class ObservedDependencies(BaseModel):
    status: Literal["available", "unavailable", "not_supported", "not_requested"] = "not_requested"
    source: Literal["tempo_client_server_parentage"] | None = None
    calls: list[ObservedCall] = Field(default_factory=list)
    start: datetime | None = None
    end: datetime | None = None
    coverage: Literal["sampled_not_exhaustive", "partial_sample_not_exhaustive"] | None = None
    traces_read: int = 0
    traces_failed: int = 0
    calls_omitted: int = 0
    calls_discarded: int = 0


def parse_observed(service: str, raw: object) -> ObservedDependencies:
    if raw is None:
        return ObservedDependencies()
    unavailable = ObservedDependencies(status="unavailable")
    if not isinstance(raw, dict):
        return unavailable
    status = raw.get("status")
    if status in ("not_supported", "unavailable", "not_requested"):
        return ObservedDependencies(status=status)
    data = raw.get("data")
    if status != "available" or not isinstance(data, dict) or data.get("service") != service or data.get("source") != "tempo_client_server_parentage":
        return unavailable
    coverage = data.get("coverage")
    if coverage not in ("sampled_not_exhaustive", "partial_sample_not_exhaustive"):
        return unavailable
    counts = [data.get(key) for key in ("traces_read", "traces_failed", "calls_omitted")]
    if any(not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 16384 for value in counts) or sum(counts[:2]) > 8:
        return unavailable
    if (counts[1] > 0) != (coverage == "partial_sample_not_exhaustive"):
        return unavailable
    try:
        start = datetime.fromisoformat(data["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(data["end"].replace("Z", "+00:00"))
        now = datetime.now(UTC)
        if start.tzinfo is None or end.tzinfo is None or not timedelta(0) < end - start <= timedelta(minutes=30) or end > now + timedelta(seconds=5) or end < now - timedelta(hours=1):
            return unavailable
    except (KeyError, TypeError, ValueError, AttributeError):
        return unavailable
    calls = data.get("calls")
    if not isinstance(calls, list) or len(calls) > 64 or (calls and counts[0] == 0):
        return unavailable
    identities = Counter(tuple(call.get(key) for key in ("trace_id", "client_span_id", "server_span_id"))
                         for call in calls if isinstance(call, dict)
                         and all(isinstance(call.get(key), str) for key in ("trace_id", "client_span_id", "server_span_id")))
    normalized, discarded = [], 0
    for raw_call in calls:
        try:
            call = ObservedCall.model_validate(raw_call)
            identity = (call.trace_id, call.client_span_id, call.server_span_id)
            if not all(SERVICE.fullmatch(name) for name in (call.source, call.target)) or call.source == call.target or service not in (call.source, call.target):
                raise ValueError("invalid service relation")
            if call.observed_at.tzinfo is None or not start <= call.observed_at <= end or identities[identity] != 1:
                raise ValueError("invalid observed time or duplicate")
            if any(set(identifier) == {"0"} for identifier in identity) or call.client_span_id == call.server_span_id:
                raise ValueError("invalid identifiers")
            normalized.append(call)
        except (ValueError, TypeError):
            discarded += 1
    if len({call.trace_id for call in normalized}) > counts[0]:
        return unavailable
    return ObservedDependencies(status="available", source="tempo_client_server_parentage", calls=normalized,
                                start=start, end=end, coverage=coverage, traces_read=counts[0], traces_failed=counts[1],
                                calls_omitted=counts[2], calls_discarded=discarded)


class ServiceTopology(BaseModel):
    service: str
    edges: list[DependencyEdge] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    source: Literal["service-catalog", "service-catalog-via-telemetry-gateway", "legacy_adapter", "unknown"] = "unknown"
    topology_kind: Literal["static_fixture_catalog", "unknown"] = "unknown"
    observed: ObservedDependencies = Field(default_factory=ObservedDependencies)
    catalog_status: Literal["available", "unavailable", "unknown"] = "unknown"

    def relation(self, service: str) -> str:
        upstream = any(e.source == service and e.target == self.service for e in self.observed.calls)
        downstream = any(e.target == service and e.source == self.service for e in self.observed.calls)
        if upstream and downstream:
            return "bidirectional_observed_call"
        if upstream:
            return "upstream_observed_call"
        if downstream:
            return "downstream_observed_call"
        upstream = any(e.source == service and e.target == self.service for e in self.edges)
        downstream = any(e.target == service and e.source == self.service for e in self.edges)
        if upstream and downstream:
            return "bidirectional_catalog_dependency"
        if upstream:
            return "upstream_catalog_dependency"
        if downstream:
            return "downstream_catalog_dependency"
        return "discovered_dependency"


def parse_topology(service: str, output: dict) -> ServiceTopology:
    if not isinstance(output, dict) or output.get("service") != service:
        raise ValueError("Dependency response does not match requested service.")
    raw_edges = output.get("edges", [])
    if not isinstance(raw_edges, list) or len(raw_edges) > 64:
        raise ValueError("Invalid or oversized dependency edge container.")
    edges, names, seen = [], [], set()
    for raw in raw_edges:
        if not isinstance(raw, dict):
            continue
        source, target = raw.get("source"), raw.get("target")
        if not all(isinstance(name, str) and SERVICE.fullmatch(name) for name in (source, target)):
            continue
        if source == target or service not in (source, target) or (source, target) in seen:
            continue
        seen.add((source, target))
        edges.append(DependencyEdge(source=source, target=target))
        candidate = target if source == service else source
        if candidate not in names:
            names.append(candidate)
    source = output.get("source")
    if source not in ("service-catalog", "service-catalog-via-telemetry-gateway"):
        source = "unknown"
    observed = parse_observed(service, output.get("observed"))
    observed_names = list(dict.fromkeys(call.target if call.source == service else call.source for call in observed.calls))
    names = list(dict.fromkeys([*observed_names, *names]))
    return ServiceTopology(service=service, edges=edges, dependencies=names, source=source,
                           topology_kind="static_fixture_catalog" if output.get("topology_kind") == "static_fixture_catalog" else "unknown",
                           observed=observed,
                           catalog_status=output.get("catalog_status") if output.get("catalog_status") in ("available", "unavailable") else "unknown")


def legacy_topology(service: str, dependencies: list[str]) -> ServiceTopology:
    if not isinstance(dependencies, list) or len(dependencies) > 64:
        raise ValueError("Invalid or oversized dependency service container.")
    names = list(dict.fromkeys(name for name in dependencies
                              if isinstance(name, str) and SERVICE.fullmatch(name) and name != service))
    # A name-only adapter supplies no edge-direction evidence.
    return ServiceTopology(service=service, dependencies=names, source="legacy_adapter")
