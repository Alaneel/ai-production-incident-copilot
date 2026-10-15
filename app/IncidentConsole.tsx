"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { refreshApproval, sendApproval } from "./approval-recovery";
import { consoleApprovalProblem, consoleAuthorizationProblem, consoleBackendEndpoint,
  createConsoleTransport, isLocalDemoBackend, type ConsoleCredentials } from "./console-auth";
import { streamConsoleIncident } from "./incident-stream";
import { incidentLocationUrl, readIncidentLocation, restoredApprovalUncertain } from "./incident-location";
import { evidenceAnchor, resolveCitations } from "./evidence-citations";
import { EvidenceExplorer } from "./EvidenceExplorer";
import { ActionEvidenceReview } from "./ActionEvidenceReview";
import { PreflightTargetReview } from "./PreflightTargetReview";
import { preflightTargetReview } from "./preflight-target-review";
import { DeploymentEventTimeline } from "./DeploymentEventTimeline";
import { DependencyImpactList } from "./DependencyImpactList";
import { OperationInspectionPanel } from "./OperationInspectionPanel";
import { RecoveryVerificationPanel } from "./RecoveryVerificationPanel";
import { recoveryVerificationView } from "./recovery-verification";
import { RootCauseCandidates } from "./RootCauseCandidates";
import type { ServiceImpact, ServiceTopology } from "./dependency-impacts";

type TraceState = "complete" | "running" | "queued";

type ApiIncident = {
  incident_id: string;
  trace_id: string;
  question: string;
  status:
    | "investigating"
    | "waiting_approval"
    | "mitigating"
    | "diagnosed"
    | "insufficient_evidence"
    | "remediation_failed"
    | "remediation_pending"
    | "resolved"
    | "rejected";
  current_step: string;
  incident_type: string | null;
  candidate_incident_types?: string[];
  service_impacts?: ServiceImpact[];
  service_topology?: ServiceTopology | null;
  service_topologies?: ServiceTopology[];
  complaint_service?: string | null;
  diagnosis_service?: string | null;
  root_cause_candidates?: unknown[];
  diagnostic_excluded_evidence_ids?: string[];
  severity: string | null;
  anomalous_window: [string, string] | null;
  created_at: string;
  recovery?: unknown;
  services: string[];
  evidence: Array<{
    id: string;
    kind: string;
    service?: string;
    source: string;
    title: string;
    observation: string;
    observed_at: string;
    query: string | null;
    attributes: Record<string, unknown>;
  }>;
  hypotheses: Array<{
    id?: string;
    rank?: number;
    cause: string;
    confidence: number;
    evidence_ids: string[];
    cause_service?: string | null;
    propagation_path?: string[];
    causal_direction_verified?: false;
  }>;
  remediation: {
    title: string;
    tool: string;
    command_preview: string;
    dry_run_passed: boolean;
    requires_approval: boolean;
    expected_result: string;
    preflight_mode?: "simulation" | "kubernetes-server-dry-run" | null;
    arguments?: Record<string, unknown>;
  } | null;
  trace: Array<{
    sequence: number;
    step: string;
    event: string;
    duration_ms: number | null;
    payload: Record<string, unknown>;
  }>;
};

const orchestratorUrl = process.env.NEXT_PUBLIC_ORCHESTRATOR_URL?.replace(/\/$/, "");

function retainLocation(incidentId: string, approvalPending: boolean, diagnosisPending = false) {
  const url = new URL(incidentLocationUrl(window.location.href, incidentId, approvalPending));
  if (diagnosisPending) url.searchParams.set("diagnosis_pending", "1"); else url.searchParams.delete("diagnosis_pending");
  window.history.replaceState(null, "", url.toString());
}

function forgetLocation() {
  const url = new URL(window.location.href);
  url.searchParams.delete("incident"); url.searchParams.delete("approval_pending"); url.searchParams.delete("diagnosis_pending");
  window.history.replaceState(null, "", url.toString());
}

const traceSteps: Array<{
  title: string;
  detail: string;
  state: TraceState;
  duration?: string;
}> = [
  {
    title: "Classify incident",
    detail: "Latency regression · severity SEV-2",
    state: "complete",
    duration: "0.3s",
  },
  {
    title: "Discover blast radius",
    detail: "payment-service → ledger-db",
    state: "complete",
    duration: "0.8s",
  },
  {
    title: "Correlate telemetry",
    detail: "12 metric series · 184 log events",
    state: "complete",
    duration: "2.1s",
  },
  {
    title: "Retrieve operational context",
    detail: "2 runbooks · 1 similar incident",
    state: "complete",
    duration: "0.9s",
  },
  {
    title: "Validate hypotheses",
    detail: "Cross-checking deploy and pool saturation",
    state: "running",
  },
  {
    title: "Propose remediation",
    detail: "Awaiting validated evidence",
    state: "queued",
  },
];

const evidence = [
  {
    id: "demo-metric",
    icon: "M",
    source: "Prometheus",
    title: "Connection pool saturation",
    detail: "active_connections = 50/50 from 14:07",
    time: "14:07:12",
  },
  {
    id: "demo-log",
    icon: "L",
    source: "Loki",
    title: "Acquire timeout burst",
    detail: "db.pool.timeout increased 38×",
    time: "14:07:16",
  },
  {
    id: "demo-deployment",
    icon: "D",
    source: "Deployments",
    title: "Release payment-v142",
    detail: "DB query fan-out changed 11 min earlier",
    time: "13:56:03",
  },
];

const bars = [18, 19, 18, 20, 22, 24, 26, 29, 35, 58, 83, 91, 88, 94, 92, 89];
const errors = [4, 3, 4, 4, 5, 4, 5, 7, 8, 13, 33, 46, 49, 44, 42, 38];

