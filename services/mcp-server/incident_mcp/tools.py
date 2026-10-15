from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Generic, TypeVar

from pydantic import BaseModel

from .gateway import TelemetryGatewayClient, TelemetryGatewayError
from .knowledge import KnowledgeIndex
from .kubernetes import KubernetesRemediator
from .schemas import (
    QueryMetricsArguments,
    RecentDeploymentsArguments,
    RestartDeploymentArguments,
    RollbackReleaseArguments,
    SearchKnowledgeArguments,
    SearchLogsArguments,
    SearchTracesArguments,
    SearchRunbooksArguments,
    ServiceArguments,
    OperationStatusArguments,
)
from .security import ApprovalReceiptVerifier, AuthorizationError, Principal
from .target_context import normalize_target_context, target_context_digest

Arguments = TypeVar("Arguments", bound=BaseModel)
Handler = Callable[[Arguments, Principal], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool(Generic[Arguments]):
    name: str
    title: str
    description: str
    arguments: type[Arguments]
    handler: Handler[Arguments]
    read_only: bool

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.arguments.model_json_schema(),
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": not self.read_only,
                "idempotentHint": self.read_only,
                "openWorldHint": True,
            },
        }


class ToolRegistry:
    def __init__(
        self,
        approval_verifier: ApprovalReceiptVerifier,
        gateway: TelemetryGatewayClient | None = None,
        remediator: KubernetesRemediator | None = None,
        knowledge_index: KnowledgeIndex | None = None,
        deployment_reader=None,
    ) -> None:
        self.approval_verifier = approval_verifier
        self.gateway = gateway
        self.deployment_reader = deployment_reader
        self.remediator = remediator
        self.knowledge_index = knowledge_index or KnowledgeIndex.load_default()
        self._executions: dict[str, dict[str, Any]] = {}
        self._execution_bindings: dict[str, dict[str, Any]] = {}
        self._execution_lock = asyncio.Lock()
        self.tools: dict[str, Tool] = {}
        self._register_tools()

    def list_definitions(self) -> list[dict[str, Any]]:
        return [tool.definition() for tool in self.tools.values()]

    async def call(
        self, name: str, raw_arguments: dict[str, Any], principal: Principal
    ) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")
        arguments = tool.arguments.model_validate(raw_arguments)
        return await asyncio.wait_for(
            tool.handler(arguments, principal),
            timeout=8,
        )

    def _register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def _register_tools(self) -> None:
        self._register(Tool(
            name="query_operation_status", title="Inspect remediation operation marker",
            description="Operator-scoped, read-only lookup of a bound operation marker. Never dispatches or retries remediation; not rollout-health proof.",
            arguments=OperationStatusArguments, handler=self._query_operation_status, read_only=True,
        ))
        self._register(Tool(
            name="query_deployment_health", title="Inspect bound Deployment rollout snapshot",
            description="Operator-scoped fresh read of Deployment identity, operation marker, generation and replica status. Does not execute remediation or independently prove service recovery.",
            arguments=OperationStatusArguments, handler=self._query_deployment_health, read_only=True,
        ))
        self._register(
            Tool(
                name="query_metrics",
                title="Query service metrics",
                description="Read an allow-listed metric series for one service and bounded time window.",
                arguments=QueryMetricsArguments,
                handler=self._query_metrics,
                read_only=True,
            )
        )
        self._register(
            Tool(
                name="search_logs",
                title="Search service logs",
                description="Search sanitized service logs with a bounded result limit and time window.",
                arguments=SearchLogsArguments,
                handler=self._search_logs,
                read_only=True,
            )
        )
        self._register(
            Tool(
                name="query_traces",
                title="Query distributed traces",
                description=(
                    "Search bounded Tempo traces for one service and time window."
                ),
                arguments=SearchTracesArguments,
                handler=self._query_traces,
                read_only=True,
            )
        )
        self._register(
            Tool(
                name="get_service_dependencies",
                title="Get service dependencies",
                description="Return catalog edges and separately labeled sampled Tempo call parentage; neither proves incident causation.",
                arguments=ServiceArguments,
                handler=self._get_dependencies,
                read_only=True,
            )
        )
        self._register(
            Tool(
                name="search_runbooks",
                title="Search runbooks",
                description="Retrieve operational guidance with stable source citations.",
                arguments=SearchRunbooksArguments,
                handler=self._search_runbooks,
                read_only=True,
            )
        )
        self._register(
            Tool(
                name="search_incident_knowledge",
                title="Search incident knowledge",
                description="Search versioned runbooks, past incidents, and technical documents with stable citations and content hashes.",
                arguments=SearchKnowledgeArguments,
                handler=self._search_incident_knowledge,
                read_only=True,
            )
        )
        self._register(
            Tool(
                name="get_recent_deployments",
                title="Get recent deployments",
                description="Return recent Kubernetes releases for one service.",
                arguments=RecentDeploymentsArguments,
                handler=self._get_recent_deployments,
                read_only=True,
            )
        )
        self._register(
            Tool(
                name="restart_deployment",
                title="Restart deployment",
                description="Dry-run or restart a Kubernetes deployment. Execution requires operator scope and a signed human approval receipt.",
                arguments=RestartDeploymentArguments,
                handler=self._restart_deployment,
                read_only=False,
            )
        )
        self._register(
            Tool(
                name="rollback_release",
                title="Rollback release",
                description="Dry-run or roll back a release. Execution requires operator scope and a signed human approval receipt.",
                arguments=RollbackReleaseArguments,
                handler=self._rollback_release,
                read_only=False,
            )
        )

    async def _query_metrics(
        self, arguments: QueryMetricsArguments, principal: Principal
    ) -> dict[str, Any]:
        principal.require("telemetry:read")
        if arguments.end <= arguments.start:
            raise ValueError("end must be after start")
        if arguments.end - arguments.start > timedelta(hours=24):
            raise ValueError("metric query window cannot exceed 24 hours")
        if self.gateway is not None:
            result = await self.gateway.query_metrics(
                service=arguments.service,
                metric=arguments.metric,
                start=arguments.start,
                end=arguments.end,
                step_seconds=arguments.step_seconds,
            )
            if result.get("service") != arguments.service or result.get("metric") != arguments.metric:
                raise ValueError("Metric gateway response scope does not match the request.")
            return {
                "service": result["service"],
                "metric": result["metric"],
                "series": result.get("points", []),
                "unit": result.get("unit"),
                "source": "prometheus-via-telemetry-gateway",
                "data_origin": result.get("data_origin", "unknown"),
            }
        baseline, anomalous, unit = {
            "request_latency_p95": (643.0, 2840.0, "milliseconds"),
            "request_error_rate": (0.01, 0.01, "ratio"),
            "db_pool_active_ratio": (
                0.46,
                1.0 if arguments.service == "payment-service" else 0.46,
                "ratio",
            ),
            "cpu_utilization": (0.38, 0.38, "ratio"),
            "memory_working_set": (
                64 * 1024 * 1024,
                64 * 1024 * 1024,
                "bytes",
            ),
            "kafka_consumer_lag": (0.0, 0.0, "messages"),
            "retry_rate": (0.0, 0.0, "retries_per_second"),
            "cache_miss_rate": (0.0, 0.0, "misses_per_second"),
        }[arguments.metric]
        values = [baseline] * 6 + [anomalous] * 3
        return {
            "service": arguments.service,
            "metric": arguments.metric,
            "series": [
                {
                    "timestamp": (
                        arguments.start + timedelta(minutes=index * 2)
                    ).isoformat(),
                    "value": value,
                }
                for index, value in enumerate(values)
            ],
            "unit": unit,
            "source": "prometheus",
            "data_origin": "synthetic",
        }

    async def _search_logs(
        self, arguments: SearchLogsArguments, principal: Principal
    ) -> dict[str, Any]:
        principal.require("telemetry:read")
        if arguments.end <= arguments.start:
            raise ValueError("end must be after start")
        if self.gateway is not None:
            result = await self.gateway.search_logs(
                service=arguments.service,
                query=arguments.query,
                start=arguments.start,
                end=arguments.end,
                limit=arguments.limit,
            )
            normalized_events = []
            for event in result.get("events", []):
                fields = event.get("fields", {})
                normalized_events.append(
                    {
                        "timestamp": event.get("timestamp"),
                        "level": event.get("level"),
                        "message": event.get("message"),
                        "pool": fields.get("pool"),
                        "active": int(fields.get("active", 0)),
                        "max": int(fields.get("max", 0)),
                        "trace_id": fields.get("trace_id"),
                        "span_id": fields.get("span_id"),
                        "request_id": fields.get("request_id"),
                    }
                )
            return {
                "service": arguments.service,
                "matches": result.get("count", len(normalized_events)),
                "events": normalized_events,
                "source": "loki-via-telemetry-gateway",
            }
        message = arguments.query or "request completed"
        is_pool_timeout = "acquisition timed out" in message
        return {
            "service": arguments.service,
            "matches": min(arguments.limit, 184),
            "events": [
                {
                    "timestamp": (
                        arguments.start + timedelta(seconds=index * 3)
                    ).isoformat(),
                    "level": "ERROR",
                    "message": message,
                    "pool": "ledger-primary" if is_pool_timeout else None,
                    "active": 50 if is_pool_timeout else 0,
                    "max": 50 if is_pool_timeout else 0,
                }
                for index in range(min(arguments.limit, 10))
            ],
            "source": "loki",
        }

    async def _query_traces(
        self,
        arguments: SearchTracesArguments,
        principal: Principal,
    ) -> dict[str, Any]:
        principal.require("telemetry:read")
        if arguments.end <= arguments.start:
            raise ValueError("end must be after start")
        if arguments.end - arguments.start > timedelta(hours=24):
            raise ValueError("trace query window cannot exceed 24 hours")
        if self.gateway is not None:
            result = await self.gateway.search_traces(
                service=arguments.service,
                start=arguments.start,
                end=arguments.end,
                limit=arguments.limit,
            )
            return {
                "service": arguments.service,
                "traces": result.get("traces", []),
                "count": result.get("count", 0),
                "source": "tempo-via-telemetry-gateway",
            }
        traces = [
            {
                "trace_id": f"trace-{arguments.service}-{index:03d}",
                "root_service_name": "checkout-api",
                "root_trace_name": "POST /v1/checkout",
                "started_at": (
                    arguments.start + timedelta(minutes=index)
                ).isoformat(),
                "duration_ms": 2840 + index * 120,
            }
            for index in range(min(arguments.limit, 3))
        ]
        return {
            "service": arguments.service,
            "traces": traces,
            "count": len(traces),
            "source": "tempo",
        }

    async def _get_dependencies(
        self, arguments: ServiceArguments, principal: Principal
    ) -> dict[str, Any]:
        principal.require("telemetry:read")
        if self.gateway is not None:
            async def catalog():
                try:
                    data = await asyncio.wait_for(self.gateway.dependencies(arguments.service), timeout=6)
                except (TelemetryGatewayError, TimeoutError):
                    data = None
                if not isinstance(data, dict) or data.get("service") != arguments.service or not isinstance(data.get("edges"), list) or len(data["edges"]) > 64:
                    return {"service": arguments.service, "edges": [], "catalog_status": "unavailable"}
                return {**data, "catalog_status": "available"}

            async def observe():
                reader = getattr(self.gateway, "observed_dependencies", None)
                if not callable(reader):
                    return {"status": "not_supported"}
                try:
                    data = await asyncio.wait_for(reader(arguments.service), timeout=6)
                except (TelemetryGatewayError, TimeoutError):
                    return {"status": "unavailable"}
                if not isinstance(data, dict) or data.get("service") != arguments.service:
                    return {"status": "unavailable"}
                calls = data.get("calls")
                if not isinstance(calls, list) or len(calls) > 64 or any(not isinstance(call, dict) for call in calls):
                    return {"status": "unavailable"}
                projected = {key: data.get(key) for key in (
                    "service", "source", "start", "end", "coverage", "traces_read", "traces_failed", "calls_omitted")}
                projected["calls"] = [{key: call.get(key) for key in (
                    "source", "target", "trace_id", "client_span_id", "server_span_id", "observed_at")}
                    for call in calls if isinstance(call, dict)]
                return {"status": "available", "data": projected}

            result, observed = await asyncio.gather(catalog(), observe())
            return {
                "service": result["service"],
                "edges": result.get("edges", []),
                "source": "service-catalog-via-telemetry-gateway",
                "topology_kind": "static_fixture_catalog" if result.get("topology_kind") == "static_fixture_catalog" else "unknown",
                "observed": observed,
                "catalog_status": result["catalog_status"],
            }
        return {
            "service": arguments.service,
            "edges": [
                {"source": "checkout-api", "target": arguments.service},
                {"source": arguments.service, "target": "ledger-db"},
                {"source": arguments.service, "target": "risk-engine"},
            ],
            "source": "service-catalog",
            "topology_kind": "static_fixture_catalog",
            "observed": {"status": "not_supported"},
            "catalog_status": "available",
        }

    async def _search_runbooks(
        self, arguments: SearchRunbooksArguments, principal: Principal
    ) -> dict[str, Any]:
        principal.require("runbook:read")
        results = self.knowledge_index.search(
            service=arguments.service,
            query=arguments.query,
            content_types={"runbook"},
            limit=arguments.limit,
        )
        return {
            "results": results,
            "source": "runbook-index",
        }

    async def _search_incident_knowledge(
        self,
        arguments: SearchKnowledgeArguments,
        principal: Principal,
    ) -> dict[str, Any]:
        principal.require("runbook:read")
        results = self.knowledge_index.search(
            service=arguments.service,
            query=arguments.query,
            content_types=set(arguments.content_types),
            limit=arguments.limit,
        )
        return {
            "results": results,
            "source": "incident-knowledge-index",
            "corpus": "checked-in-synthetic-fault-lab-v1",
        }

    async def _get_recent_deployments(
        self, arguments: RecentDeploymentsArguments, principal: Principal
    ) -> dict[str, Any]:
        principal.require("telemetry:read")
        if self.deployment_reader is not None:
            return await self.deployment_reader.recent_deployments(service=arguments.service,
                namespace=arguments.namespace, limit=arguments.limit)
        if self.gateway is not None:
            result = await self.gateway.deployments(arguments.service)
            return {
                "service": result["service"],
                "deployments": result.get("deployments", [])[: arguments.limit],
                # The gateway deployment endpoint is a fixture catalog even
                # when its metrics/logs/traces use real observability stores.
                "source": "synthetic-deployment-catalog-via-telemetry-gateway",
                "data_origin": "synthetic",
            }
        return {
            "service": arguments.service,
            "deployments": [
                {
                    "revision": "142",
                    "previous_revision": "141",
                    "deployed_at": (
                        datetime.now(UTC) - timedelta(minutes=29)
                    ).isoformat(),
                    "summary": "Parallelize ledger balance lookups",
                }
            ],
            "source": "synthetic-deployment-catalog",
            "data_origin": "synthetic",
        }

    async def _restart_deployment(
        self, arguments: RestartDeploymentArguments, principal: Principal
    ) -> dict[str, Any]:
        target = arguments.namespace
        return await self._execute_guarded(
            arguments=arguments,
            principal=principal,
            tool="restart_deployment",
            target=target,
            result={
                "service": arguments.service,
                "namespace": arguments.namespace,
                "action": "restart",
            },
        )

    async def _rollback_release(
        self, arguments: RollbackReleaseArguments, principal: Principal
    ) -> dict[str, Any]:
        return await self._execute_guarded(
            arguments=arguments,
            principal=principal,
            tool="rollback_release",
            target=arguments.revision,
            result={
                "service": arguments.service,
                "namespace": arguments.namespace,
                "revision": arguments.revision,
                "action": "rollback",
            },
        )

    @staticmethod
    def _execution_binding(arguments, principal, tool, actor, incident_id):
        return {"tool": tool, "arguments": arguments.model_dump(exclude={"approval_receipt", "idempotency_key"}),
                "principal": principal.subject, "actor": actor, "incident_id": incident_id}

    @staticmethod
    def _target_context(arguments, tool: str, *, required: bool) -> dict[str, Any] | None:
        if arguments.target_context is None:
            if required:
                raise AuthorizationError("A verified Kubernetes target context is required for live operations.")
            return None
        context = normalize_target_context(arguments.target_context)
        expected = {"tool": tool, "service": arguments.service, "namespace": arguments.namespace,
                    "revision": getattr(arguments, "revision", None)}
        for field, value in expected.items():
            if context.get(field) != value:
                raise AuthorizationError(f"Kubernetes target context does not match requested {field}.")
        return context

    def _status_binding(self, arguments: OperationStatusArguments, principal: Principal):
        principal.require("production:execute")
        raw = {"service": arguments.service, "namespace": arguments.namespace,
               "idempotency_key": arguments.idempotency_key, "dry_run": False,
               "target_context": arguments.target_context}
        schema = RestartDeploymentArguments
        if arguments.tool == "rollback_release":
            raw["revision"] = arguments.revision
            schema = RollbackReleaseArguments
        operation = schema.model_validate(raw)
        context = self._target_context(operation, arguments.tool, required=self.remediator is not None)
        if self.remediator is None and context is not None:
            raise AuthorizationError("A simulation backend cannot verify a Kubernetes target context.")
        binding = self._execution_binding(operation, principal, arguments.tool, arguments.approval_actor, arguments.incident_id)
        return context, binding

    async def _query_operation_status(self, arguments: OperationStatusArguments, principal: Principal) -> dict[str, Any]:
        context, binding = self._status_binding(arguments, principal)
        if self.remediator is not None:
            digest = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            return await self.remediator.query_operation_status(
                service=arguments.service, namespace=arguments.namespace, tool=arguments.tool,
                revision=arguments.revision, idempotency_key=arguments.idempotency_key,
                operation_binding=digest, target_context=context)
        cached = self._executions.get(arguments.idempotency_key)
        status = "unknown"
        if cached is not None:
            status = "recorded_response" if self._execution_bindings.get(arguments.idempotency_key) == binding else "conflicting_record"
        return {"status": status, "source": "process_cache", "durable": False, "read_only": True,
                "rollout_health_verified": False}

    async def _query_deployment_health(self, arguments: OperationStatusArguments, principal: Principal) -> dict[str, Any]:
        context, binding = self._status_binding(arguments, principal)
        if self.remediator is not None:
            digest = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            return await self.remediator.query_deployment_health(
                service=arguments.service, namespace=arguments.namespace, tool=arguments.tool,
                revision=arguments.revision, idempotency_key=arguments.idempotency_key,
                operation_binding=digest, target_context=context)
        return {
            "source": "simulation", "read_only": True, "marker_status": "unknown",
            "observed_at": datetime.now(UTC).isoformat(),
            "service": arguments.service, "namespace": arguments.namespace,
            "cluster_context": None, "deployment_uid": None, "generation": None,
            "template_sha256": None, "observed_generation": None,
            "desired_replicas": None, "total_replicas": None, "updated_replicas": None, "ready_replicas": None,
            "available_replicas": None, "unavailable_replicas": None,
        }

    async def _execute_guarded(
        self,
        *,
        arguments: RestartDeploymentArguments,
        principal: Principal,
        tool: str,
        target: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        receipt = None
        if arguments.dry_run:
            principal.require("telemetry:read")
            context = self._target_context(arguments, tool, required=False)
        else:
            principal.require("production:execute")
            context = self._target_context(arguments, tool, required=self.remediator is not None)
            receipt = self.approval_verifier.verify(
                arguments.approval_receipt, tool=tool,
                service=arguments.service, namespace=arguments.namespace, target=target,
                target_context_sha256=target_context_digest(context) if context is not None else None,
            )
        if self.remediator is None and context is not None:
            raise AuthorizationError("A simulation backend cannot verify a Kubernetes target context.")
        binding = self._execution_binding(arguments, principal, tool,
                                          receipt["actor"] if receipt else None,
                                          receipt["incident_id"] if receipt else None)
        async with self._execution_lock:
            if arguments.idempotency_key in self._execution_bindings:
                if self._execution_bindings.get(arguments.idempotency_key) != binding:
                    raise ValueError("Idempotency key is bound to another operation or approval.")
                # A cached response cannot prove the target still exists in the
                # same cluster. Live retries rely on the adapter's GET-validated
                # durable marker, and every dry-run contacts its backend anew.
                if not arguments.dry_run and self.remediator is None:
                    return self._executions[arguments.idempotency_key]

            if arguments.dry_run:
                preflight: dict[str, Any] = {"preflight_mode": "simulation", "target_context": None,
                                             "backend": "simulation", "dry_run": True}
                if self.remediator is not None:
                    preflight = await self.remediator.preflight(
                        tool=tool, service=arguments.service, namespace=arguments.namespace,
                        revision=getattr(arguments, "revision", None),
                        idempotency_key=arguments.idempotency_key,
                    )
                    if (not isinstance(preflight, dict)
                            or preflight.get("preflight_mode") != "kubernetes-server-dry-run"
                            or preflight.get("backend") != "kubernetes"
                            or preflight.get("accepted") is not True
                            or preflight.get("executed") is not False
                            or preflight.get("dry_run") is not True
                            or preflight.get("target_context") is None):
                        raise AuthorizationError("Kubernetes preflight did not return a verified target context.")
                    verified = arguments.model_copy(update={"target_context": preflight["target_context"]})
                    preflight = {**preflight, "target_context": self._target_context(verified, tool, required=True)}
                response = {
                    **result,
                    **preflight,
                    "accepted": True,
                    "mode": "dry-run",
                    "executed": False,
                }
                self._executions[arguments.idempotency_key] = response
                self._execution_bindings[arguments.idempotency_key] = binding
                return response

            assert receipt is not None
            backend_result: dict[str, Any] = {}
            execution_backend = "simulation"
            if self.remediator is not None:
                execution_backend = "kubernetes"
                operation_binding = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if tool == "restart_deployment":
                    backend_result = await self.remediator.restart_deployment(
                        service=arguments.service,
                        namespace=arguments.namespace,
                        idempotency_key=arguments.idempotency_key,
                        operation_binding=operation_binding,
                        target_context=context,
                    )
                else:
                    revision = getattr(arguments, "revision")
                    backend_result = await self.remediator.rollback_release(
                        service=arguments.service,
                        namespace=arguments.namespace,
                        revision=revision,
                        idempotency_key=arguments.idempotency_key,
                        operation_binding=operation_binding,
                        target_context=context,
                    )
            response = {
                **result,
                **backend_result,
                "accepted": True,
                "mode": "execute",
                "executed": True,
                "execution_backend": execution_backend,
                "approved_by": receipt["actor"],
                "incident_id": receipt["incident_id"],
            }
            self._executions[arguments.idempotency_key] = response
            self._execution_bindings[arguments.idempotency_key] = binding
            return response
