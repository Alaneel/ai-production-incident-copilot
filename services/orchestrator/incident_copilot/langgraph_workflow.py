from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, TypedDict, Awaitable, Callable, AsyncIterator

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .approval import ApprovalReceiptSigner
from .action_applicability import assess_action_applicability, validate_action_applicability
from .action_signal_time import current_action_signals
from .anomaly_windows import detect_anomaly_windows
from .guardrails import GuardrailViolation, has_usable_observation, validate_execution, validate_grounding, validate_input
from .incident_profiles import (
    DIAGNOSTIC_METRICS,
    PROFILES,
    IncidentProfile,
    classify_question,
    infer_profiles_from_evidence,
    select_primary_service,
)
from .models import (
    ApprovalDecision,
    ApprovalRequest,
    Hypothesis,
    IncidentQuestion,
    IncidentState,
    IncidentStatus,
    Evidence,
    Remediation,
    TraceEvent,
    WorkflowStep,
    normalize_target_context,
    target_context_digest,
)
from .reasoning import HypothesisGenerator
from .mcp_client import McpToolError
from .operation_status import public_observation
from .dependency_topology import ServiceTopology, legacy_topology
from .cross_service_rca import assess_diagnostic_coverage, assess_root_candidates, dependency_paths
from .cause_family import check_cause_family
from .deployment_timeline import compare_deployment_events
from .tools import TelemetryTools
from .recovery_verification import assess_recovery


class GraphState(TypedDict):
    incident: dict[str, Any]