function MetricChart({
  label,
  value,
  delta,
  data,
  tone,
  axis = ["13:45", "deploy 13:56", "14:20"],
}: {
  label: string;
  value: string;
  delta: string;
  data: number[];
  tone: "cyan" | "amber";
  axis?: string[];
}) {
  return (
    <article className="metric-card">
      <div className="metric-heading">
        <div>
          <span className="eyebrow">{label}</span>
          <strong>{value}</strong>
        </div>
        <span className={`delta ${tone}`}>{delta}</span>
      </div>
      <div className={`bar-chart ${tone}`} aria-label={`${label} trend`}>
        {data.map((height, index) => (
          <span key={index} style={{ height: `${height}%` }} />
        ))}
        {data.length > 0 && <i className="incident-marker" />}
      </div>
      <div className="chart-axis">
        {axis.map((label, index) => <span key={index}>{label}</span>)}
      </div>
    </article>
  );
}

export function IncidentConsole({ operatorToken }: { operatorToken?: string } = {}) {
  const [prompt, setPrompt] = useState("为什么 payment-service 今天延迟突然升高？");
  const [actionState, setActionState] = useState<"pending" | "approved" | "rejected">(
    "pending",
  );
  const [activeTab, setActiveTab] = useState<"timeline" | "trace">("timeline");
  const [incident, setIncident] = useState<ApiIncident | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isApproving, setIsApproving] = useState(false);
  const [isRestoring, setIsRestoring] = useState(false);
  const [restoredIncidentId, setRestoredIncidentId] = useState<string | null>(null);
  const [approvalUncertain, setApprovalUncertain] = useState(false);
  const requestInFlight = useRef(false);
  const approvalKeys = useRef(new Map<string, string>());
  const [apiError, setApiError] = useState<string | null>(null);
  const [authMode, setAuthMode] = useState<ConsoleCredentials["mode"]>("protected");
  const [trustedEndpoint, setTrustedEndpoint] = useState<string | null>(null);
  const [readCredential, setReadCredential] = useState("");
  const [operatorCredential, setOperatorCredential] = useState("");
  const [operatorActor, setOperatorActor] = useState("");
  const pendingApprovalMarker = useRef(false);
  const [diagnosisPhase, setDiagnosisPhase] = useState<"idle" | "running" | "complete" | "interrupted" | "blocked" | "restored">("idle");
  const [diagnosisRequiresRefresh, setDiagnosisRequiresRefresh] = useState(false);
  const [diagnosisQuestion, setDiagnosisQuestion] = useState<string | null>(null);
  const diagnosisEpoch = useRef(0);
  const activeDiagnosis = useRef<AbortController | null>(null);
  const backendEndpoint = consoleBackendEndpoint(orchestratorUrl);
  const credentials = useMemo<ConsoleCredentials>(() => ({ mode: authMode, trustedEndpoint,
    readToken: readCredential, operatorToken: authMode === "protected" ? operatorToken ?? operatorCredential : "",
    operatorActor }), [authMode, trustedEndpoint, readCredential, operatorToken, operatorCredential, operatorActor]);
  const apiTransport = useMemo(() => createConsoleTransport(orchestratorUrl, credentials), [credentials]);
  const readAccessError = consoleAuthorizationProblem(orchestratorUrl, credentials, "read");
  const operatorAccessError = consoleAuthorizationProblem(orchestratorUrl, credentials, "operator");
  const approvalAccessError = consoleApprovalProblem(orchestratorUrl, credentials);
  const citations = useMemo(() => resolveCitations(incident?.evidence ?? [], incident?.hypotheses[0]?.evidence_ids ?? []), [incident]);
  const citationProblem = citations.missing.length > 0 || citations.ambiguous.length > 0;
  const preflightReview = useMemo(() => preflightTargetReview(incident), [incident]);
  const recoveryReview = useMemo(() => recoveryVerificationView(incident), [incident]);
  const showDemo = !incident && diagnosisPhase === "idle";

  useEffect(() => {
    let active = true;
    // Read external navigation after hydration; no automatic credential-bearing
    // fetch is made while credentials/trust are still unset.
    queueMicrotask(() => {
      if (!active) return;
      try {
        const location = readIncidentLocation(window.location.href);
        if (!location) return;
        setRestoredIncidentId(location.incidentId);
        pendingApprovalMarker.current = location.approvalPending;
        if (new URL(window.location.href).searchParams.has("diagnosis_pending")) {
          setDiagnosisRequiresRefresh(true); setDiagnosisPhase("interrupted");
        }
        setApprovalUncertain(true);
        setApiError("Enter reader credentials or explicitly choose the local demo, trust the backend, then restore this incident. No request or action was submitted.");
      } catch {
        setApiError("Invalid incident link; no incident was loaded.");
      }
    });
    return () => { active = false; diagnosisEpoch.current += 1; activeDiagnosis.current?.abort(); activeDiagnosis.current = null; };
  }, []);

  function interruptDiagnosis() {
    if (!activeDiagnosis.current) return;
    diagnosisEpoch.current += 1;
    activeDiagnosis.current.abort(); activeDiagnosis.current = null;
    requestInFlight.current = false;
    setIsAnalyzing(false); setDiagnosisPhase("interrupted"); setDiagnosisRequiresRefresh(true);
    setApiError("Diagnostic stream was cancelled. Its completion is unconfirmed; refresh the retained incident read-only. No diagnosis was replayed.");
  }
  function clearCredentials() {
    interruptDiagnosis();
    setReadCredential(""); setOperatorCredential(""); setOperatorActor(""); setTrustedEndpoint(null);
  }
  function changeAuthorizationMode(mode: ConsoleCredentials["mode"]) {
    clearCredentials(); setAuthMode(mode);
  }

  const actionCopy = useMemo(() => {
    if (incident?.status === "rejected" || (!incident && actionState === "rejected")) {
      return { title: "Action rejected", body: "No production change was made. The decision is attached to the incident audit trail." };
    }
    if (actionState === "approved" || (incident && ["resolved", "remediation_failed", "remediation_pending"].includes(incident.status))) {
      if (incident?.status === "remediation_failed") {
        return {
          title: "Remediation failed",
          body: "The tool did not accept the operation. Review the incident trace before retrying.",
        };
      }
      if (incident?.status === "remediation_pending") {
        if (recoveryReview.mode === "simulation") return {
          title: "Simulation — live recovery unverified",
          body: "The simulated operation cannot establish a Kubernetes rollout or production SLI recovery.",
        };
        if (recoveryReview.mode === "pending" && recoveryReview.canCheck) return {
          title: "Recovery verification pending",
          body: "Execution request was acknowledged. Applied rollout and sustained SLI recovery have not yet been verified.",
        };
        return {
          title: "Execution unconfirmed",
          body: "Execution has not been confirmed. Verify the operation outcome before another execution attempt.",
        };
      }
      return {
        title: "Execution approved",
        body: incident
          ? "The named approval and reported execution outcome are recorded in the incident trace."
          : "Demo choice only. No production change was submitted.",
      };
    }
    if (actionState === "rejected") {
      return {
        title: "Action rejected",
        body: "No production change was made. The decision is attached to the incident audit trail.",
      };
    }
    return null;
  }, [actionState, incident, recoveryReview]);

  const displayEvidence = useMemo(() => {
    if (!incident) return showDemo ? evidence : [];
    return citations.cited.map((item) => ({
      id: item.id,
      icon: item.kind.slice(0, 1).toUpperCase(),
      source: item.source,
      title: item.title,
      detail: item.observation,
      time: new Date(item.observed_at).toLocaleTimeString("en-SG", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }),
    }));
  }, [incident, citations, showDemo]);

  async function submitQuestion(event: React.FormEvent) {
    event.preventDefault();
    if (!prompt.trim() || requestInFlight.current || approvalUncertain) return;
    if (!orchestratorUrl) {
      setActiveTab("trace");
      return;
    }
    if (readAccessError) { setApiError(readAccessError); return; }
    if (diagnosisRequiresRefresh && prompt === diagnosisQuestion) {
      setApiError("This diagnosis may already exist. Refresh its retained state read-only; the diagnosis POST will not be replayed."); return;
    }
    activeDiagnosis.current?.abort();
    const controller = new AbortController(), epoch = ++diagnosisEpoch.current;
    activeDiagnosis.current = controller;
    const question = prompt;
    requestInFlight.current = true;
    setIsAnalyzing(true);
    setDiagnosisPhase("running"); setDiagnosisRequiresRefresh(true); setDiagnosisQuestion(question);
    setIncident(null); setRestoredIncidentId(null); pendingApprovalMarker.current = false; forgetLocation();
    setApiError(null);
    setActionState("pending");
    try {
      const result = await streamConsoleIncident<ApiIncident>(orchestratorUrl, question, apiTransport, controller.signal, snapshot => {
        if (epoch !== diagnosisEpoch.current || controller.signal.aborted) return;
        setIncident(snapshot); setRestoredIncidentId(snapshot.incident_id); retainLocation(snapshot.incident_id, false, true);
      });
      if (epoch !== diagnosisEpoch.current || controller.signal.aborted) return;
      if (result.incidentId) { setRestoredIncidentId(result.incidentId); retainLocation(result.incidentId, false, !result.complete); }
      setDiagnosisPhase(result.complete ? "complete" : result.uncertain ? "interrupted" : "blocked");
      setDiagnosisRequiresRefresh(result.uncertain); setApiError(result.error);
    } catch {
      if (epoch !== diagnosisEpoch.current || controller.signal.aborted) return;
      setDiagnosisPhase("interrupted"); setDiagnosisRequiresRefresh(true);
      setApiError("Diagnosis completion is unconfirmed. Refresh retained state read-only; no diagnosis POST or demo fallback was retried.");
    } finally {
      if (epoch === diagnosisEpoch.current) {
        activeDiagnosis.current = null; requestInFlight.current = false; setIsAnalyzing(false);
      }
    }
  }

  async function decide(decision: "approve" | "reject") {
    if (requestInFlight.current || approvalUncertain || citationProblem || diagnosisRequiresRefresh) return;
    if (decision === "approve" && !preflightReview.known) return;
    if (!orchestratorUrl || !incident) {
      setActionState(decision === "approve" ? "approved" : "rejected");
      return;
    }
    if (approvalAccessError) { setApiError(approvalAccessError); return; }
    if (incident.status !== "waiting_approval" || !incident.remediation?.dry_run_passed) return;
    requestInFlight.current = true;
    setIsApproving(true);
    const keyScope = `${incident.incident_id}-${decision}`;
    if (!approvalKeys.current.has(keyScope)) {
      approvalKeys.current.set(keyScope, `ui-${keyScope}-${crypto.randomUUID()}`);
    }
    setApiError(null);
    try {
      // This marker must survive reload before any mutating request is sent.
      pendingApprovalMarker.current = true;
      retainLocation(incident.incident_id, true);
      const result = await sendApproval<ApiIncident>(orchestratorUrl, incident.incident_id, {
        decision,
        actor: credentials.operatorActor,
        reason:
          decision === "approve"
            ? "Evidence citations and dry-run reviewed in OpsPilot."
            : "Operator rejected the proposed production change.",
        idempotency_key: approvalKeys.current.get(keyScope),
      }, apiTransport);
      if (result.incident) setIncident(result.incident);
      setApprovalUncertain(result.uncertain);
      setApiError(result.error);
      if (!result.uncertain) { pendingApprovalMarker.current = false; retainLocation(incident.incident_id, false); }
      // The retained backend status, not the attempted decision, defines the outcome.
      if (!result.uncertain) setActionState("pending");
    } catch {
      setApprovalUncertain(true);
      setApiError("Approval outcome could not be confirmed. Only read-only refresh is safe; no decision was replayed.");
    } finally {
      requestInFlight.current = false;
      setIsApproving(false);
    }
  }

  async function refreshDecision() {
    const incidentId = incident?.incident_id ?? restoredIncidentId;
    if (!orchestratorUrl || !incidentId || requestInFlight.current) return;
    if (readAccessError) { setApiError(readAccessError); return; }
    requestInFlight.current = true;
    setIsRestoring(true);
    try {
      const result = await refreshApproval<ApiIncident>(orchestratorUrl, incidentId, apiTransport);
      if (result.incident) { setIncident(result.incident); setPrompt(result.incident.question); }
      if (result.incident) {
        if (diagnosisRequiresRefresh) {
          // A state GET cannot prove that the original stream reached its
          // final checkpoint or LangGraph's genuine approval interrupt.
          setDiagnosisQuestion(result.incident.question); setDiagnosisPhase("interrupted");
        } else setDiagnosisPhase("restored");
      }
      const uncertain = restoredApprovalUncertain(result.incident?.status ?? null, pendingApprovalMarker.current);
      setApprovalUncertain(uncertain);
      setApiError(result.error);
      if (!uncertain) { pendingApprovalMarker.current = false; retainLocation(incidentId, false, diagnosisRequiresRefresh); }
    } finally {
      requestInFlight.current = false;
      setIsRestoring(false);
    }
  }

  const incidentStatus = incident?.status.replaceAll("_", " ") ?? "Investigating";
  const retainedSteps = incident
    ? [...new Map(incident.trace.map((event) => [event.step, event])).values()].map((event) => ({
        title: event.step.replaceAll("_", " "),
        detail: `Persisted: ${event.event.replaceAll("_", " ")}`,
        state: "complete" as TraceState,
        duration: event.duration_ms === null ? undefined : `${event.duration_ms}ms`,
      }))
    : isAnalyzing ? [{ title: "Start diagnostic workflow", detail: "Waiting for the first persisted checkpoint", state: "running" as TraceState }] : showDemo ? traceSteps : [];
  if (isAnalyzing && incident) retainedSteps.push({ title: "Diagnosis still running", detail: "Awaiting the next persisted checkpoint", state: "running" });
  function metricView(metric: string, percentage = false) {
    const item = incident?.evidence.find((entry) => entry.kind === "metric" && entry.query === metric);
    const raw = item?.attributes.series;
    const points = Array.isArray(raw) ? raw.filter((point): point is { timestamp: string; value: number } =>
      point && typeof point.value === "number" && Number.isFinite(point.value) &&
      typeof point.timestamp === "string" && Number.isFinite(Date.parse(point.timestamp))) : [];
    if (!points.length) return { value: "Unavailable", delta: "No samples", data: [], axis: ["No timestamped metric samples"] };
    const peak = Math.max(...points.map((point) => point.value), 0);
    const last = points[points.length - 1].value;
    const time = (value: string) => new Date(value).toLocaleTimeString("en-SG", { hour12: false });
    return {
      value: percentage ? `${(last * 100).toFixed(1)}%` : `${last.toFixed(0)}ms`,
      delta: "Latest sample",
      data: points.map((point) => peak > 0 ? Math.max(0, point.value / peak * 100) : 0),
      axis: [time(points[0].timestamp), time(points[points.length - 1].timestamp)],
    };
  }
  const primaryService = incident?.services[0] ?? (showDemo ? "payment-service" : "Service not selected");
  const dependencyServices = incident?.services.slice(1) ?? [
    "checkout-api",
    "ledger-db",
    "risk-engine",
  ];
  const incidentTitle = incident
    ? isAnalyzing ? `${primaryService} diagnostic progress`
      : diagnosisRequiresRefresh ? `${primaryService} diagnosis unconfirmed`
        : `${primaryService} ${incident.incident_type?.replaceAll("_", " ") ?? "incident"}`
    : showDemo ? "Payment latency regression" : "Diagnosis — final result not confirmed";
  const leadingHypothesis =
    incident
      ? incident.hypotheses[0]?.cause ?? (isAnalyzing || diagnosisRequiresRefresh
        ? "Awaiting retained diagnostic evidence and hypotheses" : "Insufficient evidence to identify a root cause")
      : showDemo ? "Database connection pool exhaustion after release v142" : "Awaiting retained diagnostic evidence";
  const confidence = Math.round((incident ? incident.hypotheses[0]?.confidence ?? 0 : 0.87) * 100);
  const remediationTitle =
    incident?.remediation?.title ?? (showDemo ? "Rollback payment-service to v141" : "No remediation proposed yet");
  const commandPreview =
    incident?.remediation?.command_preview ??
    "No remediation selected; investigate an incident first.";
  const expectedResult =
    incident?.remediation?.expected_result ??
    (showDemo ? "Restore the previous query pattern and drain waiters." : "No final action recommendation is available.");
  const requiresApproval = incident?.remediation?.requires_approval ?? true;
  const actionLabel =
    incident?.remediation?.tool === "restart_deployment"
      ? "Approve restart"
      : "Approve rollback";

  function resetDecision() {
    if (
      incident?.status === "resolved" ||
      incident?.status === "rejected" ||
      incident?.status === "diagnosed" ||
      incident?.status === "insufficient_evidence" ||
      incident?.status === "remediation_failed" ||
      incident?.status === "remediation_pending"
    ) {
      return;
    }
    setActionState("pending");
  }

  function openTrace() {
    setActiveTab("trace");
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            O
          </span>
          <div>
            <strong>OpsPilot</strong>
            <span>INCIDENT INTELLIGENCE</span>
          </div>
        </div>
        <div className="topbar-center">
          <span className="live-dot" />
          {incident ? isAnalyzing ? "DIAGNOSTIC CHECKPOINT" : "RETAINED INCIDENT" : showDemo ? "DEMO INCIDENT" : "DIAGNOSIS UNCONFIRMED"}
          <b>{incident?.incident_id ?? (showDemo ? "INC-2847" : "Waiting for incident reference")}</b>
        </div>
        <div className="topbar-actions">
          <button className="icon-button" aria-label="Search">
            /
          </button>
          <button className="env-button">
            <span className="status-dot" />
            prod-sg
            <span aria-hidden="true">⌄</span>
          </button>
          <button className="avatar" aria-label="Account menu">
            AW
          </button>
        </div>
      </header>

      <section className="incident-header">
        <div>
          <div className="incident-labels">
            <span className="severity">{incident ? incident.severity ?? "Unclassified" : showDemo ? "SEV-2" : "Unclassified"}</span>
            <span className="investigating">
              <i /> {incidentStatus}
            </span>
            <span>{incident ? `Created ${new Date(incident.created_at).toLocaleString("en-SG")}` : showDemo ? "Started 14:07 SGT · 18m elapsed" : "Awaiting incident checkpoint"}</span>
          </div>
          <h1>{incidentTitle}</h1>
          <p>
            {incident
              ? `Evidence-first diagnosis for ${primaryService}; ${incident.evidence.length} retained signals.`
              : showDemo ? "Elevated p95 latency and error rate in the checkout payment path." : "No completed diagnosis has been received. No demo data is substituted."}
          </p>
        </div>
        <div className="header-meta">
          <div>
            <span>OWNER</span>
            <strong>{incident || !showDemo ? "Unassigned" : <><i className="owner-avatar">JL</i> Jane Liu</>}</strong>
          </div>
          <div>
            <span>AFFECTED</span>
            <strong>{primaryService}</strong>
          </div>
        </div>
      </section>

      <div className="workspace">
        <section className="main-column">
          <section className="panel" aria-label="Local protected API connection">
            <h2>Local API connection — not SSO</h2>
            <p>Development integration only. Use a trusted server-side identity gateway for production; never paste production secrets here.</p>
            <label htmlFor="console-auth-mode">Authorization mode</label>{" "}
            <select id="console-auth-mode" value={authMode} disabled={isApproving || isRestoring}
              onChange={event => changeAuthorizationMode(event.target.value as ConsoleCredentials["mode"])}>
              <option value="protected">Protected API credentials</option>
              <option value="local-demo" disabled={!isLocalDemoBackend(orchestratorUrl)}>Unauthenticated local demo</option>
            </select>
            {authMode === "local-demo" ? <p role="alert">Explicit HTTP loopback demo only. Requests carry no Bearer credential. The backend must have auth disabled; this does not verify human identity or authenticated approval. A 401/403 never switches modes.</p>
              : <p>The backend must use required authorization. Reader credentials diagnose and read; operator credentials approve and inspect. Credentials are not human SSO.</p>}
            <p>Configured incident backend: <code>{backendEndpoint ?? "Unavailable or unsafe URL"}</code>. No editable endpoint is accepted.</p>
            <label><input type="checkbox" checked={backendEndpoint !== null && trustedEndpoint === backendEndpoint}
              disabled={!backendEndpoint || isApproving || isRestoring}
              onChange={event => { if (event.target.checked) setTrustedEndpoint(backendEndpoint); else clearCredentials(); }} />
              I trust this exact backend for these development requests and credentials.</label>
            {authMode === "protected" && <>
              <p><label htmlFor="console-read-token">Reader API token</label>{" "}
                <input id="console-read-token" type="password" autoComplete="off" value={readCredential}
                  disabled={isApproving || isRestoring} onChange={event => { interruptDiagnosis(); setReadCredential(event.target.value); }} /></p>
              {operatorToken === undefined && <p><label htmlFor="console-operator-token">Operator API token</label>{" "}
                <input id="console-operator-token" type="password" autoComplete="off" value={operatorCredential}
                  disabled={isApproving || isRestoring} onChange={event => { interruptDiagnosis(); setOperatorCredential(event.target.value); }} /></p>}
            </>}
            <p><label htmlFor="console-operator-actor">{authMode === "protected" ? "Operator actor configured for this backend" : "Local demo audit actor (unverified)"}</label>{" "}
              <input id="console-operator-actor" autoComplete="off" value={operatorActor} maxLength={120}
                disabled={isApproving || isRestoring} onChange={event => { interruptDiagnosis(); setOperatorActor(event.target.value); }} />
              {" "}{authMode === "protected" ? "The backend validates this binding; entering a name is not identity verification."
                : "The demo records this supplied name without authenticating a person or binding it to a credential."}</p>
            <p>Tokens stay only in this page&apos;s memory, never in browser storage, URLs, incident payloads or traces. Reload or clear the connection to remove them.</p>
            <button type="button" disabled={isApproving || isRestoring} onClick={clearCredentials}>Clear credentials and trust</button>
            {(incident || restoredIncidentId) && <button type="button" disabled={isAnalyzing || isApproving || isRestoring || Boolean(readAccessError)}
              onClick={() => void refreshDecision()}>Restore / refresh incident (read-only)</button>}
            {orchestratorUrl && readAccessError && <p role="status">{readAccessError}</p>}
          </section>
          <form className="question-box" onSubmit={submitQuestion}>
            <label htmlFor="incident-question">ASK THE INCIDENT COPILOT</label>
            <div>
              <span className="prompt-glyph" aria-hidden="true">›_</span>
              <input
                id="incident-question"
                value={prompt}
                onChange={(event) => { interruptDiagnosis(); setPrompt(event.target.value); }}
                aria-label="Incident question"
                disabled={isApproving || isRestoring || approvalUncertain}
              />
              <button type="submit" disabled={isAnalyzing || isApproving || isRestoring || approvalUncertain || (diagnosisRequiresRefresh && prompt === diagnosisQuestion) || Boolean(orchestratorUrl && readAccessError)}>Analyze <kbd>↵</kbd></button>
            </div>
          </form>
          {isAnalyzing && (
            <div className="api-notice running" role="status">Receiving persisted diagnostic checkpoints… Evidence and tool progress below are provisional; no final completion or approval is confirmed.</div>
          )}
          {diagnosisPhase === "complete" && <div className="api-notice connected" role="status">Diagnostic stream completed. This is a diagnosis result, not proof of execution or recovery.</div>}
          {diagnosisPhase === "interrupted" && <div className="api-notice error" role="status">Diagnostic completion unconfirmed. Use read-only restore / refresh; the same diagnosis POST cannot be replayed here. {restoredIncidentId ? `Retained incident: ${restoredIncidentId}.` : "No incident reference has been confirmed."}</div>}
          {diagnosisPhase === "restored" && <div className="api-notice connected" role="status">Retained state recovered by read-only GET. No diagnostic POST or production action was resubmitted.</div>}
          {isApproving && (
            <div className="api-notice running" role="status">Recording the decision and waiting for the guarded execution result…</div>
          )}
          {isRestoring && (
            <div className="api-notice running" role="status">Restoring retained incident state. No operation is being submitted…</div>
          )}
          {apiError && (
            <div className="api-notice error" role="alert">{apiError}</div>
          )}
          {incident && (
            <div className="api-notice connected">
              API checkpoint · {incident.incident_id} · {incident.trace.length} retained events{isAnalyzing ? " · diagnosis still running" : diagnosisRequiresRefresh ? " · completion unconfirmed" : ""}
            </div>
          )}

          <div className="tab-row">
            <button
              className={activeTab === "timeline" ? "active" : ""}
              onClick={() => setActiveTab("timeline")}
            >
              Incident overview
            </button>
            <button
              className={activeTab === "trace" ? "active" : ""}
              onClick={() => setActiveTab("trace")}
            >
              Agent trace <span>{incident?.trace.length ?? 6}</span>
            </button>
          </div>

          {activeTab === "timeline" ? (
            <>
              <section className="metrics-grid">
                <MetricChart
                  label="P95 LATENCY"
                  {...(showDemo ? { value: "2.84s", delta: "+341%", data: bars } : metricView("request_latency_p95"))}
                  tone="amber"
                />
                <MetricChart
                  label="ERROR RATE"
                  {...(showDemo ? { value: "12.7%", delta: "+11.4pp", data: errors } : metricView("request_error_rate", true))}
                  tone="cyan"
                />
              </section>

              <section className="panel dependency-panel">
                <div className="panel-title">
                  <div>
                    <span className="eyebrow">SERVICE MAP</span>
                    <h2>Dependency blast radius</h2>
                  </div>
                  <span className="small-status">
                    {incident?.services.length ?? (showDemo ? 4 : 0)} services {showDemo ? "inspected (demo)" : "discovered"}
                  </span>
                </div>
                {incident ? <DependencyImpactList services={incident.services} impacts={incident.service_impacts ?? []} evidence={incident.evidence} topology={incident.service_topology} /> : showDemo ? <div className="service-map">
                  <div className={`service-node ${incident ? "" : "healthy"} checkout-node`}>
                    <i>C</i>
                    <div>
                      <strong>{dependencyServices[0] ?? "upstream"}</strong>
                      <span>dependency</span>
                    </div>
                  </div>
                  <span className="connector connector-one" />
                  <div className={`service-node ${incident ? "" : "critical"} payment-node`}>
                    <i>P</i>
                    <div><strong>{primaryService}</strong><span>{incident ? "selected service" : "primary signal"}</span></div>
                    <b>{incident ? metricView("request_error_rate", true).value : "12.7%"}</b>
                  </div>
                  <span className="connector connector-two" />
                  <div className={`service-node ${incident ? "" : "warning"} ledger-node`}>
                    <i>DB</i>
                    <div>
                      <strong>{dependencyServices[1] ?? "downstream"}</strong>
                      <span>dependency</span>
                    </div>
                  </div>
                  <span className="connector connector-three" />
                  <div className={`service-node ${incident ? "" : "healthy"} risk-node`}>
                    <i>R</i>
                    <div>
                      <strong>{dependencyServices[2] ?? "supporting service"}</strong>
                      <span>dependency</span>
                    </div>
                  </div>
                </div> : <p>No service topology checkpoint has been received.</p>}
              </section>

              {incident && <RootCauseCandidates incident={incident} />}

              <section className="panel hypothesis-panel">
                <div className="hypothesis-top">
                  <div className="confidence-ring">
                    <strong>{!showDemo && (!incident?.hypotheses.length || (incident.candidate_incident_types?.length ?? 0) > 1) ? "—" : confidence}</strong><span>{!showDemo && (!incident?.hypotheses.length || (incident.candidate_incident_types?.length ?? 0) > 1) ? "" : "%"}</span>
                  </div>
                  <div>
                    <span className="eyebrow">LEADING HYPOTHESIS</span>
                    <h2>{leadingHypothesis}</h2>
                  </div>
                  <span className="validated">
                    {incident?.status === "insufficient_evidence" ? "More telemetry needed" : "Evidence retained"}
                  </span>
                </div>
                <p>
                  {incident?.status === "insufficient_evidence"
                    ? incident.remediation?.expected_result ?? "Retained telemetry is insufficient to establish a root cause."
                    : incident
                    ? `The leading explanation references ${citations.cited.length} uniquely resolved evidence items. These citations can be inspected below.`
                    : showDemo ? "Release payment-v142 increased parallel ledger lookups. Active connections reached the configured ceiling 11 minutes later, causing request queueing and cascading checkout timeouts." : "Waiting for retained evidence; no completed root-cause hypothesis is available."}
                </p>
                {incident && (
                  <p>
                    {incident.anomalous_window
                      ? `Observed anomaly samples: ${new Date(incident.anomalous_window[0]).toLocaleString("en-SG")} — ${new Date(incident.anomalous_window[1]).toLocaleString("en-SG")}.`
                      : "No anomalous interval established from timestamped metric samples."}
                  </p>
                )}
                <div className="evidence-list">
                  {displayEvidence.map((item) => (
                    <article key={item.id}>
                      <i>{item.icon}</i>
                      <div>
                        <span>{item.source}</span>
                        <strong>{item.title}</strong>
                        {incident && <a href={`#${evidenceAnchor(item.id)}`}>{item.id}</a>}
                        <small>{item.detail}</small>
                      </div>
                      <time>{item.time}</time>
                    </article>
                  ))}
                </div>
                {incident && !incident.hypotheses.length && <p>No root-cause citations exist. Retained observations below are context only.</p>}
                {citationProblem && <p role="alert">Citation integrity failed. Missing IDs: {citations.missing.join(", ") || "none"}; ambiguous IDs: {citations.ambiguous.join(", ") || "none"}. Approval is blocked.</p>}
                {(incident?.candidate_incident_types?.length ?? 0) > 1 && (
                  <div>
                    <h3>Competing hypotheses — causal order unresolved</h3>
                    <p>Several observed failure patterns match. The order below is not a calibrated confidence ranking. No production mutation is proposed.</p>
                    <ul>{incident?.hypotheses.map((hypothesis, index) => <li key={index}>
                      <strong>{hypothesis.cause}</strong>{" · "}
                      {hypothesis.evidence_ids.map(id => <a key={id} href={`#${evidenceAnchor(id)}`}>{id}{" "}</a>)}
                    </li>)}</ul>
                  </div>
                )}
              </section>

              {incident && <DeploymentEventTimeline services={incident.services} evidence={incident.evidence} window={incident.anomalous_window} />}
              {incident && <EvidenceExplorer evidence={incident.evidence} citedIds={citations.cited.map(item => item.id)} />}

              <section className="panel remediation-panel">
                <div className="remediation-copy">
                  <span className="eyebrow">RECOMMENDED REMEDIATION</span>
                  <h2>{remediationTitle}</h2>
                  <p>{expectedResult}</p>
                  <div className="command-preview">
                    <span>$</span>
                    <code>{commandPreview}</code>
                    <b>{!requiresApproval ? "ADVISORY" : preflightReview.mode === "simulation" ? "SIMULATION"
                      : preflightReview.mode === "kubernetes-server-dry-run" ? "SERVER DRY RUN" : "PREFLIGHT UNKNOWN"}</b>
                  </div>
                  {incident && <ActionEvidenceReview incident={incident} />}
                  {incident && incident.remediation && ["restart_deployment", "rollback_release"].includes(incident.remediation.tool)
                    && <PreflightTargetReview incident={incident} />}
                </div>
                <div className="approval-card">
                  {diagnosisRequiresRefresh || (!showDemo && !incident?.remediation) ? (
                    <div className="decision rejected" role="status"><strong>{isAnalyzing ? "Diagnosis still running" : "Final diagnosis requires review"}</strong><p>Progress snapshots do not authorize an action. Wait for the complete event or recover the retained incident with a read-only GET.</p></div>
                  ) : citationProblem ? (
                    <div className="decision rejected"><strong>Citations require review</strong><p>Approval is blocked because a cited evidence ID is missing or ambiguous. Inspect the retained incident trace.</p></div>
                  ) : approvalUncertain ? (
                    <div className="decision rejected" role="status">
                      <strong>Approval outcome unconfirmed</strong>
                      <p>The action may already be running. Only read-only status refresh is available; do not submit another decision.</p>
                      <button onClick={() => void refreshDecision()} disabled={isApproving || isRestoring || Boolean(readAccessError)}>Refresh incident status</button>
                    </div>
                  ) : incident?.status === "remediation_failed" && !incident.remediation?.dry_run_passed ? (
                    <div className="decision rejected">
                      <strong>Remediation blocked</strong>
                      <p>{incident.remediation?.expected_result} Review the incident trace before proposing another action.</p>
                    </div>
                  ) : !requiresApproval ? (
                    <div className="decision approved">
                      <strong>Advisory only</strong>
                      <p>{incident?.status === "insufficient_evidence"
                        ? "Collect telemetry and retry diagnosis before changing production."
                        : "No production mutation is proposed; follow the cited runbook guidance."}</p>
                    </div>
                  ) : actionCopy ? (
                    <div className={`decision ${actionState}`}>
                      <strong>{actionCopy.title}</strong>
                      <p>{actionCopy.body}</p>
                      {!incident && <button onClick={resetDecision}>
                        Reset decision
                      </button>}
                    </div>
                  ) : (
                    <>
                      <span>⚠ HUMAN APPROVAL REQUIRED</span>
                      <p>{preflightReview.mode === "simulation"
                        ? "This is a simulated action. No Kubernetes operation will be submitted."
                        : "This action changes production state and cannot run automatically."}</p>
                      {!preflightReview.known && <p role="alert">Preflight is unknown or inconsistent. Run a new diagnosis or renew preflight before approval. Rejection remains available.</p>}
                      {incident && approvalAccessError && <p role="status">{approvalAccessError}</p>}
                      <div>
                        <button
                          className="reject-button"
                          disabled={isApproving || isAnalyzing || Boolean(incident && approvalAccessError)}
                          onClick={() => void decide("reject")}
                        >
                          Reject
                        </button>
                        <button
                          className="approve-button"
                          disabled={isApproving || isAnalyzing || !preflightReview.known || Boolean(incident && approvalAccessError)}
                          onClick={() => void decide("approve")}
                        >
                          {actionLabel}
                        </button>
                      </div>
                    </>
                  )}
                </div>
                {incident && ["mitigating", "remediation_pending", "remediation_failed", "resolved"].includes(incident.status)
                  && incident.trace.some((event) => event.event === "execution_intent_prepared")
                  && incident.remediation && ["restart_deployment", "rollback_release"].includes(incident.remediation.tool)
                  && <OperationInspectionPanel key={`${incident.incident_id}:${incident.remediation.tool}`}
                       url={orchestratorUrl} incidentId={incident.incident_id} tool={incident.remediation.tool}
                       transport={apiTransport} operatorAccessError={operatorAccessError} readAccessError={readAccessError} />}
              </section>
              {incident && ["remediation_pending", "resolved"].includes(incident.status)
                && <RecoveryVerificationPanel key={`${incident.incident_id}:${authMode}:${trustedEndpoint}`} url={orchestratorUrl} incident={incident}
                     operatorToken={authMode === "protected" && !operatorAccessError ? credentials.operatorToken : ""}
                     transport={apiTransport} onUpdate={setIncident} />}
            </>
          ) : (
            <section className="panel trace-panel">
              <div className="panel-title">
                <div>
                  <span className="eyebrow">DURABLE WORKFLOW</span>
                  <h2>Agent execution trace</h2>
                </div>
                <span className="small-status">{incident ? `${incident.trace.length} retained events` : "checkpoint #12 (demo)"}</span>
              </div>
              <div className="trace-list">
                {(incident
                  ? incident.trace.map((event) => ({
                      title: event.step.replaceAll("_", " "),
                      detail:
                        Object.keys(event.payload).length > 0
                          ? `${event.event.replaceAll("_", " ")} · ${JSON.stringify(event.payload)}`
                          : event.event.replaceAll("_", " "),
                      state: "complete" as TraceState,
                      duration:
                        event.duration_ms === null ? undefined : `${event.duration_ms}ms`,
                    }))
                  : showDemo ? traceSteps : retainedSteps
                ).map((step, index) => (
                  <article key={index} className={step.state}>
                    <div className="trace-index">
                      {step.state === "complete" ? "✓" : index + 1}
                    </div>
                    <div>
                      <strong>{step.title}</strong>
                      <span>{step.detail}</span>
                    </div>
                    <time>{step.duration ?? step.state}</time>
                  </article>
                ))}
              </div>
              <div className="trace-footer">
                <span>TRACE ID</span>
                <code>{incident?.trace_id ?? (showDemo ? "tr_01JAZ8Y3PK7X4N" : "Not received")}</code>
                <span>All tool inputs, outputs and approval decisions are retained.</span>
              </div>
            </section>
          )}
        </section>

        <aside className="side-column">
          <section className="side-panel workflow-panel">
            <div className="side-title">
              <span>AGENT WORKFLOW</span>
              <b><i /> {incident ? isAnalyzing ? "DIAGNOSING" : incidentStatus.toUpperCase() : showDemo ? "RUNNING (DEMO)" : diagnosisPhase.toUpperCase()}</b>
            </div>
            <div className="workflow-list">
              {retainedSteps.map((step, index) => (
                <div key={step.title} className={step.state}>
                  <span>{step.state === "complete" ? "✓" : index + 1}</span>
                  <div><strong>{step.title}</strong><small>{step.detail}</small></div>
                  {step.duration && <time>{step.duration}</time>}
                </div>
              ))}
            </div>
          </section>

          <section className="side-panel activity-panel">
            <div className="side-title">
              <span>LIVE TOOL ACTIVITY</span>
              <button onClick={openTrace}>View trace</button>
            </div>
            <div className="activity-feed">
              {incident ? incident.trace.filter((event) => event.event === "tool_result").map((event) => (
                <article key={event.sequence}>
                  <i>·</i>
                  <div><strong>{String(event.payload.tool ?? event.step)}</strong><span>Result retained · see trace</span></div>
                  <time>{event.duration_ms === null ? "—" : `${event.duration_ms}ms`}</time>
                </article>
              )) : showDemo ? <><article>
                <i className="success">✓</i>
                <div><strong>query_metrics</strong><span>prometheus · 12 series</span></div>
                <time>218ms</time>
              </article>
              <article>
                <i className="success">✓</i>
                <div><strong>search_logs</strong><span>loki · 184 matches</span></div>
                <time>641ms</time>
              </article>
              <article>
                <i className="success">✓</i>
                <div><strong>get_recent_deployments</strong><span>kubernetes · 3 releases</span></div>
                <time>156ms</time>
              </article>
              <article>
                <i className="running-icon">↻</i>
                <div><strong>validate_evidence</strong><span>cross-source correlation</span></div>
                <time>running</time>
              </article>
              </> : <p>No tool-result checkpoint has been received.</p>}
            </div>
          </section>

          <section className="side-panel guardrail-panel">
            <div className="side-title">
              <span>GUARDRAILS</span>
              <b className="protected">PROTECTED</b>
            </div>
            <div className="guardrail-row">
              <span>Input policy</span><strong>{incident ? "See audit trace" : showDemo ? "Passed (demo)" : "Unconfirmed"}</strong>
            </div>
            <div className="guardrail-row">
              <span>Evidence grounding</span><strong>{incident ? citations.cited.length : showDemo ? 3 : 0} citations</strong>
            </div>
            <div className="guardrail-row">
              <span>Execution mode</span><strong className="amber-text">Approval only</strong>
            </div>
          </section>
        </aside>
      </div>

      <footer>
        <span><i className="status-dot" /> {incident ? "Persisted API snapshot received" : showDemo ? "Demo telemetry" : "Diagnosis completion unconfirmed"}</span>
        <span>{incident ? "Evidence sources are listed in the incident trace" : showDemo ? "Prometheus · Loki · Kubernetes · Runbooks" : "No demo evidence substituted"}</span>
        <span>{incident ? `Current step: ${incident.current_step.replaceAll("_", " ")}` : showDemo ? "Last checkpoint 2s ago (demo)" : "Waiting for retained checkpoint"}</span>
      </footer>
    </main>
  );
}
