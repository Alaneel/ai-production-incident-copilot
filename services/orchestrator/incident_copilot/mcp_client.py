from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from itertools import count
from math import isfinite
from typing import Any

import httpx

from .models import Evidence, EvidenceKind
from .dependency_topology import ServiceTopology, parse_topology


class McpToolError(RuntimeError):
    pass


class McpTelemetryTools:
    """Typed MCP client adapter used by the production LangGraph workflow."""

    def __init__(
        self,
        *,
        url: str,
        read_token: str,
        operator_token: str,
        timeout_seconds: float = 8.0,
        max_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url
        self.read_token = read_token
        self.operator_token = operator_token
        self.max_attempts = max_attempts
        self._ids = count(1)
        self._initialized_tokens: set[str] = set()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def query_metrics(
        self, service: str, metrics: tuple[str, ...], *,
        start: datetime | None = None, end: datetime | None = None,
    ) -> list[Evidence]:
        end = end or datetime.now(UTC)
        start = start or end - timedelta(minutes=30)
        outputs = await asyncio.gather(
            *(
                self._call_tool(
                    "query_metrics",
                    {
                        "service": service,
                        "metric": metric,
                        "start": (
                            end - timedelta(minutes=5)
                            if metric == "memory_working_set"
                            else start
                        ).isoformat(),
                        "end": end.isoformat(),
                        # Keep the 30-minute context bounded to 121 evaluation
                        # points while retaining short incidents. A 60-second
                        # step can collapse a 30-second spike to one point.
                        "step_seconds": 5 if metric == "memory_working_set" else 15,
                    },
                )
                for metric in metrics
            )
        )
        evidence = []
        for index, (metric, output) in enumerate(zip(metrics, outputs, strict=True)):
            if output.get("service") != service or output.get("metric") != metric:
                raise McpToolError("Metric response does not match the requested service and metric.")
            raw_series = output.get("series", [])
            series_by_time: dict[datetime, dict[str, Any]] = {}
            conflicting_times: set[datetime] = set()
            query_start = end - timedelta(minutes=5) if metric == "memory_working_set" else start
            discarded_samples = 0
            if not isinstance(raw_series, list):
                raise McpToolError(f"Invalid metric series returned for {metric}.")
            for point in raw_series:
                if not isinstance(point, dict) or isinstance(point.get("value"), bool):
                    discarded_samples += 1
                    continue
                try:
                    value = float(point["value"])
                    timestamp = datetime.fromisoformat(str(point["timestamp"]).replace("Z", "+00:00"))
                except (KeyError, TypeError, ValueError, OverflowError):
                    discarded_samples += 1
                    continue
                if not isfinite(value) or timestamp.tzinfo is None:
                    discarded_samples += 1
                    continue
                timestamp = timestamp.astimezone(UTC)
                if not query_start <= timestamp <= end or timestamp in conflicting_times:
                    discarded_samples += 1
                    continue
                previous = series_by_time.get(timestamp)
                if previous is not None:
                    discarded_samples += 1
                    if previous["value"] != value:
                        del series_by_time[timestamp]
                        conflicting_times.add(timestamp)
                        discarded_samples += 1
                    continue
                series_by_time[timestamp] = {"timestamp": timestamp.isoformat(), "value": value}
            series = [series_by_time[timestamp] for timestamp in sorted(series_by_time)]
            unit = output.get("unit")
            observation = (
                f"{metric} peaked at {max(point['value'] for point in series):.3g} "
                f"{unit or ''} in the bounded query window"
                if series
                else f"No valid samples returned for {metric} in the bounded query window"
            )
            evidence.append(
                Evidence(
                    kind=EvidenceKind.METRIC,
                    source=str(output.get("source", "mcp:query_metrics")),
                    title=f"{metric} samples" if series else f"{metric} unavailable",
                    observation=observation.strip(),
                    observed_at=datetime.fromisoformat(series[-1]["timestamp"]) if series else end,
                    service=service,
                    score=0.95 - index * 0.04 if series else 0.0,
                    query=metric,
                    attributes={
                        "series": series,
                        "unit": unit,
                        "data_origin": output.get("data_origin", "unknown"),
                        "discarded_samples": discarded_samples,
                        "query_start": query_start.isoformat(),
                        "query_end": end.isoformat(),
                    },
                )
            )
        return evidence

    async def query_recovery_metrics(self, service: str, *, start: datetime, end: datetime) -> list[Evidence]:
        return await self.query_metrics(service, ("request_latency_p95", "request_error_rate"), start=start, end=end)

    async def search_logs(self, service: str, query: str) -> list[Evidence]:
        end = datetime.now(UTC)
        start = end - timedelta(minutes=30)
        output = await self._call_tool(
            "search_logs",
            {
                "service": service,
                "query": query,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": 200,
            },
        )
        raw_events = output.get("events", [])
        if not isinstance(raw_events, list):
            raise McpToolError("Invalid log event container returned.")
        events = []
        seen = set()
        discarded = 0
        for raw in raw_events:
            if not isinstance(raw, dict) or not isinstance(raw.get("message"), str) or not raw["message"].strip():
                discarded += 1
                continue
            try:
                timestamp = datetime.fromisoformat(str(raw["timestamp"]).replace("Z", "+00:00"))
            except (KeyError, ValueError, TypeError):
                discarded += 1
                continue
            if timestamp.tzinfo is None or not start <= timestamp <= end:
                discarded += 1
                continue
            event = {key: raw[key] for key in ("message", "level", "trace_id", "span_id", "request_id", "pool") if isinstance(raw.get(key), str)}
            event["timestamp"] = timestamp.astimezone(UTC).isoformat()
            for key in ("active", "max"):
                value = raw.get(key)
                if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    try:
                        event[key] = int(value)
                    except (ValueError, OverflowError):
                        pass
            identity = (event["timestamp"], event["message"], event.get("trace_id"), event.get("span_id"))
            if identity in seen:
                discarded += 1
                continue
            seen.add(identity)
            events.append(event)
        events.sort(key=lambda event: event["timestamp"])
        pool_events = [
            event
            for event in events
            if event.get("max", 0) > 0 and event.get("active", 0) >= event["max"]
        ]
        query_label = query or "service events"
        observation = f"{len(events)} valid retained events in the query window"
        if pool_events:
            observation += f"; {len(pool_events)} sampled events show a saturated pool"
        return [
            Evidence(
                kind=EvidenceKind.LOG,
                source=str(output.get("source", "mcp:search_logs")),
                title=f"Log matches for {query_label}",
                observation=observation,
                observed_at=datetime.fromisoformat(events[-1]["timestamp"]) if events else end,
                service=service,
                score=0.94 if events else 0.0,
                query=query_label,
                attributes={"events": events, "discarded_events": discarded,
                            "query_start": start.isoformat(), "query_end": end.isoformat()},
            )
        ]

    async def query_traces(self, service: str) -> list[Evidence]:
        end = datetime.now(UTC)
        start = end - timedelta(minutes=30)
        output = await self._call_tool(
            "query_traces",
            {
                "service": service,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": 20,
            },
        )
        traces = output.get("traces", [])
        return [
            Evidence(
                kind=EvidenceKind.TRACE,
                source=str(output.get("source", "mcp:query_traces")),
                title=f"Distributed traces for {service}",
                observation=(
                    f"{len(traces)} traces retained; longest sampled trace was "
                    f"{max((float(item.get('duration_ms', 0)) for item in traces), default=0):.0f} ms"
                ),
                observed_at=end,
                service=service,
                score=0.93 if traces else 0.2,
                query="service trace search",
                attributes={"traces": traces},
            )
        ]

    async def get_service_dependencies(self, service: str) -> list[str]:
        return (await self.get_service_topology(service)).dependencies

    async def get_service_topology(self, service: str) -> ServiceTopology:
        output = await self._call_tool(
            "get_service_dependencies",
            {"service": service},
        )
        try:
            return parse_topology(service, output)
        except ValueError as error:
            raise McpToolError("Invalid dependency topology returned.") from error

    async def search_incident_knowledge(
        self, service: str, query: str
    ) -> list[Evidence]:
        output = await self._call_tool(
            "search_incident_knowledge",
            {
                "service": service,
                "query": query,
                "content_types": [
                    "runbook",
                    "past_incident",
                    "technical_doc",
                ],
                "limit": 8,
            },
        )
        now = datetime.now(UTC)
        kinds = {
            "runbook": EvidenceKind.RUNBOOK,
            "past_incident": EvidenceKind.INCIDENT,
            "technical_doc": EvidenceKind.TECHNICAL_DOC,
        }
        evidence: list[Evidence] = []
        for item in output.get("results", []):
            document_type = str(item.get("document_type", "runbook"))
            updated_at = item.get("updated_at")
            observed_at = now
            if isinstance(updated_at, str):
                observed_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            evidence.append(
                Evidence(
                    kind=kinds.get(document_type, EvidenceKind.RUNBOOK),
                    source=str(item.get("uri", "mcp:search_incident_knowledge")),
                    title=str(item["title"]),
                    observation=str(
                        item.get("excerpt", "Matched operational guidance.")
                    ),
                    observed_at=observed_at,
                    service=service,
                    score=float(item.get("score", 0)),
                    attributes={
                        "section": item.get("section"),
                        "document_type": document_type,
                        "updated_at": updated_at,
                        "content_hash": item.get("content_hash"),
                        "matched_terms": item.get("matched_terms", []),
                    },
                )
            )
        return evidence

    async def get_recent_deployments(self, service: str, namespace: str = "prod-sg") -> list[Evidence]:
        output = await self._call_tool(
            "get_recent_deployments",
            {"service": service, "namespace": namespace, "limit": 10},
        )
        evidence: list[Evidence] = []
        for deployment in output.get("deployments", []):
            deployed_at = datetime.fromisoformat(
                str(deployment["deployed_at"]).replace("Z", "+00:00")
            )
            attributes = {**deployment, "data_origin": output.get("data_origin", "unknown")}
            if "event_timeline" in attributes:
                raw_timeline = output.get("event_timeline")
                status = raw_timeline.get("status") if isinstance(raw_timeline, dict) else "unavailable"
                attributes["event_timeline"] = {"status": status if status in {"available", "empty", "unavailable", "not_requested"} else "unavailable",
                    "returned_valid_events_only": True,
                    "retention_warning": "Events may expire; absence is not proof that no deployment occurred."}
            evidence.append(
                Evidence(
                    kind=EvidenceKind.DEPLOYMENT,
                    source=str(output.get("source", "mcp:get_recent_deployments")),
                    title=f"Release {deployment['revision']}",
                    observation=str(deployment.get("summary", "Deployment observed.")),
                    observed_at=deployed_at,
                    service=service,
                    score=0.9,
                    attributes=attributes,
                )
            )
        evidence.extend(self._deployment_events(output, evidence, service, namespace))
        return evidence

    @staticmethod
    def _deployment_events(output, snapshots, service, namespace):
        timeline = output.get("event_timeline", {})
        if (output.get("data_origin") != "kubernetes_api" or not isinstance(timeline, dict)
                or timeline.get("status") != "available"):
            return []
        records = timeline.get("events")
        if not isinstance(records, list) or len(records) > 100:
            return []
        uids = {item.attributes.get("deployment_uid") for item in snapshots
                if item.kind == EvidenceKind.DEPLOYMENT and item.attributes.get("namespace") == namespace
                and isinstance(item.attributes.get("deployment_uid"), str)}
        retained = {}
        for event in records:
            try:
                if (not isinstance(event, dict) or event.get("namespace") != namespace or event.get("service") != service
                        or event.get("deployment_uid") not in uids or event.get("data_origin") != "kubernetes_api"
                        or event.get("timestamp_semantics") != "event_observation_not_rollout_time"
                        or event.get("reason") not in {"ScalingReplicaSet", "NewReplicaSetCreated", "ProgressDeadlineExceeded",
                            "FailedCreate", "ReplicaSetUpdated", "DeploymentRollback"}
                        or not isinstance(event.get("event_uid"), str) or re.fullmatch(r"[A-Za-z0-9-]{1,128}", event["event_uid"]) is None):
                    continue
                times = {}
                for key in ("first_observed_at", "last_observed_at"):
                    value = event.get(key)
                    if value is None:
                        times[key] = None
                        continue
                    if not isinstance(value, str):
                        raise ValueError()
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None or parsed.year < 1970 or parsed > datetime.now(UTC) + timedelta(seconds=30):
                        raise ValueError()
                    times[key] = parsed.astimezone(UTC)
                first, last = times.values()
                if first is None and last is None or first and last and first > last:
                    continue
                count = event.get("count")
                if count is not None and (not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10**9):
                    continue
                normalized = {key: event[key] for key in ("event_uid", "deployment_uid", "service", "namespace", "reason", "data_origin", "timestamp_semantics")}
                normalized.update({key: value.isoformat() if value else None for key, value in times.items()})
                normalized["count"] = count
                normalized["type"] = event.get("type") if event.get("type") in {"Normal", "Warning"} else "unknown"
                identity = event["event_uid"]
                if identity in retained and retained[identity].attributes != normalized:
                    return []
                retained[identity] = Evidence(kind=EvidenceKind.DEPLOYMENT_EVENT,
                    source="kubernetes-api-deployment-events", title=f"Deployment event: {normalized['reason']}",
                    observation=(f"{normalized['reason']} first observed {normalized['first_observed_at'] or 'unknown'}, "
                        f"last observed {normalized['last_observed_at'] or 'unknown'}. Event timing is context, not proof of rollout time or causation."),
                    observed_at=last or first, service=service, score=0.6,
                    query="get_recent_deployments:events", attributes=normalized)
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        return list(retained.values())

    async def rollback_release(
        self,
        service: str,
        revision: str,
        idempotency_key: str,
        dry_run: bool,
        namespace: str = "prod-sg",
        approval_receipt: str | None = None,
        target_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._call_tool(
            "rollback_release",
            {
                "service": service,
                "namespace": namespace,
                "revision": revision,
                "dry_run": dry_run,
                "approval_receipt": approval_receipt,
                "idempotency_key": idempotency_key,
                **({"target_context": target_context} if target_context is not None else {}),
            },
            operator=not dry_run,
        )

    async def restart_deployment(
        self,
        service: str,
        idempotency_key: str,
        dry_run: bool,
        namespace: str = "prod-sg",
        approval_receipt: str | None = None,
        target_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._call_tool(
            "restart_deployment",
            {
                "service": service,
                "namespace": namespace,
                "dry_run": dry_run,
                "approval_receipt": approval_receipt,
                "idempotency_key": idempotency_key,
                **({"target_context": target_context} if target_context is not None else {}),
            },
            operator=not dry_run,
        )

    async def query_operation_status(self, *, service: str, namespace: str,
                                     tool: str, revision: str | None,
                                     idempotency_key: str, approval_actor: str,
                                     incident_id: str, target_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._call_tool("query_operation_status", {
            "service": service, "namespace": namespace, "tool": tool,
            "revision": revision, "idempotency_key": idempotency_key,
            "approval_actor": approval_actor, "incident_id": incident_id,
            **({"target_context": target_context} if target_context is not None else {}),
        }, operator=True)

    async def query_deployment_health(self, *, service: str, namespace: str,
                                     tool: str, revision: str | None,
                                     idempotency_key: str, approval_actor: str,
                                     incident_id: str, target_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._call_tool("query_deployment_health", {
            "service": service, "namespace": namespace, "tool": tool,
            "revision": revision, "idempotency_key": idempotency_key,
            "approval_actor": approval_actor, "incident_id": incident_id,
            **({"target_context": target_context} if target_context is not None else {}),
        }, operator=True)

    async def _call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        operator: bool = False,
    ) -> dict[str, Any]:
        token = self.operator_token if operator else self.read_token
        await self._initialize(token)
        response = await self._request(
            token,
            {
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        if "error" in response:
            raise McpToolError(f"{name} failed at the MCP boundary.")
        result = response.get("result", {})
        if result.get("isError"):
            raise McpToolError(f"{name} returned an error result")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        for item in result.get("content", []):
            if item.get("type") == "text":
                parsed = json.loads(item["text"])
                if isinstance(parsed, dict):
                    return parsed
        raise McpToolError(f"{name} did not return structured content")

    async def _initialize(self, token: str) -> None:
        if token in self._initialized_tokens:
            return
        response = await self._request(
            token,
            {
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "incident-copilot-orchestrator",
                        "version": "0.2.0",
                    },
                },
            },
        )
        if "error" in response:
            raise McpToolError("MCP initialization failed.")
        self._initialized_tokens.add(token)

    async def _request(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
            "Origin": "http://localhost:3000",
        }
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._client.post(
                    self.url, json=payload, headers=headers
                )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise McpToolError("MCP response must be a JSON object")
                return body
            except (httpx.TimeoutException, httpx.TransportError) as error:
                last_error = error
                if attempt == self.max_attempts:
                    break
                await asyncio.sleep(0.05 * (2 ** (attempt - 1)))
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500 or attempt == self.max_attempts:
                    raise McpToolError(
                        f"MCP HTTP failure: {error.response.status_code}"
                    ) from error
                last_error = error
                await asyncio.sleep(0.05 * (2 ** (attempt - 1)))
        raise McpToolError("MCP request exhausted retry budget") from last_error
