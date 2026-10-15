from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .models import Evidence, EvidenceKind


class TelemetryTools(Protocol):
    async def query_recovery_metrics(self, service: str, *, start: datetime, end: datetime) -> list[Evidence]:
        ...

    async def query_deployment_health(self, *, service: str, namespace: str,
                                     tool: str, revision: str | None,
                                     idempotency_key: str, approval_actor: str,
                                     incident_id: str, target_context: dict | None = None) -> dict:
        ...

    async def query_operation_status(self, *, service: str, namespace: str,
                                     tool: str, revision: str | None,
                                     idempotency_key: str, approval_actor: str,
                                     incident_id: str, target_context: dict | None = None) -> dict:
        ...

    async def query_metrics(
        self, service: str, metrics: tuple[str, ...]
    ) -> list[Evidence]:
        ...

    async def search_logs(self, service: str, query: str) -> list[Evidence]:
        ...

    async def query_traces(self, service: str) -> list[Evidence]:
        ...

    async def get_service_dependencies(self, service: str) -> list[str]:
        ...

    async def search_incident_knowledge(
        self, service: str, query: str
    ) -> list[Evidence]:
        ...

    async def get_recent_deployments(self, service: str, namespace: str = "prod-sg") -> list[Evidence]:
        ...

    async def rollback_release(
        self,
        service: str,
        revision: str,
        idempotency_key: str,
        dry_run: bool,
        namespace: str = "prod-sg",
        approval_receipt: str | None = None,
        target_context: dict | None = None,
    ) -> dict:
        ...

    async def restart_deployment(
        self,
        service: str,
        idempotency_key: str,
        dry_run: bool,
        namespace: str = "prod-sg",
        approval_receipt: str | None = None,
        target_context: dict | None = None,
    ) -> dict:
        ...