class LangGraphIncidentWorkflow:
    """Durable LangGraph runtime with a real interrupt/resume approval node."""

    def __init__(
        self,
        *,
        tools: TelemetryTools,
        checkpointer: Any,
        approval_signer: ApprovalReceiptSigner,
        hypothesis_generator: HypothesisGenerator | None = None,
    ) -> None:
        self.tools = tools
        self.approval_signer = approval_signer
        self.hypothesis_generator = hypothesis_generator
        self.graph = self._build_graph(checkpointer)

    async def start(self, request: IncidentQuestion) -> IncidentState:
        validate_input(request)
        incident = IncidentState(
            question=request.question,
            environment=request.environment,
        )
        output = await self.graph.ainvoke(
            {"incident": incident.model_dump(mode="json")},
            config=self._config(incident.incident_id),
            durability="sync",
        )
        return IncidentState.model_validate(output["incident"])

    async def start_stream(self, request: IncidentQuestion) -> AsyncIterator[IncidentState]:
        """Emit retained checkpoints, never speculative node updates.

        LangGraph stream output is not itself a persistence acknowledgement.
        Read the saver at each boundary, including after the graph exits. A
        closed consumer stops this run; reconnecting may inspect the saved
        prefix, but must not silently start or resume another diagnosis.
        """
        validate_input(request)
        incident = IncidentState(question=request.question, environment=request.environment)
        config = self._config(incident.incident_id)
        previous: dict[str, Any] | None = None
        stream = self.graph.astream(
            {"incident": incident.model_dump(mode="json")},
            config=config, stream_mode="values", durability="sync",
        )
        try:
            async for _ in stream:
                retained = await self.get(incident.incident_id)
                if retained is not None:
                    value = retained.model_dump(mode="json")
                    if value != previous:
                        previous = value
                        yield retained
            retained = await self.get(incident.incident_id)
            if retained is None:
                raise ValueError("Diagnosis has no retained checkpoint.")
            if retained.model_dump(mode="json") != previous:
                yield retained
        finally:
            await stream.aclose()

    async def resume_with_approval(
        self,
        incident_id: str,
        approval: ApprovalRequest,
    ) -> IncidentState:
        snapshot = await self.graph.aget_state(self._config(incident_id))
        if not self._has_approval_interrupt(snapshot):
            raise GuardrailViolation("This checkpoint is not waiting for human approval.")
        output = await self.graph.ainvoke(
            Command(
                resume={
                    "decision": approval.decision.value,
                    "actor": approval.actor,
                    "reason": approval.reason,
                    "idempotency_key": approval.idempotency_key,
                }
            ),
            config=self._config(incident_id),
            durability="sync",
        )
        return IncidentState.model_validate(output["incident"])

    @staticmethod
    def _has_approval_interrupt(snapshot: Any) -> bool:
        # A proposal can set waiting_approval before the human node actually
        # pauses. next/status alone must not admit a concurrent resume or claim.
        return snapshot.next == (WorkflowStep.HUMAN_APPROVAL.value,) and any(
            task.name == WorkflowStep.HUMAN_APPROVAL.value and bool(task.interrupts)
            for task in snapshot.tasks
        )

    async def is_waiting_for_approval(self, incident_id: str) -> bool:
        snapshot = await self.graph.aget_state(self._config(incident_id))
        return self._has_approval_interrupt(snapshot)

    async def recheck_recovery(self, incident_id: str, *, request_actor: str = "internal-observer") -> IncidentState | None:
        """Resume only the read-only recovery branch, never the dispatch node."""
        snapshot = await self.graph.aget_state(self._config(incident_id))
        if not snapshot.values or "incident" not in snapshot.values:
            return None
        incident = IncidentState.model_validate(snapshot.values["incident"])
        if snapshot.next != (WorkflowStep.WAIT_FOR_RECOVERY.value,):
            raise GuardrailViolation("No durable recovery observation is waiting to be checked.")
        self._recovery_operation(incident)
        if not isinstance(request_actor, str) or not 2 <= len(request_actor) <= 120:
            raise GuardrailViolation("Recovery observation requires a bounded audit actor.")
        output = await self.graph.ainvoke(
            Command(resume={"kind": "recovery_check", "request_actor": request_actor}),
            config=self._config(incident_id), durability="sync",
        )
        return IncidentState.model_validate(output["incident"])

    async def get(self, incident_id: str) -> IncidentState | None:
        snapshot = await self.graph.aget_state(self._config(incident_id))
        if not snapshot.values or "incident" not in snapshot.values:
            return None
        return IncidentState.model_validate(snapshot.values["incident"])

    async def history(self, incident_id: str) -> list[IncidentState]:
        history: list[IncidentState] = []
        async for snapshot in self.graph.aget_state_history(self._config(incident_id)):
            if snapshot.values and "incident" in snapshot.values:
                history.append(
                    IncidentState.model_validate(snapshot.values["incident"])
                )
        return history

    async def inspect_operation(self, incident_id: str, *,
                                on_prepared: Callable[[dict], Awaitable[None]] | None = None) -> dict | None:
        incident = await self.get(incident_id)
        if incident is None:
            return None
        validate_execution(incident)
        remediation = incident.remediation
        if (incident.approval_decision != ApprovalDecision.APPROVE or remediation is None
                or remediation.tool not in {"restart_deployment", "rollback_release"}):
            raise GuardrailViolation("No approved destructive operation is available to inspect.")
        intents = [e.payload for e in incident.trace if e.event == "execution_intent_prepared"]
        decisions = [e.payload for e in incident.trace if e.event == "human_decision"]
        if not intents or not decisions:
            raise GuardrailViolation("A durable execution intent is required for inspection.")
        intent, decision = intents[-1], decisions[-1]
        key = intent.get("idempotency_key")
        if (not isinstance(key, str) or not 8 <= len(key) <= 200
                or decision.get("decision") != "approve"
                or decision.get("idempotency_key") != key
                or decision.get("actor") != incident.approval_actor
                or intent.get("actor") != incident.approval_actor
                or intent.get("tool") != remediation.tool
                or intent.get("arguments") != remediation.arguments):
            raise GuardrailViolation("The execution intent does not match the recorded approval.")
        arguments = remediation.arguments
        context = self._approved_target_context(incident)
        if on_prepared is not None:
            await on_prepared({"incident_id": incident.incident_id, "trace_id": incident.trace_id,
                               "tool": remediation.tool, "arguments": {field: arguments[field] for field in ("service", "namespace", "revision", "target_context") if field in arguments},
                               "approval_actor": incident.approval_actor, "idempotency_key": key})
        result = await asyncio.wait_for(self.tools.query_operation_status(
            service=arguments["service"], namespace=arguments["namespace"],
            tool=remediation.tool, revision=arguments.get("revision"),
            idempotency_key=key, approval_actor=incident.approval_actor,
            incident_id=incident.incident_id,
            **({"target_context": context} if context is not None else {}),
        ), timeout=15)
        return {"incident_id": incident.incident_id, "incident_status": incident.status.value,
                "tool": remediation.tool, "read_only": True,
                "observation": public_observation(result)}

    def _build_graph(self, checkpointer: Any) -> Any:
        builder = StateGraph(GraphState)
        builder.add_node(WorkflowStep.CLASSIFY.value, self._classify)
        builder.add_node(WorkflowStep.SELECT_SERVICES.value, self._select_services)
        builder.add_node(WorkflowStep.QUERY_TELEMETRY.value, self._query_telemetry)
        builder.add_node(WorkflowStep.RETRIEVE_CONTEXT.value, self._retrieve_context)
        builder.add_node(
            WorkflowStep.GENERATE_HYPOTHESES.value, self._generate_hypotheses
        )
        builder.add_node(WorkflowStep.VALIDATE_EVIDENCE.value, self._validate_evidence)
        builder.add_node(
            WorkflowStep.PROPOSE_REMEDIATION.value, self._propose_remediation
        )
        builder.add_node(WorkflowStep.HUMAN_APPROVAL.value, self._human_approval)
        builder.add_node(WorkflowStep.PREPARE_EXECUTION.value, self._prepare_execution)
        builder.add_node(WorkflowStep.EXECUTE.value, self._execute)
        builder.add_node(WorkflowStep.VERIFY_RECOVERY.value, self._verify_recovery)
        builder.add_node(WorkflowStep.WAIT_FOR_RECOVERY.value, self._wait_for_recovery)
        builder.add_node(WorkflowStep.COMPLETE.value, self._complete)

        builder.add_edge(START, WorkflowStep.CLASSIFY.value)
        builder.add_edge(
            WorkflowStep.CLASSIFY.value, WorkflowStep.SELECT_SERVICES.value
        )
        builder.add_edge(
            WorkflowStep.SELECT_SERVICES.value, WorkflowStep.QUERY_TELEMETRY.value
        )
        builder.add_edge(
            WorkflowStep.QUERY_TELEMETRY.value, WorkflowStep.RETRIEVE_CONTEXT.value
        )
        builder.add_conditional_edges(
            WorkflowStep.RETRIEVE_CONTEXT.value,
            lambda state: (
                "complete"
                if self._incident(state).status == IncidentStatus.INSUFFICIENT_EVIDENCE
                else "reason"
            ),
            {
                "complete": WorkflowStep.COMPLETE.value,
                "reason": WorkflowStep.GENERATE_HYPOTHESES.value,
            },
        )
        builder.add_edge(
            WorkflowStep.GENERATE_HYPOTHESES.value,
            WorkflowStep.VALIDATE_EVIDENCE.value,
        )
        builder.add_conditional_edges(
            WorkflowStep.VALIDATE_EVIDENCE.value,
            lambda state: "complete" if self._incident(state).status == IncidentStatus.INSUFFICIENT_EVIDENCE else "propose",
            {"complete": WorkflowStep.COMPLETE.value, "propose": WorkflowStep.PROPOSE_REMEDIATION.value},
        )
        builder.add_conditional_edges(
            WorkflowStep.PROPOSE_REMEDIATION.value,
            self._route_after_proposal,
            {
                "approval": WorkflowStep.HUMAN_APPROVAL.value,
                "complete": WorkflowStep.COMPLETE.value,
            },
        )
        builder.add_conditional_edges(
            WorkflowStep.HUMAN_APPROVAL.value,
            self._route_after_approval,
            {
                "execute": WorkflowStep.PREPARE_EXECUTION.value,
                "complete": WorkflowStep.COMPLETE.value,
            },
        )
        builder.add_edge(WorkflowStep.PREPARE_EXECUTION.value, WorkflowStep.EXECUTE.value)
        builder.add_edge(WorkflowStep.EXECUTE.value, WorkflowStep.VERIFY_RECOVERY.value)
        builder.add_conditional_edges(
            WorkflowStep.VERIFY_RECOVERY.value, self._route_after_recovery,
            {"wait": WorkflowStep.WAIT_FOR_RECOVERY.value, "complete": WorkflowStep.COMPLETE.value},
        )
        builder.add_edge(WorkflowStep.WAIT_FOR_RECOVERY.value, WorkflowStep.VERIFY_RECOVERY.value)
        builder.add_edge(WorkflowStep.COMPLETE.value, END)
        return builder.compile(checkpointer=checkpointer)

    async def _classify(self, graph_state: GraphState) -> GraphState:
        incident, started = self._begin(graph_state, WorkflowStep.CLASSIFY)
        profile = classify_question(incident.question)
        incident.incident_type = profile.key
        incident.severity = "SEV-2"
        self._record(
            incident,
            "incident_classified",
            {"incident_type": profile.key},
        )
        return self._finish(incident, started)

    async def _select_services(self, graph_state: GraphState) -> GraphState:
        incident, started = self._begin(graph_state, WorkflowStep.SELECT_SERVICES)
        profile = self._profile(incident)
        primary = select_primary_service(incident.question, profile)
        self._record(
            incident,
            "tool_call",
            {
                "tool": "get_service_dependencies",
                "arguments": {"service": primary},
                "read_only": True,
            },
        )
        topology_reader = getattr(self.tools, "get_service_topology", None)
        if callable(topology_reader):
            topology = await topology_reader(primary)
        else:
            topology = legacy_topology(primary, await self.tools.get_service_dependencies(primary))
        incident.service_topology = topology
        incident.complaint_service = primary
        incident.service_topologies = [topology]
        dependencies = topology.dependencies
        self._record(
            incident,
            "tool_result",
            {
                "tool": "get_service_dependencies",
                "dependencies": dependencies,
                "topology": topology.model_dump(mode="json"),
                "causal_direction_verified": False,
            },
        )
        incident.services = [primary, *dependencies[:8]]
        self._record(
            incident,
            "services_selected",
            {"primary": primary, "dependencies": dependencies[:8], "omitted_services": dependencies[8:],
             "max_related_services": 8},
        )
        return self._finish(incident, started)

    async def _query_telemetry(self, graph_state: GraphState) -> GraphState:
        incident, started = self._begin(graph_state, WorkflowStep.QUERY_TELEMETRY)
        service = incident.services[0]
        self._record(
            incident,
            "tool_call",
            {
                "tool": "query_metrics",
                "arguments": {
                    "service": service,
                    "metrics": list(DIAGNOSTIC_METRICS),
                    "window": "last_30_minutes",
                    "step_seconds": 15,
                    "metric_overrides": {
                        "memory_working_set": {
                            "window": "last_5_minutes",
                            "step_seconds": 5,
                        }
                    },
                },
                "read_only": True,
            },
        )
        self._record(
            incident,
            "tool_call",
            {
                "tool": "search_logs",
                "arguments": {
                    "service": service,
                    "query": "",
                    "window": "last_30_minutes",
                },
                "read_only": True,
            },
        )
        self._record(
            incident,
            "tool_call",
            {
                "tool": "query_traces",
                "arguments": {
                    "service": service,
                    "window": "last_30_minutes",
                    "limit": 20,
                },
                "read_only": True,
            },
        )
        results = await asyncio.gather(
            self.tools.query_metrics(
                service,
                DIAGNOSTIC_METRICS,
            ),
            self.tools.search_logs(service, ""),
            self.tools.query_traces(service),
            return_exceptions=True,
        )
        retained_results = []
        for tool, result in zip(("query_metrics", "search_logs", "query_traces"), results):
            if isinstance(result, (McpToolError, TimeoutError)):
                self._record(incident, "tool_error", {
                    "tool": tool, "service": service, "read_only": True,
                    "error_type": type(result).__name__, "outcome": "unavailable",
                })
                retained_results.append([])
            elif isinstance(result, BaseException):
                # Programming errors and cancellation are not missing telemetry.
                raise result
            else:
                expected_kind = {"query_metrics": "metric", "search_logs": "log", "query_traces": "trace"}[tool]
                excluded = [item for item in result if item.service != service or item.kind.value != expected_kind
                            or tool == "query_metrics" and item.query not in DIAGNOSTIC_METRICS]
                incident.evidence.extend(excluded)
                incident.diagnostic_excluded_evidence_ids.extend(item.id for item in excluded)
                result = [item for item in result if item.id not in incident.diagnostic_excluded_evidence_ids]
                self._record(incident, "tool_result", {
                    "tool": tool, "service": service,
                    "evidence_ids": [item.id for item in result],
                    "sources": sorted({item.source for item in result}),
                    "excluded_evidence_ids": [item.id for item in excluded],
                })
                retained_results.append(result)
        metric_evidence, log_evidence, trace_evidence = retained_results
        incident.evidence.extend(
            [*metric_evidence, *log_evidence, *trace_evidence]
        )
        windows = detect_anomaly_windows(metric_evidence, now=datetime.now(UTC))
        if windows:
            incident.anomalous_window = (
                datetime.fromisoformat(min(window["start"] for window in windows)),
                datetime.fromisoformat(max(window["end"] for window in windows)),
            )
        else:
            incident.anomalous_window = None
        self._record(incident, "anomaly_windows_detected", {
            "windows": windows,
            "method": "bounded_fault_lab_thresholds_and_memory_trend",
        })
        initial_profile = self._profile(incident)
        signal_now = datetime.now(UTC)
        primary_signals = [item for item in [*metric_evidence, *log_evidence] if item.service == service]
        current_signals, time_check = current_action_signals(primary_signals, now=signal_now)
        candidates = infer_profiles_from_evidence(current_signals, now=signal_now)
        simulation_summary = (not candidates
            and getattr(self.tools, "allow_simulated_signal_summaries", False) is True
            and not any("series" in item.attributes or "events" in item.attributes for item in primary_signals)
            and any(
            item.kind.value == "metric"
            and item.attributes.get("anomalous") is True
            and item.source == "prometheus"
            for item in primary_signals))
        if simulation_summary:
            candidates = [initial_profile]
        self._record(incident, "diagnostic_signal_time_checked", {
            **time_check, "primary_service": service,
            "eligible_evidence_ids": [item.id for item in current_signals],
            "simulation_only_summary_compatibility": simulation_summary,
            "historical_evidence_retained": True,
        })
        incident.candidate_incident_types = [profile.key for profile in candidates]
        inferred_profile = candidates[0] if len(candidates) == 1 else None
        if len(candidates) > 1:
            incident.incident_type = None
        elif inferred_profile is None:
            if self.hypothesis_generator is None or (not current_signals and not simulation_summary):
                incident.status = IncidentStatus.INSUFFICIENT_EVIDENCE
            incident.incident_type = None
        else:
            incident.incident_type = inferred_profile.key
        self._record(
            incident,
            "signals_classified",
            {
                "initial_type": initial_profile.key,
                "inferred_type": inferred_profile.key if inferred_profile else None,
                "candidate_types": incident.candidate_incident_types,
                "ambiguous": len(candidates) > 1,
                "metric_count": len(metric_evidence),
                "log_evidence_count": len(log_evidence),
                "trace_evidence_count": len(trace_evidence),
            },
        )
        await self._query_dependency_impacts(incident)
        await self._investigate_downstream(incident)
        return self._finish(incident, started)

    async def _investigate_downstream(self, incident: IncidentState) -> None:
        """Bounded directed discovery, then service-local evidence collection.

        Neither services[0] nor its local classification is retargeted. This
        branch can explain a candidate source, but cannot authorize a mutation.
        """
        primary = incident.services[0]
        now = datetime.now(UTC)
        topology_reader = getattr(self.tools, "get_service_topology", None)
        initial_paths = dependency_paths(primary, incident.service_topologies, now=now)
        semaphore = asyncio.Semaphore(4)
        def ordered(paths):
            basis_order = {"observed_trace": 0, "mixed": 1, "catalog": 2}
            return sorted((name for name in paths if name != primary), key=lambda name: (
                basis_order.get(paths[name]["path_basis"], 3), len(paths[name]["propagation_path"]), name,
            ))

        initial_downstream = ordered(initial_paths)
        discovery_targets = initial_downstream[:4]
        if callable(topology_reader):
            for service in discovery_targets:
                self._record(incident, "tool_call", {
                    "tool": "get_service_dependencies", "arguments": {"service": service},
                    "purpose": "cross_service_discovery", "read_only": True, "timeout_seconds": 10,
                })

            async def discover(service: str):
                async with semaphore:
                    try:
                        topology = await asyncio.wait_for(topology_reader(service), timeout=10)
                        if not isinstance(topology, ServiceTopology) or topology.service != service:
                            raise ValueError("Mismatched dependency service")
                        return service, topology, None
                    except (McpToolError, TimeoutError, ValueError) as error:
                        return service, None, type(error).__name__

            for service, topology, error_type in await asyncio.gather(*(discover(name) for name in discovery_targets)):
                if topology is not None:
                    incident.service_topologies.append(topology)
                self._record(incident, "tool_error" if error_type else "tool_result", {
                    "tool": "get_service_dependencies", "service": service,
                    "purpose": "cross_service_discovery", "read_only": True,
                    "error_type": error_type,
                    "topology": topology.model_dump(mode="json") if topology is not None else None,
                })
        paths = dependency_paths(primary, incident.service_topologies, now=datetime.now(UTC))
        # Take no more than eight related services beyond the complaint. Known
        # upstream names remain impact observations, never guessed root paths.
        downstream = ordered(paths)
        related = list(dict.fromkeys(incident.services[1:]))[:8]
        new_services = [name for name in downstream if name not in incident.services][:max(0, 8 - len(related))]
        related.extend(new_services)
        incident.services.extend(new_services)
        if new_services:
            await self._query_dependency_impacts(incident, services=new_services)
        selected = [name for name in downstream if name in related][:4]
        query_errors: dict[str, list[str]] = {name: [] for name in selected}
        calls = []
        for service in selected:
            available_metrics = {item.query for item in incident.evidence
                                 if item.service == service and item.kind.value == "metric"
                                 and item.id not in incident.diagnostic_excluded_evidence_ids}
            missing = tuple(name for name in DIAGNOSTIC_METRICS if name not in available_metrics)
            if missing:
                calls.append((service, "query_metrics", {"service": service, "metrics": list(missing)},
                              lambda name=service, metrics=missing: self.tools.query_metrics(name, metrics)))
            calls.extend([
                (service, "search_logs", {"service": service, "query": ""},
                 lambda name=service: self.tools.search_logs(name, "")),
                (service, "query_traces", {"service": service},
                 lambda name=service: self.tools.query_traces(name)),
            ])
        for service, tool, arguments, _ in calls:
            self._record(incident, "tool_call", {
                "tool": tool, "arguments": arguments, "purpose": "cross_service_diagnosis",
                "read_only": True, "timeout_seconds": 10,
            })

        async def query(service, tool, arguments, reader):
            async with semaphore:
                try:
                    result = await asyncio.wait_for(reader(), timeout=10)
                    # A provider cannot fill another service's evidence slot.
                    expected_kind = {"query_metrics": "metric", "search_logs": "log", "query_traces": "trace"}[tool]
                    excluded = [item for item in result if item.service != service or item.kind.value != expected_kind
                                or tool == "query_metrics" and item.query not in arguments["metrics"]]
                    return service, tool, [item for item in result if item not in excluded], excluded, None
                except (McpToolError, TimeoutError) as error:
                    return service, tool, [], [], type(error).__name__

        for service, tool, evidence, excluded, error_type in await asyncio.gather(*(
            query(service, tool, arguments, reader) for service, tool, arguments, reader in calls
        )):
            incident.evidence.extend([*evidence, *excluded])
            incident.diagnostic_excluded_evidence_ids.extend(item.id for item in excluded)
            if error_type:
                query_errors[service].append(tool)
            self._record(incident, "tool_error" if error_type else "tool_result", {
                "tool": tool, "service": service, "purpose": "cross_service_diagnosis",
                "read_only": True, "error_type": error_type,
                "evidence_ids": [item.id for item in evidence],
                "excluded_evidence_ids": [item.id for item in excluded],
            })
        now = datetime.now(UTC)
        for service in [primary, *selected]:
            local = [item for item in incident.evidence if item.service == service
                     and item.kind.value in {"metric", "log"}
                     and item.id not in incident.diagnostic_excluded_evidence_ids]
            current, time_check = current_action_signals(local, now=now)
            profiles = infer_profiles_from_evidence(current, now=now)
            investigation = {
                "service": service, "candidate_types": [profile.key for profile in profiles],
                "evidence_ids": [item.id for item in current],
                "anomaly_windows": detect_anomaly_windows(current, now=now),
                "query_errors": query_errors.get(service, []),
                "current_signal_time": time_check,
                "causal_direction_verified": False,
            }
            incident.service_investigations.append(investigation)
        all_candidates = assess_root_candidates(incident, now=now)
        incident.root_cause_candidates = all_candidates[:8]
        # Downstream timeouts are symptoms when an independent specific local
        # failure exists on the complaint-to-dependency path.
        specific = [item for item in all_candidates if item.incident_type != "downstream_timeout"]
        candidates = specific or all_candidates
        incident.diagnosis_service = candidates[0].service if len(candidates) == 1 else None
        coverage = assess_diagnostic_coverage(incident, now=now)
        if not coverage["complete"]:
            incident.diagnosis_service = None
        self._record(incident, "cross_service_diagnostic_coverage_assessed", {
            **coverage, "read_only": True, "omitted_or_unavailable_is_healthy": False,
        })
        remote = [item for item in incident.root_cause_candidates if item.service != primary]
        if remote and incident.status == IncidentStatus.INSUFFICIENT_EVIDENCE:
            incident.status = IncidentStatus.INVESTIGATING
        self._record(incident, "cross_service_candidates_assessed", {
            "complaint_service": primary, "diagnosis_service": incident.diagnosis_service,
            "candidates": [item.model_dump(mode="json") for item in incident.root_cause_candidates],
            "investigated_services": [primary, *selected],
            "omitted_diagnostic_services": [name for name in downstream if name not in selected],
            "omitted_discovery_services": initial_downstream[4:],
            "max_hops": 2, "max_related_services": 8, "max_diagnostic_services": 4,
            "max_concurrency": 4, "causal_direction_verified": False,
            "candidate_count": len(all_candidates), "omitted_candidate_count": max(0, len(all_candidates) - 8),
            "method": "directed_path_local_failure_and_symptom_window_overlap_not_causal_proof",
        })

    async def _query_dependency_impacts(self, incident: IncidentState, *, services: list[str] | None = None) -> None:
        primary = incident.services[0]
        dependencies = list(dict.fromkeys(service for service in (services if services is not None else incident.services[1:]) if service != primary))
        selected = dependencies[:8]
        semaphore = asyncio.Semaphore(4)
        for service in selected:
            self._record(incident, "tool_call", {
                "tool": "query_metrics", "arguments": {"service": service, "metrics": ["request_latency_p95", "request_error_rate"]},
                "read_only": True, "purpose": "dependency_impact", "timeout_seconds": 10,
            })

        async def query(service: str):
            async with semaphore:
                try:
                    evidence = await asyncio.wait_for(self.tools.query_metrics(
                        service, ("request_latency_p95", "request_error_rate")), timeout=10)
                    excluded = [item for item in evidence if item.service != service or item.kind.value != "metric"
                                or item.query not in {"request_latency_p95", "request_error_rate"}]
                    return service, [item for item in evidence if item not in excluded], excluded, None
                except (McpToolError, TimeoutError) as error:
                    return service, [], [], type(error).__name__

        for service, evidence, excluded, error_type in await asyncio.gather(*(query(service) for service in selected)):
            incident.evidence.extend([*evidence, *excluded])
            incident.diagnostic_excluded_evidence_ids.extend(item.id for item in excluded)
            windows = detect_anomaly_windows(evidence, now=datetime.now(UTC))
            overlaps = None
            if incident.anomalous_window is not None and windows:
                start, end = incident.anomalous_window
                overlaps = any(datetime.fromisoformat(window["start"]) <= end
                               and datetime.fromisoformat(window["end"]) >= start for window in windows)
            impact = {
                "service": service,
                "relation": incident.service_topology.relation(service) if incident.service_topology else "discovered_dependency",
                "topology_kind": incident.service_topology.topology_kind if incident.service_topology else "unknown",
                "observed_call_count": sum(service in (call.source, call.target) for call in incident.service_topology.observed.calls) if incident.service_topology else 0,
                "status": "query_failed" if error_type else "observed_anomaly" if windows else "unconfirmed",
                "evidence_ids": [item.id for item in evidence], "anomaly_windows": windows,
                "overlaps_primary_window": overlaps,
                "causal_direction_verified": False,
            }
            incident.service_impacts.append(impact)
            self._record(incident, "tool_result" if error_type is None else "tool_error", {
                "tool": "query_metrics", "service": service, "purpose": "dependency_impact",
                "evidence_ids": impact["evidence_ids"], "error_type": error_type,
                "excluded_evidence_ids": [item.id for item in excluded],
            })
        self._record(incident, "dependency_impacts_assessed", {
            "impacts": incident.service_impacts,
            "omitted_services": (incident.service_topology.dependencies[8:]
                                 if services is None and incident.service_topology else dependencies[8:]),
            "max_services": 8, "max_concurrency": 4,
            "method": "timestamped_latency_error_thresholds_not_causal_proof",
        })

    async def _retrieve_context(self, graph_state: GraphState) -> GraphState:
        incident, started = self._begin(graph_state, WorkflowStep.RETRIEVE_CONTEXT)
        profile = self._profile(incident)
        service = incident.services[0]
        knowledge_query = (
            f"{incident.question} {profile.knowledge_query}"
            if incident.incident_type is not None else incident.question
        )
        if len(incident.candidate_incident_types) > 1:
            knowledge_query = incident.question + " " + " ".join(
                PROFILES[key].knowledge_query for key in incident.candidate_incident_types
            )
        self._record(
            incident,
            "tool_call",
            {
                "tool": "search_incident_knowledge",
                "arguments": {
                    "service": service,
                    "query": knowledge_query,
                    "content_types": [
                        "runbook",
                        "past_incident",
                        "technical_doc",
                    ],
                },
                "read_only": True,
            },
        )
        self._record(
            incident,
            "tool_call",
            {
                "tool": "get_recent_deployments",
                "arguments": {"service": service},
                "read_only": True,
            },
        )
        knowledge, deployments = await asyncio.gather(
            self.tools.search_incident_knowledge(
                service,
                knowledge_query,
            ),
            self.tools.get_recent_deployments(service, namespace=incident.environment),
        )
        self._record(
            incident,
            "tool_result",
            {
                "tool": "search_incident_knowledge",
                "evidence_ids": [item.id for item in knowledge],
                "sources": [item.source for item in knowledge],
            },
        )
        self._record(
            incident,
            "tool_result",
            {
                "tool": "get_recent_deployments",
                "evidence_ids": [item.id for item in deployments],
                "sources": sorted({item.source for item in deployments}),
            },
        )
        incident.evidence.extend([*knowledge, *deployments])
        await self._retrieve_candidate_context(incident)
        event_comparison = compare_deployment_events(incident)
        if event_comparison is not None:
            self._record(incident, "deployment_event_window_comparison", event_comparison)
        if (incident.status == IncidentStatus.INSUFFICIENT_EVIDENCE
                or not self._has_observed_signals(incident)):
            has_samples = self._has_observed_signals(incident)
            reason = (
                "Retained telemetry does not match a supported diagnostic failure pattern."
                if has_samples else "No valid metric samples or log events were retained."
            )
            incident.status = IncidentStatus.INSUFFICIENT_EVIDENCE
            incident.incident_type = None
            incident.hypotheses = []
            incident.remediation = Remediation(
                title="Collect telemetry before proposing a production change",
                tool="none",
                arguments={"service": service},
                command_preview="Check telemetry ingestion and the selected service/time window.",
                risk="low",
                requires_approval=False,
                dry_run_passed=False,
                expected_result=reason + " Collect current failure signals and retry diagnosis.",
            )
            self._record(
                incident,
                "diagnosis_abstained",
                {
                    "reason": reason,
                    "evidence_ids": [item.id for item in incident.evidence],
                    "production_action_proposed": False,
                },
            )
        return self._finish(incident, started)

    async def _retrieve_candidate_context(self, incident: IncidentState) -> None:
        primary = incident.services[0]
        services = list(dict.fromkeys(item.service for item in incident.root_cause_candidates
                                      if item.service != primary))[:4]
        semaphore = asyncio.Semaphore(4)
        calls = []
        for service in services:
            types = list(dict.fromkeys(item.incident_type for item in incident.root_cause_candidates
                                       if item.service == service))
            query = incident.question + " " + " ".join(PROFILES[key].knowledge_query for key in types)
            calls.extend([
                (service, "search_incident_knowledge", {"service": service, "query": query},
                 lambda name=service, text=query: self.tools.search_incident_knowledge(name, text)),
                (service, "get_recent_deployments", {"service": service, "namespace": incident.environment},
                 lambda name=service: self.tools.get_recent_deployments(name, namespace=incident.environment)),
            ])
        for service, tool, arguments, _ in calls:
            self._record(incident, "tool_call", {
                "tool": tool, "arguments": arguments, "purpose": "candidate_service_context",
                "read_only": True, "timeout_seconds": 10,
            })

        async def retrieve(service, tool, reader):
            async with semaphore:
                try:
                    result = await asyncio.wait_for(reader(), timeout=10)
                    return service, tool, [item for item in result if item.service == service], None
                except (McpToolError, TimeoutError) as error:
                    return service, tool, [], type(error).__name__

        for service, tool, result, error_type in await asyncio.gather(*(
            retrieve(service, tool, reader) for service, tool, _, reader in calls
        )):
            incident.evidence.extend(result)
            self._record(incident, "tool_error" if error_type else "tool_result", {
                "tool": tool, "service": service, "purpose": "candidate_service_context",
                "read_only": True, "error_type": error_type,
                "evidence_ids": [item.id for item in result],
            })

    async def _generate_hypotheses(self, graph_state: GraphState) -> GraphState:
        incident, started = self._begin(graph_state, WorkflowStep.GENERATE_HYPOTHESES)
        remote = [item for item in incident.root_cause_candidates if item.service != incident.services[0]]
        if remote and self.hypothesis_generator is None:
            specific = [item for item in incident.root_cause_candidates if item.incident_type != "downstream_timeout"]
            candidates = specific or incident.root_cause_candidates
            incident.hypotheses = []
            for rank, candidate in enumerate(candidates[:3], start=1):
                profile = PROFILES[candidate.incident_type]
                scoped = incident.model_copy(update={"incident_type": profile.key, "services": [candidate.service]})
                supporting = self._supporting_evidence(scoped, scope_all_kinds=True)
                # The local fault signature, not a generic latency metric, must
                # remain cited even if its provider relevance score is lower.
                local = [item for item in supporting if item.id in candidate.local_evidence_ids]
                ids = list(dict.fromkeys([
                    *[item.id for item in local[:2]], *candidate.local_evidence_ids[:2],
                    *candidate.symptom_evidence_ids[:2], *[item.id for item in supporting[:4]],
                ]))
                incident.hypotheses.append(Hypothesis(
                    rank=rank, cause=profile.cause, confidence=profile.confidence,
                    evidence_ids=ids, cause_service=candidate.service,
                    propagation_path=candidate.propagation_path,
                    validation_notes=[*candidate.validation_notes,
                        "Service-local fault signals and call paths identify investigation candidates, not confirmed causation.",
                        "Rule scores are not calibrated probabilities; no cross-service mutation is authorized."],
                ))
            self._record(incident, "cross_service_hypotheses_generated", {
                "complaint_service": incident.services[0], "diagnosis_service": incident.diagnosis_service,
                "hypotheses": [item.model_dump(mode="json") for item in incident.hypotheses],
                "causal_direction_verified": False, "production_action_proposed": False,
            })
            return self._finish(incident, started)
        if len(incident.candidate_incident_types) > 1 and self.hypothesis_generator is None:
            incident.hypotheses = []
            for rank, key in enumerate(incident.candidate_incident_types, start=1):
                profile = PROFILES[key]
                scoped = incident.model_copy(update={"incident_type": key})
                supporting = self._supporting_evidence(scoped)
                incident.hypotheses.append(Hypothesis(
                    rank=rank, cause=profile.cause, confidence=profile.confidence,
                    evidence_ids=[item.id for item in supporting[:4]],
                    cause_service=incident.services[0], propagation_path=[incident.services[0]],
                    validation_notes=["Multiple observed failure patterns match; causal ordering is unresolved.",
                                      "Ranking follows lab rule order, not calibrated causal probability."],
                ))
            self._record(incident, "competing_hypotheses", {
                "candidate_types": incident.candidate_incident_types,
                "production_action_proposed": False,
                "evidence_ids_by_candidate": {key: item.evidence_ids for key, item in zip(incident.candidate_incident_types, incident.hypotheses)},
            })
            return self._finish(incident, started)
        if self.hypothesis_generator is not None:
            hypotheses, model_trace = await self.hypothesis_generator.generate(
                question=incident.question,
                incident_type=incident.incident_type,
                evidence=[item for item in incident.evidence if item.id not in incident.diagnostic_excluded_evidence_ids],
            )
            incident.hypotheses = hypotheses
            self._attribute_model_hypotheses(incident)
            self._record(incident, "model_generation", model_trace)
            return self._finish(incident, started)

        profile = self._profile(incident)
        supporting = self._supporting_evidence(incident)
        self._record(incident, "evidence_selection", {
            "policy": "profile_metric_priority_then_score",
            "profile": profile.key,
            "primary_service": incident.services[0] if incident.services else None,
            "preferred_metrics": list(profile.metrics),
            "selected_ids": [item.id for item in supporting[:4]],
        })
        incident.hypotheses = [
            Hypothesis(
                rank=1,
                cause=profile.cause,
                confidence=profile.confidence,
                cause_service=incident.services[0], propagation_path=[incident.services[0]],
                evidence_ids=[item.id for item in supporting[:4]],
                validation_notes=[
                    "Citations are selected by failure-profile metric priority, then evidence score.",
                    "Operational knowledge provides context, not independent proof of causation.",
                    "This is a deterministic profile hypothesis, not a validated causal conclusion.",
                ],
            ),
            Hypothesis(
                rank=2,
                cause=profile.alternative_cause,
                confidence=0.31,
                evidence_ids=[item.id for item in supporting[:2]],
                validation_notes=["This alternative has less corroborating evidence."],
            ),
        ]
        return self._finish(incident, started)

    @staticmethod
    def _attribute_model_hypotheses(incident: IncidentState) -> None:
        """Bind only known-family cited local signals, not model-supplied names.

        This is a structural/profile linkage, NOT semantic entailment. Existing
        generator signatures and strict provider schemas remain unchanged.
        """
        for hypothesis in incident.hypotheses:
            hypothesis.cause_service = None
            hypothesis.propagation_path = []
            cited = set(hypothesis.evidence_ids)
            matches = [item for item in incident.root_cause_candidates
                       if check_cause_family(hypothesis.cause, item.incident_type)["status"] == "recognized_family_match"
                       and cited.intersection(item.local_evidence_ids)]
            if len(matches) != 1:
                continue
            candidate = matches[0]
            hypothesis.cause_service = candidate.service
            if candidate.path_basis == "primary" or cited.intersection(candidate.symptom_evidence_ids):
                hypothesis.propagation_path = candidate.propagation_path
            hypothesis.validation_notes.append(
                "Service attribution links a known failure family to cited local signals; semantic and causal validity remain unverified."
            )

    async def _validate_evidence(self, graph_state: GraphState) -> GraphState:
        incident, started = self._begin(graph_state, WorkflowStep.VALIDATE_EVIDENCE)
        if self.hypothesis_generator is not None and not incident.hypotheses:
            self._record(incident, "model_hypothesis_abstained", {
                "reason": "The model returned no supported hypothesis.",
                "production_action_proposed": False,
                "semantic_entailment_verified": False,
            })
            incident.status = IncidentStatus.INSUFFICIENT_EVIDENCE
            incident.incident_type = None
            incident.remediation = Remediation(
                title="Collect additional evidence before diagnosing a cause",
                tool="none", arguments={"service": incident.services[0]},
                command_preview="Review telemetry coverage, observations and the incident window.",
                risk="low", requires_approval=False, dry_run_passed=False,
                expected_result="No supported model hypothesis. No production action was proposed.",
            )
            return self._finish(incident, started)
        try:
            validate_grounding(incident)
        except GuardrailViolation as error:
            self._record(incident, "hypothesis_citations_rejected", {
                "reason": str(error),
                "hypotheses": [{"rank": item.rank, "evidence_ids": item.evidence_ids}
                               for item in incident.hypotheses],
                "production_action_proposed": False,
            })
            incident.status = IncidentStatus.INSUFFICIENT_EVIDENCE
            incident.incident_type = None
            incident.hypotheses = []
            incident.remediation = Remediation(
                title="Repair diagnosis evidence before proposing a production change",
                tool="none", arguments={"service": incident.services[0]},
                command_preview="Review retained observations and rejected citation IDs in the audit trace.",
                risk="low", requires_approval=False, dry_run_passed=False,
                expected_result="Generated hypotheses failed citation validation. No production action was proposed.",
            )
            return self._finish(incident, started)
        self._record(incident, "hypothesis_citations_validated", {
            "checks": ["retained_unique_ids", "usable_observations", "metric_or_log", "distinct_kinds"],
            "hypotheses": [{"rank": item.rank, "evidence_ids": list(dict.fromkeys(item.evidence_ids))}
                           for item in incident.hypotheses],
            "semantic_entailment_verified": False,
        })
        return self._finish(incident, started)

    @staticmethod
    def _bound_target_context(tool: str, arguments: dict, value: Any) -> dict:
        if not isinstance(value, dict):
            raise ValueError("A complete Kubernetes target context is required.")
        context = normalize_target_context(value)
        if any(context[field] != expected for field, expected in {
            "tool": tool, "service": arguments["service"], "namespace": arguments["namespace"],
            "revision": arguments.get("revision") if tool == "rollback_release" else None,
        }.items()):
            raise ValueError("Preflight target scope does not match the proposed operation.")
        return context

    async def _run_preflight(self, call: Awaitable[dict]) -> dict:
        try:
            return await asyncio.wait_for(call, timeout=15)
        except (McpToolError, TimeoutError) as error:
            # Admission/transport failure cannot be silently converted to simulation.
            return {"accepted": False, "preflight_error_type": type(error).__name__}

    def _approved_target_context(self, incident: IncidentState) -> dict | None:
        remediation = incident.remediation
        if remediation is None or "target_context" not in remediation.arguments:
            raise GuardrailViolation("Renew approval after a verified preflight; legacy target context is missing.")
        mode = remediation.preflight_mode
        records = [event.payload for event in incident.trace if event.event == "tool_result"
                   and event.payload.get("tool") == remediation.tool and event.payload.get("dry_run") is True]
        if (not records or records[-1].get("accepted") is not True
                or records[-1].get("preflight_mode") != mode
                or "target_context" not in records[-1]):
            raise GuardrailViolation("The approved target lacks a matching successful preflight record.")
        context = remediation.arguments["target_context"]
        if mode == "simulation" and context is None and records[-1]["target_context"] is None:
            return None
        if mode != "kubernetes-server-dry-run":
            raise GuardrailViolation("Unverified preflight mode cannot authorize execution.")
        try:
            normalized = self._bound_target_context(remediation.tool, remediation.arguments, context)
            retained = self._bound_target_context(remediation.tool, remediation.arguments, records[-1]["target_context"])
        except (ValueError, TypeError, KeyError) as error:
            raise GuardrailViolation("The approved Kubernetes target context is invalid.") from error
        if normalized != retained:
            raise GuardrailViolation("The approved Kubernetes target differs from the preflight record.")
        return normalized

    async def _propose_remediation(self, graph_state: GraphState) -> GraphState:
        incident, started = self._begin(graph_state, WorkflowStep.PROPOSE_REMEDIATION)
        if incident.status == IncidentStatus.INSUFFICIENT_EVIDENCE and incident.remediation is not None:
            return self._finish(incident, started)
        coverage = assess_diagnostic_coverage(incident, now=datetime.now(UTC))
        if not coverage["complete"]:
            incident.diagnosis_service = None
            incident.remediation = Remediation(
                title="Complete unresolved dependency diagnostics before changing production",
                tool="none", arguments={"service": incident.services[0],
                                        "unresolved_services": coverage["unresolved_services"]},
                command_preview="Expand bounded read-only investigation or restore telemetry; do not infer dependency health from omissions.",
                risk="low", requires_approval=False, dry_run_passed=False,
                expected_result="Downstream diagnostics remain unresolved or unavailable. No restart or rollback is proposed.",
            )
            self._record(incident, "cross_service_remediation_advisory", {
                **coverage, "complaint_service": incident.services[0],
                "dry_run_or_execution_called": False, "causal_direction_verified": False,
            })
            return self._finish(incident, started)
        if any(item.service != incident.services[0] for item in incident.root_cause_candidates):
            candidates = list(dict.fromkeys(item.service for item in incident.root_cause_candidates
                                           if item.service != incident.services[0]))
            incident.remediation = Remediation(
                title="Investigate candidate dependency failures before changing production",
                tool="none", arguments={"service": incident.services[0], "candidate_services": candidates},
                command_preview="Review candidate-service telemetry, runbooks and call-path evidence; confirm causal direction before a separately scoped action.",
                risk="low", requires_approval=False, dry_run_passed=False,
                expected_result="Cross-service attribution is a hypothesis, not execution authority. No restart or rollback is proposed.",
            )
            self._record(incident, "cross_service_remediation_advisory", {
                "complaint_service": incident.services[0], "candidate_services": candidates,
                "diagnosis_service": incident.diagnosis_service,
                "dry_run_or_execution_called": False, "causal_direction_verified": False,
            })
            return self._finish(incident, started)
        if incident.incident_type is None:
            incident.remediation = Remediation(
                title="Disambiguate competing failure patterns" if len(incident.candidate_incident_types) > 1 else "Investigate cited hypotheses before selecting remediation",
                tool="none", arguments={"service": incident.services[0]},
                command_preview="Review evidence and select a verified runbook action.",
                risk="low", requires_approval=False, dry_run_passed=False,
                expected_result="Validate the unclassified failure before proposing a production mutation.",
            )
            return self._finish(incident, started)
        profile = self._profile(incident)
        service = incident.services[0]
        allow_simulated = getattr(self.tools, "allow_simulated_deployment_targets", False) is True
        previous_revision = self._previous_revision(incident, allow_simulated=allow_simulated)
        if profile.action == "rollback_release" and previous_revision is None and not allow_simulated:
            incident.remediation = Remediation(
                title=f"Verify live deployment history for {service} before considering rollback",
                tool="none", arguments={"service": service},
                command_preview="Read actual deployment/revision history and verify the fault's relationship to the release.",
                risk="low", requires_approval=False, dry_run_passed=False,
                expected_result="Diagnosis retained; synthetic, unknown or invalid deployment metadata cannot authorize a rollback target.",
            )
            self._record(incident, "rollback_target_unverified", {
                "service": service, "reason": "No validated live deployment target for the incident namespace.",
                "deployment_evidence_ids": [item.id for item in incident.evidence if item.kind.value == "deployment"],
                "dry_run_or_execution_called": False,
            })
            return self._finish(incident, started)
        if profile.action == "rollback_release" and previous_revision is None:
            incident.status = IncidentStatus.REMEDIATION_FAILED
            incident.remediation = Remediation(
                title=f"Rollback target unavailable for {service}",
                tool="rollback_release",
                arguments={"service": service, "namespace": incident.environment},
                command_preview="Verify deployment history before proposing rollback.",
                risk="high",
                requires_approval=True,
                dry_run_passed=False,
                expected_result="No trustworthy previous revision was retained for the primary service.",
            )
            self._record(
                incident,
                "remediation_blocked",
                {"reason": "Missing or invalid rollback target in deployment evidence.",
                 "tool": "rollback_release", "service": service},
            )
            return self._finish(incident, started)
        if profile.action != "none":
            assessment = assess_action_applicability(incident, profile.action,
                allow_simulated_summaries=getattr(self.tools, "allow_simulated_action_summaries", False) is True)
            self._record(incident, "action_signal_applicability_checked", assessment)
            if not assessment["allowed"]:
                incident.remediation = Remediation(
                    title="Review the diagnosis-to-action evidence before remediation",
                    tool="none", arguments={"service": service},
                    command_preview="Compare the leading hypothesis citations with current primary-service failure signals.",
                    risk="low", requires_approval=False, dry_run_passed=False,
                    expected_result=assessment["reason"] + " No production mutation is proposed.",
                )
                return self._finish(incident, started)
        dry_run_key = (
            f"{incident.incident_id}:{profile.action}:{previous_revision or incident.environment}:dry-run"
        )
        if profile.action == "rollback_release":
            self._record(
                incident,
                "tool_call",
                {
                    "tool": "rollback_release",
                    "arguments": {
                        "service": service,
                        "namespace": incident.environment,
                        "revision": previous_revision,
                        "dry_run": True,
                        "idempotency_key": dry_run_key,
                    },
                    "read_only": False,
                },
            )
            result = await self._run_preflight(self.tools.rollback_release(
                service=service,
                revision=previous_revision,
                namespace=incident.environment,
                idempotency_key=dry_run_key,
                dry_run=True,
            ))
            title = f"Restore {service} container images from revision {previous_revision}"
            arguments = {
                "service": service,
                "namespace": incident.environment,
                "revision": previous_revision,
            }
            command_preview = (
                f"rollback_release(service='{service}', namespace='{incident.environment}', "
                f"revision='{previous_revision}') — container images only, not full rollout undo"
            )
        elif profile.action == "restart_deployment":
            self._record(
                incident,
                "tool_call",
                {
                    "tool": "restart_deployment",
                    "arguments": {
                        "service": service,
                        "namespace": incident.environment,
                        "dry_run": True,
                        "idempotency_key": dry_run_key,
                    },
                    "read_only": False,
                },
            )
            result = await self._run_preflight(self.tools.restart_deployment(
                service=service,
                namespace=incident.environment,
                idempotency_key=dry_run_key,
                dry_run=True,
            ))
            title = f"Restart {service} deployment"
            arguments = {
                "service": service,
                "namespace": incident.environment,
            }
            command_preview = f"kubectl -n {incident.environment} rollout restart deploy/{service}"
        else:
            result = {"accepted": True}
            title = f"Contain {profile.cause.lower()}"
            arguments = {"service": service}
            command_preview = "No production mutation; apply runbook guidance."

        preflight_mode = None
        preflight_error = None
        dry_run_passed = profile.action != "none" and isinstance(result, dict) and result.get("accepted") is True
        if profile.action != "none":
            dry_run_passed = (dry_run_passed and result.get("executed") is False
                              and result.get("dry_run") is True)
            if dry_run_passed:
                try:
                    mode = result.get("preflight_mode")
                    if mode == "kubernetes-server-dry-run" and result.get("backend") == "kubernetes":
                        arguments["target_context"] = self._bound_target_context(profile.action, arguments, result.get("target_context"))
                    elif (mode == "simulation" and result.get("backend") == "simulation"
                          and "target_context" in result and result["target_context"] is None):
                        arguments["target_context"] = None
                    else:
                        raise ValueError("Preflight backend/mode or target context is unverified.")
                    preflight_mode = mode
                except (ValueError, TypeError, KeyError):
                    dry_run_passed = False
                    preflight_error = "invalid_preflight_context"
            if isinstance(result, dict) and result.get("preflight_error_type"):
                preflight_error = result["preflight_error_type"]
            self._record(
                incident,
                "tool_result",
                {
                    "tool": profile.action,
                    "dry_run": True,
                    "accepted": dry_run_passed,
                    "preflight_mode": preflight_mode,
                    "target_context": arguments.get("target_context"),
                    "error_type": preflight_error,
                },
            )

        requires_approval = profile.action != "none"
        incident.remediation = Remediation(
            title=title,
            tool=profile.action,
            arguments=arguments,
            command_preview=command_preview,
            risk="high" if requires_approval else "low",
            requires_approval=requires_approval,
            dry_run_passed=dry_run_passed,
            preflight_mode=preflight_mode,
            expected_result=(
                "Dry-run did not explicitly succeed; inspect the tool result before retrying."
                if requires_approval and not dry_run_passed else profile.expected_result + (
                    " The Kubernetes adapter restores container images only, not the full release or pod template."
                    if profile.action == "rollback_release" else ""
                )
            ),
        )
        if requires_approval and not dry_run_passed:
            incident.status = IncidentStatus.REMEDIATION_FAILED
            self._record(
                incident,
                "remediation_blocked",
                {"reason": "Dry-run did not explicitly succeed.", "tool": profile.action},
            )
            return self._finish(incident, started)
        if requires_approval:
            incident.status = IncidentStatus.WAITING_APPROVAL
        finished = self._finish(incident, started)
        if not requires_approval:
            return finished
        paused = self._incident(finished)
        paused.current_step = WorkflowStep.HUMAN_APPROVAL
        self._record(
            paused,
            "workflow_interrupted",
            {"reason": "human_approval", "dry_run_key": dry_run_key},
        )
        paused.updated_at = datetime.now(UTC)
        return {"incident": paused.model_dump(mode="json")}

    async def _human_approval(self, graph_state: GraphState) -> GraphState:
        incident = self._incident(graph_state)
        remediation = incident.remediation
        if remediation is None:
            raise ValueError("No remediation is available for approval.")
        response = interrupt(
            {
                "incident_id": incident.incident_id,
                "remediation_id": remediation.id,
                "title": remediation.title,
                "tool": remediation.tool,
                "arguments": remediation.arguments,
                "risk": remediation.risk,
                "dry_run_passed": remediation.dry_run_passed,
                "preflight_mode": remediation.preflight_mode,
                "evidence_ids": incident.hypotheses[0].evidence_ids,
            }
        )
        approval = ApprovalRequest.model_validate(response)
        incident.approval_decision = approval.decision
        incident.approval_actor = approval.actor
        incident.approval_reason = approval.reason
        self._record(
            incident,
            "human_decision",
            {
                "decision": approval.decision.value,
                "actor": approval.actor,
                "reason": approval.reason,
                "idempotency_key": approval.idempotency_key,
            },
        )
        if approval.decision == ApprovalDecision.REJECT:
            incident.status = IncidentStatus.REJECTED
        else:
            incident.status = IncidentStatus.MITIGATING
        incident.updated_at = datetime.now(UTC)
        return {
            "incident": {
                **incident.model_dump(mode="json"),
                "_approval_idempotency_key": approval.idempotency_key,
            }
        }

    async def _prepare_execution(self, graph_state: GraphState) -> GraphState:
        idempotency_key = graph_state["incident"].get("_approval_idempotency_key")
        incident, started = self._begin(graph_state, WorkflowStep.PREPARE_EXECUTION)
        if not isinstance(idempotency_key, str):
            raise ValueError("Approval idempotency key is missing.")
        validate_execution(incident)
        assert incident.remediation is not None
        self._approved_target_context(incident)
        self._validate_rollback_target(incident)
        validate_action_applicability(incident,
            allow_simulated_summaries=getattr(self.tools, "allow_simulated_action_summaries", False) is True)
        self._record(incident, "execution_intent_prepared", {
            "tool": incident.remediation.tool,
            "arguments": dict(incident.remediation.arguments),
            "actor": incident.approval_actor, "idempotency_key": idempotency_key,
            "dispatch_status": "not_confirmed", "execution_outcome": "unknown",
        })
        # The graph's sync durability commits this node before scheduling the
        # mutation node. Do not persist the signed receipt in the checkpoint.
        output = self._finish(incident, started)
        output["incident"]["_approval_idempotency_key"] = idempotency_key
        return output

    async def _execute(self, graph_state: GraphState) -> GraphState:
        raw_incident = graph_state["incident"]
        idempotency_key = raw_incident.get("_approval_idempotency_key")
        incident, started = self._begin(graph_state, WorkflowStep.EXECUTE)
        if not isinstance(idempotency_key, str):
            raise ValueError("Approval idempotency key is missing.")
        validate_execution(incident)
        assert incident.remediation is not None
        intents = [event.payload for event in incident.trace if event.event == "execution_intent_prepared"]
        if not intents or any(intents[-1].get(key) != value for key, value in {
            "tool": incident.remediation.tool, "arguments": incident.remediation.arguments,
            "actor": incident.approval_actor, "idempotency_key": idempotency_key,
        }.items()):
            raise ValueError("A matching durable execution intent is required before dispatch.")
        self._validate_rollback_target(incident)
        validate_action_applicability(incident,
            allow_simulated_summaries=getattr(self.tools, "allow_simulated_action_summaries", False) is True)
        service = str(incident.remediation.arguments["service"])
        namespace = str(incident.remediation.arguments["namespace"])
        context = self._approved_target_context(incident)
        target = (
            str(incident.remediation.arguments["revision"])
            if incident.remediation.tool == "rollback_release"
            else namespace
        )
        receipt = self.approval_signer.issue(
            incident_id=incident.incident_id,
            actor=str(incident.approval_actor),
            tool=incident.remediation.tool,
            service=service,
            target=target,
            namespace=namespace,
            target_context_sha256=target_context_digest(context) if context is not None else None,
        )
        self._record(
            incident,
            "tool_call",
            {
                "tool": incident.remediation.tool,
                "arguments": {
                    **incident.remediation.arguments,
                    "dry_run": False,
                    "idempotency_key": idempotency_key,
                },
                "read_only": False,
                "approval": {
                    "actor": incident.approval_actor,
                    "receipt_bound": True,
                    "target_context_sha256": target_context_digest(context) if context is not None else None,
                },
            },
        )
        try:
            if incident.remediation.tool == "rollback_release":
                execution_result = await self.tools.rollback_release(
                    service=service,
                    revision=target,
                    namespace=namespace,
                    idempotency_key=idempotency_key,
                    dry_run=False,
                    approval_receipt=receipt,
                    **({"target_context": context} if context is not None else {}),
                )
            elif incident.remediation.tool == "restart_deployment":
                execution_result = await self.tools.restart_deployment(
                    service=service,
                    namespace=namespace,
                    idempotency_key=idempotency_key,
                    dry_run=False,
                    approval_receipt=receipt,
                    **({"target_context": context} if context is not None else {}),
                )
            else:
                raise ValueError("Unsupported destructive remediation tool.")
        except (McpToolError, TimeoutError) as error:
            # A lost response is not evidence that a production mutation failed.
            # Preserve the approval and call instead of retrying with a new key.
            incident.status = IncidentStatus.REMEDIATION_PENDING
            incident.recovery = {"status": "pending", "simulation": context is None,
                "execution_acknowledged_at": None, "applied_generation": None,
                "applied_template_sha256": None, "reason_codes": ["execution_unconfirmed"],
                "checked_at": datetime.now(UTC).isoformat(), "metric_evidence_ids": [],
                "observation_window": None}
            self._record(
                incident,
                "tool_error",
                {
                    "tool": incident.remediation.tool,
                    "error_type": type(error).__name__,
                    "outcome": "unknown",
                    "idempotency_key": idempotency_key,
                    "reason": "Verify the operation outcome before another execution attempt.",
                },
            )
            return self._finish(incident, started)
        if not isinstance(execution_result, dict):
            incident.status = IncidentStatus.REMEDIATION_PENDING
            incident.recovery = {"status": "pending", "simulation": context is None,
                "execution_acknowledged_at": None, "applied_generation": None,
                "applied_template_sha256": None, "reason_codes": ["execution_unconfirmed"],
                "checked_at": datetime.now(UTC).isoformat(), "metric_evidence_ids": [], "observation_window": None}
            self._record(incident, "tool_error", {"tool": incident.remediation.tool,
                "error_type": "InvalidExecutionResponse", "outcome": "unknown", "idempotency_key": idempotency_key})
            return self._finish(incident, started)
        accepted = execution_result.get("accepted") is True
        executed = accepted and execution_result.get("executed") is True
        generation = execution_result.get("applied_generation")
        generation = generation if isinstance(generation, int) and not isinstance(generation, bool) and 0 < generation <= (1 << 63) - 1 else None
        digest = execution_result.get("applied_template_sha256")
        digest = digest if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) else None
        backend = execution_result.get("execution_backend")
        backend = backend if isinstance(backend, str) and backend in {"kubernetes", "simulation"} else "unknown"
        replay = execution_result.get("idempotent_replay")
        replay = replay if isinstance(replay, bool) else None
        real_ack = (executed and context is not None and backend == "kubernetes"
                    and replay is False and generation is not None and digest is not None)
        acknowledged_at = datetime.now(UTC).isoformat() if real_ack else None
        self._record(
            incident,
            "tool_result",
            {
                "tool": incident.remediation.tool,
                "dry_run": False,
                "accepted": accepted,
                "executed": executed,
                "execution_backend": backend,
                "applied_generation": generation,
                "applied_template_sha256": digest,
                "idempotent_replay": replay,
                "execution_acknowledged_at": acknowledged_at,
            },
        )
        # A PATCH acknowledgement is not rollout health or incident recovery.
        # A latest-marker replay cannot reconstruct the originally applied template.
        incident.recovery = {"status": "pending", "simulation": context is None,
            "execution_acknowledged_at": acknowledged_at,
            "applied_generation": generation if real_ack else None,
            "applied_template_sha256": digest if real_ack else None,
            "reason_codes": ["recovery_not_checked"], "checked_at": None,
            "metric_evidence_ids": [], "observation_window": None}
        incident.status = IncidentStatus.REMEDIATION_PENDING if accepted else IncidentStatus.REMEDIATION_FAILED
        return self._finish(incident, started)

    def _recovery_operation(self, incident: IncidentState) -> tuple[dict, dict]:
        validate_execution(incident)
        remediation = incident.remediation
        if remediation is None or remediation.tool not in {"restart_deployment", "rollback_release"}:
            raise GuardrailViolation("No approved remediation is available for recovery verification.")
        context = self._approved_target_context(incident)
        intents = [event.payload for event in incident.trace if event.event == "execution_intent_prepared"]
        decisions = [event.payload for event in incident.trace if event.event == "human_decision"]
        if not intents or not decisions:
            raise GuardrailViolation("A durable approved execution intent is required for recovery verification.")
        intent, decision = intents[-1], decisions[-1]
        key = intent.get("idempotency_key")
        if (not isinstance(key, str) or not 8 <= len(key) <= 200
                or decision.get("decision") != "approve" or decision.get("idempotency_key") != key
                or decision.get("actor") != incident.approval_actor or intent.get("actor") != incident.approval_actor
                or intent.get("tool") != remediation.tool or intent.get("arguments") != remediation.arguments):
            raise GuardrailViolation("Recovery verification must match the original approved operation.")
        if self._has_recovery_ack(incident):
            acknowledgements = [event.payload for event in incident.trace
                if event.step == WorkflowStep.EXECUTE and event.event == "tool_result"]
            acknowledgement = acknowledgements[-1] if acknowledgements else {}
            recovery = incident.recovery
            if (acknowledgement.get("tool") != remediation.tool or acknowledgement.get("accepted") is not True
                    or acknowledgement.get("executed") is not True or acknowledgement.get("execution_backend") != "kubernetes"
                    or acknowledgement.get("idempotent_replay") is not False
                    or any(acknowledgement.get(field) != recovery.get(field) for field in
                           ("applied_generation", "applied_template_sha256", "execution_acknowledged_at"))):
                raise GuardrailViolation("Recovery observation must retain the original execution acknowledgement.")
        return {"service": remediation.arguments["service"], "namespace": remediation.arguments["namespace"],
            "tool": remediation.tool, "revision": remediation.arguments.get("revision"),
            "idempotency_key": key, "approval_actor": incident.approval_actor,
            "incident_id": incident.incident_id, "target_context": context}, intent

    @staticmethod
    def _has_recovery_ack(incident: IncidentState) -> bool:
        recovery = incident.recovery or {}
        generation = recovery.get("applied_generation")
        digest = recovery.get("applied_template_sha256")
        try:
            acknowledged = datetime.fromisoformat(recovery.get("execution_acknowledged_at", ""))
        except (TypeError, ValueError):
            return False
        return (recovery.get("simulation") is False and acknowledged.tzinfo is not None
                and acknowledged <= datetime.now(UTC)
                and isinstance(generation, int) and not isinstance(generation, bool) and generation > 0
                and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None)

    async def _verify_recovery(self, graph_state: GraphState) -> GraphState:
        incident, started = self._begin(graph_state, WorkflowStep.VERIFY_RECOVERY)
        if incident.status == IncidentStatus.REMEDIATION_FAILED:
            return self._finish(incident, started)
        operation, _ = self._recovery_operation(incident)
        now = datetime.now(UTC)
        deployment, metrics = {}, []
        error_type = None
        if self._has_recovery_ack(incident):
            acknowledged = datetime.fromisoformat(incident.recovery["execution_acknowledged_at"]).astimezone(UTC)
            start = max(acknowledged, now - timedelta(seconds=60))
            self._record(incident, "tool_call", {"tool": "query_deployment_health",
                "arguments": operation, "read_only": True})
            self._record(incident, "tool_call", {"tool": "query_recovery_metrics",
                "arguments": {"service": operation["service"], "start": start.isoformat(), "end": now.isoformat()},
                "read_only": True})
            try:
                deployment, metrics = await asyncio.wait_for(asyncio.gather(
                    self.tools.query_deployment_health(**operation),
                    self.tools.query_recovery_metrics(operation["service"], start=start, end=now),
                ), timeout=15)
            except (McpToolError, TimeoutError, ValueError, TypeError, AttributeError) as error:
                error_type = type(error).__name__
                deployment, metrics = {}, []
        checked_at = datetime.now(UTC)
        verdict = assess_recovery(incident, deployment, metrics, now=checked_at)
        if error_type:
            verdict["status"] = "pending"
            verdict["reason_codes"] = ["observation_unavailable"]
        if isinstance(metrics, list) and len(metrics) <= 32:
            known_ids = {item.id for item in incident.evidence}
            for item in metrics:
                if isinstance(item, Evidence) and item.id not in known_ids:
                    incident.evidence.append(item)
                    known_ids.add(item.id)
        incident.recovery = {**(incident.recovery or {}), **verdict}
        health_fields = {"source", "read_only", "marker_status", "observed_at", "service", "namespace",
            "cluster_context", "deployment_uid", "generation", "template_sha256", "observed_generation",
            "desired_replicas", "total_replicas", "updated_replicas", "ready_replicas", "available_replicas", "unavailable_replicas"}
        retained_health = {key: value for key, value in deployment.items()
            if key in health_fields and (value is None or type(value) in {int, bool}
                                        or isinstance(value, str) and len(value) <= 128)} if isinstance(deployment, dict) else {}
        incident.recovery["deployment_observation"] = retained_health
        incident.status = (IncidentStatus.RESOLVED if verdict["status"] == "verified"
                           else IncidentStatus.REMEDIATION_PENDING)
        self._record(incident, "recovery_verification", {**verdict,
            "read_only": True, "error_type": error_type,
            "deployment_observation": retained_health,
            "limitation": "Observed rollout and SLIs, not proof that this operation causally fixed the incident."})
        return self._finish(incident, started)

    @staticmethod
    def _route_after_recovery(graph_state: GraphState) -> str:
        incident = LangGraphIncidentWorkflow._incident(graph_state)
        return ("wait" if incident.status == IncidentStatus.REMEDIATION_PENDING
                and LangGraphIncidentWorkflow._has_recovery_ack(incident) else "complete")

    async def _wait_for_recovery(self, graph_state: GraphState) -> GraphState:
        incident = self._incident(graph_state)
        response = interrupt({"kind": "recovery_check", "incident_id": incident.incident_id,
            "read_only": True, "reason": "Recovery has not been verified; another observation never re-executes remediation."})
        if (not isinstance(response, dict) or set(response) != {"kind", "request_actor"}
                or response.get("kind") != "recovery_check" or not isinstance(response.get("request_actor"), str)
                or not 2 <= len(response["request_actor"]) <= 120):
            raise GuardrailViolation("Recovery observation accepts no action or target override.")
        self._recovery_operation(incident)
        incident, started = self._begin(graph_state, WorkflowStep.WAIT_FOR_RECOVERY)
        self._record(incident, "recovery_check_requested", {"read_only": True,
            "request_actor": response["request_actor"], "execution_retried": False})
        return self._finish(incident, started)

    async def _complete(self, graph_state: GraphState) -> GraphState:
        incident, started = self._begin(graph_state, WorkflowStep.COMPLETE)
        if (
            incident.remediation is not None
            and not incident.remediation.requires_approval
            and incident.status != IncidentStatus.INSUFFICIENT_EVIDENCE
        ):
            incident.status = IncidentStatus.DIAGNOSED
        self._record(
            incident,
            "workflow_completed",
            {"executed": any(event.step == WorkflowStep.EXECUTE and event.event == "tool_result"
                             and event.payload.get("executed") is True for event in incident.trace),
             "recovery_verified": incident.status == IncidentStatus.RESOLVED},
        )
        return self._finish(incident, started)

    def _route_after_proposal(self, graph_state: GraphState) -> str:
        incident = self._incident(graph_state)
        if incident.status == IncidentStatus.REMEDIATION_FAILED:
            return "complete"
        if incident.remediation is not None and incident.remediation.requires_approval:
            return "approval"
        return "complete"

    def _route_after_approval(self, graph_state: GraphState) -> str:
        incident = self._incident(graph_state)
        return (
            "execute"
            if incident.approval_decision == ApprovalDecision.APPROVE
            else "complete"
        )

    @staticmethod
    def _has_observed_signals(incident: IncidentState) -> bool:
        return any(
            item.kind.value in {"metric", "log"} and has_usable_observation(item)
            and item.id not in incident.diagnostic_excluded_evidence_ids
            for item in incident.evidence
        )

    @staticmethod
    def _profile(incident: IncidentState) -> IncidentProfile:
        if incident.incident_type is None:
            return classify_question(incident.question)
        return PROFILES[incident.incident_type]

    @staticmethod
    def _supporting_evidence(incident: IncidentState, *, scope_all_kinds: bool = False) -> list[Evidence]:
        profile = LangGraphIncidentWorkflow._profile(incident)
        selected: list[Evidence] = []
        # Prefer operational context before a generic latency-propagation trace.
        # Deployment regressions must retain rollout evidence in the leading set.
        kinds = ("metric", "log", "runbook", "past_incident", "deployment", "trace", "technical_doc")
        if profile.key == "deployment_regression":
            kinds = ("metric", "log", "deployment", "runbook", "past_incident", "trace", "technical_doc")
        for kind in kinds:
            candidates = [
                item for item in incident.evidence
                if item.kind.value == kind and has_usable_observation(item)
                and item.id not in incident.diagnostic_excluded_evidence_ids
            ]
            if incident.services and (scope_all_kinds or kind in {"metric", "log", "deployment"}):
                candidates = [item for item in candidates if item.service == incident.services[0]]
            if candidates:
                if kind == "metric":
                    def metric_rank(item: Evidence) -> tuple[int, float, str]:
                        metric = item.query or item.attributes.get("metric")
                        priority = len(profile.metrics)
                        if metric in profile.metrics:
                            priority = profile.metrics.index(metric)
                        return (-priority, item.score, item.id)
                    chosen = max(candidates, key=metric_rank)
                elif kind == "log":
                    def log_rank(item: Evidence) -> tuple[bool, float, str]:
                        events = item.attributes.get("events", [])
                        messages = [event.get("message", "") for event in events if isinstance(event, dict)] if isinstance(events, list) else []
                        text = " ".join([item.observation, item.query or "", *[message for message in messages if isinstance(message, str)]]).lower()
                        return (profile.log_query.lower() in text, item.score, item.id)
                    chosen = max(candidates, key=log_rank)
                else:
                    chosen = max(candidates, key=lambda item: (item.score, item.id))
                selected.append(chosen)
        return selected

    @staticmethod
    def _previous_revision(incident: IncidentState, *, allow_simulated: bool = False) -> str | None:
        candidates = [
            item for item in incident.evidence
            if item.kind.value == "deployment"
            and item.id not in incident.diagnostic_excluded_evidence_ids
            and incident.services and item.service == incident.services[0]
            and item.score > 0
        ]
        if not candidates:
            return None
        newest = max(candidates, key=lambda item: item.observed_at)
        if not allow_simulated and any(
            item.observed_at == newest.observed_at
            and (item.attributes.get("data_origin") != "kubernetes_api"
                 or item.attributes.get("namespace") != incident.environment)
            for item in candidates
        ):
            return None
        previous = newest.attributes.get("previous_revision")
        if not isinstance(previous, str) or re.fullmatch(r"[a-zA-Z0-9._-]{1,80}", previous) is None:
            return None
        if previous == str(newest.attributes.get("revision", "")):
            return None
        if any(
            item.observed_at == newest.observed_at
            and item.attributes.get("previous_revision") != previous
            for item in candidates
        ):
            return None
        return previous

    def _validate_rollback_target(self, incident: IncidentState) -> None:
        remediation = incident.remediation
        if remediation is None or remediation.tool != "rollback_release":
            return
        allow_simulated = getattr(self.tools, "allow_simulated_deployment_targets", False) is True
        previous = self._previous_revision(incident, allow_simulated=allow_simulated)
        if (previous is None or previous != remediation.arguments.get("revision")
                or remediation.arguments.get("namespace") != incident.environment
                or not incident.services or remediation.arguments.get("service") != incident.services[0]):
            raise GuardrailViolation("Rollback target must match verified deployment evidence and incident scope.")

    def _begin(
        self, graph_state: GraphState, step: WorkflowStep
    ) -> tuple[IncidentState, float]:
        incident = self._incident(graph_state)
        incident.current_step = step
        self._record(incident, "step_started")
        return incident, perf_counter()

    def _finish(self, incident: IncidentState, started: float) -> GraphState:
        self._record(
            incident,
            "step_completed",
            duration_ms=round((perf_counter() - started) * 1_000),
        )
        incident.updated_at = datetime.now(UTC)
        return {"incident": incident.model_dump(mode="json")}

    def _record(
        self,
        incident: IncidentState,
        event: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        incident.trace.append(
            TraceEvent(
                sequence=len(incident.trace) + 1,
                step=incident.current_step,
                event=event,
                payload=payload or {},
                duration_ms=duration_ms,
            )
        )

    @staticmethod
    def _incident(graph_state: GraphState) -> IncidentState:
        cleaned = {
            key: value
            for key, value in graph_state["incident"].items()
            if not key.startswith("_")
        }
        return IncidentState.model_validate(cleaned)

    @staticmethod
    def _config(incident_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": incident_id}}