class DemoTelemetryTools:
    """Deterministic fault-lab adapter used by local demos and evaluation tests."""
    # This provider's mutation methods are simulation-only, never remote calls.
    allow_simulated_deployment_targets = True
    allow_simulated_action_summaries = True
    allow_simulated_signal_summaries = True

    def __init__(self) -> None:
        self._executions: dict[str, dict[str, str | bool]] = {}

    async def query_deployment_health(self, **kwargs) -> dict:
        return {"source": "simulation", "read_only": True, "marker_status": "unknown"}

    async def query_recovery_metrics(self, service: str, *, start: datetime, end: datetime) -> list[Evidence]:
        return []

    async def query_operation_status(self, **kwargs) -> dict:
        # Demo execution cache is not bound to approval identity or durable.
        return {"status": "unknown", "source": "fixture", "read_only": True,
                "rollout_health_verified": False}

    async def query_metrics(
        self, service: str, metrics: tuple[str, ...]
    ) -> list[Evidence]:
        await asyncio.sleep(0)
        now = datetime.now(UTC)
        evidence = []
        for index, metric in enumerate(metrics):
            evidence.append(
                Evidence(
                    kind=EvidenceKind.METRIC,
                    source="prometheus",
                    title=f"{metric} anomaly",
                    observation=f"{metric} departed from baseline in the incident window",
                    observed_at=now - timedelta(minutes=18),
                    service=service,
                    score=0.96 - index * 0.04,
                    query=metric,
                    attributes={"metric": metric, "anomalous": True},
                )
            )
        return evidence

    async def search_logs(self, service: str, query: str) -> list[Evidence]:
        await asyncio.sleep(0)
        return [
            Evidence(
                kind=EvidenceKind.LOG,
                source="loki",
                title=f"Log matches for {query}",
                observation=f"Repeated '{query}' events align with the anomalous window",
                observed_at=datetime.now(UTC) - timedelta(minutes=18),
                service=service,
                score=0.94,
                query=query,
                attributes={"matching_events": 184},
            )
        ]

    async def query_traces(self, service: str) -> list[Evidence]:
        await asyncio.sleep(0)
        now = datetime.now(UTC)
        traces = [
            {
                "trace_id": f"trace-{service}-{index:03d}",
                "root_service_name": "checkout-api",
                "root_trace_name": "POST /v1/checkout",
                "started_at": (now - timedelta(minutes=18 - index)).isoformat(),
                "duration_ms": 2840 + index * 120,
            }
            for index in range(3)
        ]
        return [
            Evidence(
                kind=EvidenceKind.TRACE,
                source="tempo",
                title=f"Distributed traces for {service}",
                observation=(
                    "3 traces show latency propagation from checkout-api "
                    f"through {service}"
                ),
                observed_at=now - timedelta(minutes=18),
                service=service,
                score=0.93,
                query="service trace search",
                attributes={"traces": traces},
            )
        ]

    async def get_service_dependencies(self, service: str) -> list[str]:
        await asyncio.sleep(0)
        graph = {
            "payment-service": ["checkout-api", "ledger-db", "risk-engine"],
            "checkout-api": ["payment-service"],
            "ledger-service": ["payment-service"],
            "risk-engine": ["payment-service"],
        }
        return graph.get(service, [])

    async def search_incident_knowledge(
        self, service: str, query: str
    ) -> list[Evidence]:
        await asyncio.sleep(0)
        return [
            Evidence(
                kind=EvidenceKind.RUNBOOK,
                source="runbook://payments/db-pool-saturation",
                title="Payment DB pool saturation",
                observation="Rollback the latest query-shape change when saturation follows a deploy",
                observed_at=datetime.now(UTC),
                service=service,
                score=0.88,
                attributes={"section": "Mitigation", "revision": "2026-06-18"},
            ),
            Evidence(
                kind=EvidenceKind.INCIDENT,
                source="incident://INC-2026-041/payment-pool-exhaustion",
                title="Payment v142 ledger fan-out exhausted the connection pool",
                observation=(
                    "A prior v142 incident showed the same pool saturation and "
                    "acquisition timeout signature."
                ),
                observed_at=datetime.now(UTC) - timedelta(days=12),
                service=service,
                score=0.86,
                attributes={
                    "section": "Root cause and resolution",
                    "document_type": "past_incident",
                },
            ),
            Evidence(
                kind=EvidenceKind.TECHNICAL_DOC,
                source="doc://platform/database-pool-capacity",
                title="Database connection pool capacity model",
                observation=(
                    "Parallel fan-out increases concurrent connection demand "
                    "without requiring a traffic increase."
                ),
                observed_at=datetime.now(UTC) - timedelta(days=82),
                service=service,
                score=0.81,
                attributes={
                    "section": "Operational constraints",
                    "document_type": "technical_doc",
                },
            ),
        ]

    async def get_recent_deployments(self, service: str, namespace: str = "prod-sg") -> list[Evidence]:
        await asyncio.sleep(0)
        return [
            Evidence(
                kind=EvidenceKind.DEPLOYMENT,
                source="synthetic-deployment-catalog",
                title="Release payment-v142",
                observation="v142 introduced parallel ledger query fan-out 11 minutes before impact",
                observed_at=datetime.now(UTC) - timedelta(minutes=29),
                service=service,
                score=0.9,
                attributes={"revision": "142", "previous_revision": "141", "data_origin": "synthetic"},
            )
        ]

    async def rollback_release(
        self,
        service: str,
        revision: str,
        idempotency_key: str,
        dry_run: bool,
        namespace: str = "prod-sg",
        approval_receipt: str | None = None,
        target_context: dict | None = None,
    ) -> dict:
        if target_context is not None:
            raise ValueError("Simulation cannot verify a Kubernetes target context.")
        if idempotency_key in self._executions:
            return self._executions[idempotency_key]
        result: dict[str, str | bool] = {
            "accepted": True,
            "executed": not dry_run,
            "execution_backend": "simulation",
            "service": service,
            "revision": revision,
            "namespace": namespace,
            "mode": "dry-run" if dry_run else "execute",
            **({"preflight_mode": "simulation", "target_context": None,
                "backend": "simulation", "dry_run": True} if dry_run else {}),
        }
        self._executions[idempotency_key] = result
        return result

    async def restart_deployment(
        self,
        service: str,
        idempotency_key: str,
        dry_run: bool,
        namespace: str = "prod-sg",
        approval_receipt: str | None = None,
        target_context: dict | None = None,
    ) -> dict:
        if target_context is not None:
            raise ValueError("Simulation cannot verify a Kubernetes target context.")
        if idempotency_key in self._executions:
            return self._executions[idempotency_key]
        result: dict[str, str | bool] = {
            "accepted": True,
            "executed": not dry_run,
            "execution_backend": "simulation",
            "service": service,
            "namespace": namespace,
            "mode": "dry-run" if dry_run else "execute",
            **({"preflight_mode": "simulation", "target_context": None,
                "backend": "simulation", "dry_run": True} if dry_run else {}),
        }
        self._executions[idempotency_key] = result
        return result
