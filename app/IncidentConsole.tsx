"use client";

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
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
    duration: "1.4s",
  },
  {
    title: "Propose remediation",
    detail: "Awaiting validated evidence",
    state: "queued",
    duration: "0.6s",
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

// Demo sample readouts. The axis is piecewise (13:45 → 13:56 deploy marker at
// the midpoint → 14:20), so sample times follow the same two segments.
function demoSampleTime(index: number, count: number) {
  const mid = (count - 1) / 2;
  const minutes = index <= mid ? (index / mid) * 11 : 11 + ((index - mid) / mid) * 24;
  const total = 13 * 60 + 45 + Math.round(minutes);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}
const demoLatencySamples = bars.map((height, index) => ({ time: demoSampleTime(index, bars.length), value: `${(height / 89 * 2.84).toFixed(2)}s` }));
const demoErrorSamples = errors.map((height, index) => ({ time: demoSampleTime(index, errors.length), value: `${(height / 38 * 12.7).toFixed(1)}%` }));

// Demo replay: tools are revealed by the workflow step that calls them.
const demoTools = [
  { name: "query_metrics", detail: "prometheus · 12 series", step: 2, duration: "218ms" },
  { name: "search_logs", detail: "loki · 184 matches", step: 2, duration: "641ms" },
  { name: "get_recent_deployments", detail: "kubernetes · 3 releases", step: 1, duration: "156ms" },
  { name: "validate_evidence", detail: "cross-source correlation", step: 4, duration: "1.4s" },
];
const demoStepDelays = [450, 700, 1100, 650, 1100, 700];
const DEMO_SNAPSHOT_STAGE = 4;

function toggleTheme() {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
}

type PaletteCommand = { id: string; group: string; label: string; hint?: string; run: () => string | void };

function CommandPalette({ commands }: { commands: PaletteCommand[] }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const needle = query.trim().toLowerCase();
  const matches = needle ? commands.filter((command) => `${command.group} ${command.label}`.toLowerCase().includes(needle)) : commands;
  const selected = Math.min(active, Math.max(matches.length - 1, 0));

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        returnFocus.current = document.activeElement as HTMLElement | null;
        setQuery(""); setActive(0); setOpen((value) => !value);
        return;
      }
      const target = event.target instanceof Element ? event.target : null;
      const typing = target?.closest("input, textarea, select, [contenteditable='true']");
      if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
        const field = document.getElementById("incident-question") as HTMLInputElement | null;
        if (field && !field.disabled) { event.preventDefault(); field.focus(); field.select(); }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, []);

  function show() {
    returnFocus.current = document.activeElement as HTMLElement | null;
    setQuery(""); setActive(0); setOpen(true);
  }
  function close() {
    setOpen(false);
    returnFocus.current?.focus?.();
  }
  function run(command: PaletteCommand | undefined) {
    if (!command) return;
    setOpen(false);
    const message = command.run();
    if (message) {
      setToast(message);
      if (toastTimer.current) clearTimeout(toastTimer.current);
      toastTimer.current = setTimeout(() => setToast(null), 1800);
    }
  }
  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") { event.preventDefault(); close(); }
    else if (event.key === "ArrowDown") { event.preventDefault(); setActive((selected + 1) % Math.max(matches.length, 1)); }
    else if (event.key === "ArrowUp") { event.preventDefault(); setActive((selected - 1 + matches.length) % Math.max(matches.length, 1)); }
    else if (event.key === "Enter") { event.preventDefault(); run(matches[selected]); }
  }

  return (
    <>
      <button type="button" className="icon-button" aria-label="Open command palette" aria-haspopup="dialog" onClick={show}>
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
        </svg>
        <span className="search-label">Search…</span>
        <kbd>⌘K</kbd>
      </button>
      {open && createPortal(
        <div className="palette-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
          <div className="palette" role="dialog" aria-modal="true" aria-label="Command palette" onKeyDown={onKeyDown}>
            <div className="palette-input">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
              </svg>
              <input autoFocus value={query} placeholder="Go to a section or run a command…" aria-label="Filter commands"
                role="combobox" aria-expanded="true" aria-controls="palette-list" aria-autocomplete="list"
                aria-activedescendant={matches[selected] ? `palette-${matches[selected].id}` : undefined}
                onChange={(event) => { setQuery(event.target.value); setActive(0); }} />
              <kbd>esc</kbd>
            </div>
            <ul id="palette-list" role="listbox" aria-label="Commands">
              {matches.length === 0 && <li className="palette-empty">No matching command</li>}
              {matches.map((command, index) => {
                const heading = index === 0 || matches[index - 1].group !== command.group ? command.group : null;
                return (
                  <Fragment key={command.id}>
                    {heading && <li className="palette-group" role="presentation">{heading}</li>}
                    <li id={`palette-${command.id}`} role="option" aria-selected={index === selected}
                      onMouseMove={() => { if (index !== selected) setActive(index); }}
                      onClick={() => run(command)}>
                      <span>{command.label}</span>
                      {command.hint && <kbd>{command.hint}</kbd>}
                    </li>
                  </Fragment>
                );
              })}
            </ul>
            <div className="palette-foot" aria-hidden="true"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> run</span><span><kbd>/</kbd> ask the copilot</span></div>
          </div>
        </div>,
        document.body,
      )}
      {toast && createPortal(<div className="palette-toast" role="status">{toast}</div>, document.body)}
    </>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return (
    <button type="button" className={`copy-button${copied ? " copied" : ""}`} aria-label={label}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          if (timer.current) clearTimeout(timer.current);
          timer.current = setTimeout(() => setCopied(false), 1600);
        }, () => undefined);
      }}>
      {copied
        ? <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m5 12 5 5 9-10" /></svg>
        : <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h9" /></svg>}
      <span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
    </button>
  );
}

function MetricChart({
  label,
  value,
  delta,
  data,
  tone,
  axis = ["13:45", "13:56", "14:20"],
  samples = [],
}: {
  label: string;
  value: string;
  delta: string;
  data: number[];
  tone: "critical" | "warning";
  axis?: string[];
  samples?: Array<{ time: string; value: string }>;
}) {
  const [cursor, setCursor] = useState<number | null>(null);
  const gradientId = `metric-fill-${tone}`;
  const coords = data.map((height, index) => ({
    x: data.length > 1 ? (index / (data.length - 1)) * 100 : 50,
    y: 40 - Math.max(0, Math.min(100, height)) * 0.36,
  }));
  const points = coords.map(({ x, y }) => `${x.toFixed(2)},${y.toFixed(2)}`);
  const linePath = points.length ? `M${points.join(" L")}` : "";
  const areaPath = points.length ? `${linePath} L100,40 L0,40 Z` : "";
  const showMarker = data.length > 0 && axis.length === 3;
  const inspectable = samples.length === data.length && data.length > 1;
  const reading = inspectable && cursor !== null ? samples[cursor] : null;
  function locate(event: React.PointerEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    setCursor(Math.round(ratio * (data.length - 1)));
  }
  function step(event: React.KeyboardEvent<HTMLDivElement>) {
    const last = data.length - 1;
    const next = event.key === "ArrowRight" ? Math.min(last, (cursor ?? -1) + 1)
      : event.key === "ArrowLeft" ? Math.max(0, (cursor ?? last + 1) - 1)
        : event.key === "Home" ? 0 : event.key === "End" ? last : event.key === "Escape" ? null : undefined;
    if (next === undefined) return;
    event.preventDefault(); setCursor(next);
  }
  return (
    <article className={`metric-card ${tone}`}>
      <div className="metric-heading">
        <div>
          <span className="eyebrow">{label}</span>
          <strong>{value}</strong>
        </div>
        <span className={`delta ${tone}`}>{delta}</span>
      </div>
      <div className={`metric-chart ${tone}${inspectable ? " inspectable" : ""}`} role="img"
        aria-label={`${label} trend${inspectable ? ". Use arrow keys to read samples." : ""}`}
        tabIndex={inspectable ? 0 : undefined}
        onPointerMove={inspectable ? locate : undefined} onPointerLeave={inspectable ? () => setCursor(null) : undefined}
        onKeyDown={inspectable ? step : undefined} onBlur={inspectable ? () => setCursor(null) : undefined}>
        {data.length > 0 ? (
          <svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
            <defs>
              <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="currentColor" stopOpacity="0.32" />
                <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
              </linearGradient>
            </defs>
            <path d={areaPath} fill={`url(#${gradientId})`} />
            <path d={linePath} fill="none" stroke="currentColor" strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          </svg>
        ) : (
          <span className="metric-empty">No timestamped samples retained</span>
        )}
        {showMarker && <i className="incident-marker"><b>v142 deployed</b></i>}
        {reading && cursor !== null && (
          <div className="chart-cursor" style={{ "--x": `${coords[cursor].x}%`, "--y": `${(coords[cursor].y / 40) * 100}%` } as React.CSSProperties}>
            <i aria-hidden="true" />
            <span role="status"><time>{reading.time}</time><b>{reading.value}</b></span>
          </div>
        )}
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
  const [demoReplay, setDemoReplay] = useState({ stage: DEMO_SNAPSHOT_STAGE, running: false, done: false });
  const demoTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
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
    return () => {
      active = false; diagnosisEpoch.current += 1; activeDiagnosis.current?.abort(); activeDiagnosis.current = null;
      if (demoTimer.current) clearTimeout(demoTimer.current);
    };
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
      replayDemo();
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

  // Offline demo only: replays the canned workflow locally. Nothing is sent.
  function replayDemo() {
    if (demoTimer.current) clearTimeout(demoTimer.current);
    setActionState("pending"); setActiveTab("trace");
    const advance = (stage: number) => {
      const finished = stage >= traceSteps.length;
      setDemoReplay({ stage, running: !finished, done: finished });
      demoTimer.current = finished ? null : setTimeout(() => advance(stage + 1), demoStepDelays[stage]);
    };
    advance(0);
  }

  async function decide(decision: "approve" | "reject") {
    if (showDemo && demoReplay.running) return;
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

  const incidentStatus = incident?.status.replaceAll("_", " ") ?? (showDemo && demoReplay.done ? "Awaiting approval" : "Investigating");
  const demoSteps: Array<{ title: string; detail: string; state: TraceState; duration?: string }> = traceSteps.map((step, index) => {
    const state: TraceState = index < demoReplay.stage ? "complete" : index === demoReplay.stage ? "running" : "queued";
    const proposed = index === traceSteps.length - 1 && state === "complete";
    return { ...step, state, duration: state === "complete" ? step.duration : undefined,
      detail: proposed ? "Rollback proposed · held for human approval" : step.detail };
  });
  const demoPending = (step: number) => showDemo && demoReplay.running && demoReplay.stage <= step;
  const retainedSteps = incident
    ? [...new Map(incident.trace.map((event) => [event.step, event])).values()].map((event) => ({
        title: event.step.replaceAll("_", " "),
        detail: `Persisted: ${event.event.replaceAll("_", " ")}`,
        state: "complete" as TraceState,
        duration: event.duration_ms === null ? undefined : `${event.duration_ms}ms`,
      }))
    : isAnalyzing ? [{ title: "Start diagnostic workflow", detail: "Waiting for the first persisted checkpoint", state: "running" as TraceState }] : showDemo ? demoSteps : [];
  if (isAnalyzing && incident) retainedSteps.push({ title: "Diagnosis still running", detail: "Awaiting the next persisted checkpoint", state: "running" });
  function metricView(metric: string, percentage = false) {
    const item = incident?.evidence.find((entry) => entry.kind === "metric" && entry.query === metric);
    const raw = item?.attributes.series;
    const points = Array.isArray(raw) ? raw.filter((point): point is { timestamp: string; value: number } =>
      point && typeof point.value === "number" && Number.isFinite(point.value) &&
      typeof point.timestamp === "string" && Number.isFinite(Date.parse(point.timestamp))) : [];
    if (!points.length) return { value: "Unavailable", delta: "No samples", data: [], axis: ["No timestamped metric samples"], samples: [] };
    const peak = Math.max(...points.map((point) => point.value), 0);
    const last = points[points.length - 1].value;
    const time = (value: string) => new Date(value).toLocaleTimeString("en-SG", { hour12: false });
    const format = (sample: number) => percentage ? `${(sample * 100).toFixed(1)}%` : `${sample.toFixed(0)}ms`;
    return {
      value: format(last),
      samples: points.map((point) => ({ time: time(point.timestamp), value: format(point.value) })),
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
    (showDemo ? "kubectl rollout undo deployment/payment-service --to-revision=141 -n payments"
      : "No remediation selected; investigate an incident first.");
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

  function jumpTo(selector: string) {
    setActiveTab("timeline");
    // A click commits the tab switch synchronously; scroll on the next task.
    setTimeout(() => {
      const target = document.querySelector<HTMLElement>(selector);
      if (!target) return;
      const before = window.scrollY;
      target.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
      // Some embedded browsers ignore smooth scrolling; land the jump regardless.
      setTimeout(() => { if (window.scrollY === before) target.scrollIntoView({ block: "start" }); }, 350);
      target.classList.remove("flash"); void target.offsetWidth; target.classList.add("flash");
    }, 0);
  }
  function copyText(text: string, done: string) {
    if (!navigator.clipboard) return "Clipboard unavailable in this browser";
    void navigator.clipboard.writeText(text).catch(() => undefined);
    return done;
  }
  const traceId = incident?.trace_id ?? (showDemo ? "tr_01JAZ8Y3PK7X4N" : null);
  const hasCommand = Boolean(incident?.remediation || showDemo);
  const paletteCommands: PaletteCommand[] = [
    { id: "ask", group: "Investigate", label: "Ask the incident copilot", hint: "/", run: () => {
      const field = document.getElementById("incident-question") as HTMLInputElement | null;
      field?.focus(); field?.select();
    } },
    ...(showDemo ? [{ id: "replay", group: "Investigate", label: "Replay the demo investigation", run: () => { replayDemo(); } }] : []),
    { id: "overview", group: "Navigate", label: "Incident overview", run: () => jumpTo(".metrics-grid") },
    { id: "map", group: "Navigate", label: "Service map", run: () => jumpTo(".dependency-panel") },
    { id: "hypothesis", group: "Navigate", label: "Leading hypothesis and evidence", run: () => jumpTo(".hypothesis-panel") },
    { id: "remediation", group: "Navigate", label: "Remediation and approval", run: () => jumpTo(".remediation-panel") },
    { id: "trace", group: "Navigate", label: "Agent execution trace", run: openTrace },
    { id: "theme", group: "Console", label: "Toggle colour theme", run: toggleTheme },
    ...(hasCommand ? [{ id: "copy-command", group: "Console", label: "Copy remediation command", run: () => copyText(commandPreview, "Command copied") }] : []),
    ...(traceId ? [{ id: "copy-trace", group: "Console", label: "Copy trace ID", run: () => copyText(traceId, "Trace ID copied") }] : []),
  ];

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 12h4l2.5-6 5 12 2.5-6H21" />
            </svg>
          </span>
          <strong>OpsPilot</strong>
        </div>
        <nav className="topbar-center" aria-label="Breadcrumb">
          <span className="sep" aria-hidden="true">/</span>
          <span className="crumb-root">Incidents</span>
          <span className="sep" aria-hidden="true">/</span>
          <b>{incident?.incident_id ?? (showDemo ? "INC-2847" : "Waiting for incident reference")}</b>
          <span className="mode-pill">
            <span className="live-dot" />
            {incident ? isAnalyzing ? "Diagnostic checkpoint" : "Retained incident" : showDemo ? "Demo incident" : "Diagnosis unconfirmed"}
          </span>
        </nav>
        <div className="topbar-actions">
          <CommandPalette commands={paletteCommands} />
          <span className="env-button" title="Environment label for this incident">
            <span className="status-dot" />
            prod-sg
          </span>
          <button type="button" className="theme-toggle" aria-label="Toggle colour theme" onClick={toggleTheme}>
            <svg className="moon" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" /></svg>
            <svg className="sun" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
          </button>
          <span className="avatar" title="Signed-in operator (demo)">AW</span>
        </div>
      </header>

      <section className="incident-header">
        <div className="incident-heading">
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
            <span>Owner</span>
            <strong>{incident || !showDemo ? "Unassigned" : <><i className="owner-avatar">JL</i> Jane Liu</>}</strong>
          </div>
          <div>
            <span>Affected service</span>
            <strong>{primaryService}</strong>
          </div>
          <div>
            <span>Trace</span>
            <strong className="mono">{incident?.trace_id ?? (showDemo ? "tr_01JAZ8Y3PK7X4N" : "Not received")}</strong>
          </div>
        </div>
      </section>

      <div className="workspace">
        <section className="main-column">
          <form className="question-box" onSubmit={submitQuestion}>
            <label htmlFor="incident-question">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" /></svg>
              Ask the incident copilot
            </label>
            <div>
              <span className="prompt-glyph" aria-hidden="true">›_</span>
              <input
                id="incident-question"
                value={prompt}
                onChange={(event) => { interruptDiagnosis(); setPrompt(event.target.value); }}
                aria-label="Incident question"
                disabled={isApproving || isRestoring || approvalUncertain}
              />
              <button type="submit" disabled={isAnalyzing || isApproving || isRestoring || approvalUncertain || (showDemo && demoReplay.running) || (diagnosisRequiresRefresh && prompt === diagnosisQuestion) || Boolean(orchestratorUrl && readAccessError)}>{showDemo && demoReplay.running ? "Replaying…" : <>Analyze <kbd>↵</kbd></>}</button>
            </div>
          </form>
          {showDemo && demoReplay.running && (
            <div className="api-notice running" role="status">Replaying the demo investigation locally. No backend, model or cluster is contacted.</div>
          )}
          {showDemo && demoReplay.done && (
            <div className="api-notice connected" role="status">Demo replay complete: the rollback is proposed and held for human approval.
              <button type="button" className="notice-link" onClick={() => jumpTo(".hypothesis-panel")}>Review hypothesis and approval →</button>
            </div>
          )}
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

          <details className="connection-panel" aria-label="Local protected API connection" open={restoredIncidentId !== null && !incident ? true : undefined}>
            <summary>
              <span className="connection-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
              </span>
              <span className="connection-title">
                <strong>Local API connection — not SSO</strong>
                <small>{authMode === "local-demo" ? "Unauthenticated local demo" : "Protected API credentials"} · <code>{backendEndpoint ?? "no backend configured"}</code></small>
              </span>
              <span className={`connection-state ${!orchestratorUrl ? "idle" : readAccessError ? "warn" : "ok"}`}>
                {!orchestratorUrl ? "Demo data" : readAccessError ? "Setup required" : "Ready"}
              </span>
              <svg className="chevron" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
            </summary>
            <div className="connection-body">
              <p className="connection-note">Development integration only. Use a trusted server-side identity gateway for production; never paste production secrets here.</p>
              <div className="field-grid">
                <div className="field">
                  <label htmlFor="console-auth-mode">Authorization mode</label>
                  <select id="console-auth-mode" value={authMode} disabled={isApproving || isRestoring}
                    onChange={event => changeAuthorizationMode(event.target.value as ConsoleCredentials["mode"])}>
                    <option value="protected">Protected API credentials</option>
                    <option value="local-demo" disabled={!isLocalDemoBackend(orchestratorUrl)}>Unauthenticated local demo</option>
                  </select>
                </div>
                <div className="field">
                  <span className="field-label">Configured incident backend</span>
                  <code className="field-value">{backendEndpoint ?? "Unavailable or unsafe URL"}</code>
                  <small>No editable endpoint is accepted.</small>
                </div>
                {authMode === "protected" && <>
                  <div className="field"><label htmlFor="console-read-token">Reader API token</label>
                    <input id="console-read-token" type="password" autoComplete="off" value={readCredential}
                      disabled={isApproving || isRestoring} onChange={event => { interruptDiagnosis(); setReadCredential(event.target.value); }} /></div>
                  {operatorToken === undefined && <div className="field"><label htmlFor="console-operator-token">Operator API token</label>
                    <input id="console-operator-token" type="password" autoComplete="off" value={operatorCredential}
                      disabled={isApproving || isRestoring} onChange={event => { interruptDiagnosis(); setOperatorCredential(event.target.value); }} /></div>}
                </>}
                <div className="field field-wide"><label htmlFor="console-operator-actor">{authMode === "protected" ? "Operator actor configured for this backend" : "Local demo audit actor (unverified)"}</label>
                  <input id="console-operator-actor" autoComplete="off" value={operatorActor} maxLength={120}
                    disabled={isApproving || isRestoring} onChange={event => { interruptDiagnosis(); setOperatorActor(event.target.value); }} />
                  <small>{authMode === "protected" ? "The backend validates this binding; entering a name is not identity verification."
                    : "The demo records this supplied name without authenticating a person or binding it to a credential."}</small></div>
              </div>
              {authMode === "local-demo" ? <p className="connection-callout warn" role="alert">Explicit HTTP loopback demo only. Requests carry no Bearer credential. The backend must have auth disabled; this does not verify human identity or authenticated approval. A 401/403 never switches modes.</p>
                : <p className="connection-callout">The backend must use required authorization. Reader credentials diagnose and read; operator credentials approve and inspect. Credentials are not human SSO.</p>}
              <label className="trust-check"><input type="checkbox" checked={backendEndpoint !== null && trustedEndpoint === backendEndpoint}
                disabled={!backendEndpoint || isApproving || isRestoring}
                onChange={event => { if (event.target.checked) setTrustedEndpoint(backendEndpoint); else clearCredentials(); }} />
                <span>I trust this exact backend for these development requests and credentials.</span></label>
              <p className="connection-note">Tokens stay only in this page&apos;s memory, never in browser storage, URLs, incident payloads or traces. Reload or clear the connection to remove them.</p>
              <div className="connection-actions">
                <button type="button" className="ghost-button" disabled={isApproving || isRestoring} onClick={clearCredentials}>Clear credentials and trust</button>
                {(incident || restoredIncidentId) && <button type="button" className="ghost-button" disabled={isAnalyzing || isApproving || isRestoring || Boolean(readAccessError)}
                  onClick={() => void refreshDecision()}>Restore / refresh incident (read-only)</button>}
              </div>
            </div>
          </details>
          {orchestratorUrl && readAccessError && <p className="api-notice warn" role="status">{readAccessError}</p>}

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
                  label="P95 latency"
                  {...(showDemo ? { value: "2.84s", delta: "+341%", data: bars, samples: demoLatencySamples } : metricView("request_latency_p95"))}
                  tone="warning"
                />
                <MetricChart
                  label="Error rate"
                  {...(showDemo ? { value: "12.7%", delta: "+11.4pp", data: errors, samples: demoErrorSamples } : metricView("request_error_rate", true))}
                  tone="critical"
                />
              </section>

              <section className="panel dependency-panel">
                <div className="panel-title">
                  <div>
                    <span className="eyebrow">Service map</span>
                    <h2>Dependency blast radius</h2>
                  </div>
                  <span className="small-status">
                    {incident?.services.length ?? (showDemo ? 4 : 0)} services {showDemo ? "inspected (demo)" : "discovered"}
                  </span>
                </div>
                {incident ? <DependencyImpactList services={incident.services} impacts={incident.service_impacts ?? []} evidence={incident.evidence} topology={incident.service_topology} /> : showDemo ? <div className="service-map">
                  <div className="map-column">
                    <div className="service-node healthy">
                      <i>C</i>
                      <div>
                        <strong>{dependencyServices[0] ?? "upstream"}</strong>
                        <span>upstream · healthy</span>
                      </div>
                    </div>
                  </div>
                  <svg className="map-link" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                    <path className="flow critical" d="M0,50 L100,50" vectorEffect="non-scaling-stroke" />
                  </svg>
                  <div className="map-column">
                    <div className="service-node critical">
                      <i>P</i>
                      <div><strong>{primaryService}</strong><span>primary signal</span></div>
                      <b>12.7%</b>
                    </div>
                  </div>
                  <svg className="map-link" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                    <path className="flow warning" d="M0,50 C50,50 50,22 100,22" vectorEffect="non-scaling-stroke" />
                    <path className="flow" d="M0,50 C50,50 50,78 100,78" vectorEffect="non-scaling-stroke" />
                  </svg>
                  <div className="map-column stacked">
                    <div className="service-node warning">
                      <i>DB</i>
                      <div>
                        <strong>{dependencyServices[1] ?? "downstream"}</strong>
                        <span>pool 50/50 saturated</span>
                      </div>
                    </div>
                    <div className="service-node healthy">
                      <i>R</i>
                      <div>
                        <strong>{dependencyServices[2] ?? "supporting service"}</strong>
                        <span>downstream · healthy</span>
                      </div>
                    </div>
                  </div>
                </div> : <p className="empty-state">No service topology checkpoint has been received.</p>}
              </section>

              {incident && <RootCauseCandidates incident={incident} />}

              <section className={`panel hypothesis-panel${demoPending(4) ? " is-pending" : ""}`} inert={demoPending(4) || undefined}>
                <div className="hypothesis-top">
                  <div className="confidence-ring" style={{ "--p": `${!showDemo && (!incident?.hypotheses.length || (incident.candidate_incident_types?.length ?? 0) > 1) ? 0 : confidence}%` } as React.CSSProperties}>
                    <strong>{!showDemo && (!incident?.hypotheses.length || (incident.candidate_incident_types?.length ?? 0) > 1) ? "—" : confidence}</strong><span>{!showDemo && (!incident?.hypotheses.length || (incident.candidate_incident_types?.length ?? 0) > 1) ? "" : "%"}</span>
                  </div>
                  <div>
                    <span className="eyebrow">Leading hypothesis</span>
                    <h2>{leadingHypothesis}</h2>
                  </div>
                  <span className={`validated ${incident?.status === "insufficient_evidence" ? "warn" : ""}`}>
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
                    <article key={item.id} data-kind={item.icon}>
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
                {citationProblem && <p className="inline-alert" role="alert">Citation integrity failed. Missing IDs: {citations.missing.join(", ") || "none"}; ambiguous IDs: {citations.ambiguous.join(", ") || "none"}. Approval is blocked.</p>}
                {(incident?.candidate_incident_types?.length ?? 0) > 1 && (
                  <div className="competing-hypotheses">
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

              <section className={`panel remediation-panel${demoPending(5) ? " is-pending" : ""}`} inert={demoPending(5) || undefined}>
                <div className="remediation-copy">
                  <span className="eyebrow">Recommended remediation</span>
                  <h2>{remediationTitle}</h2>
                  <p>{expectedResult}</p>
                  <div className="command-preview">
                    <div className="command-head">
                      <b>{!requiresApproval ? "ADVISORY" : preflightReview.mode === "simulation" ? "SIMULATION"
                      : preflightReview.mode === "kubernetes-server-dry-run" ? "SERVER DRY RUN" : "PREFLIGHT UNKNOWN"}</b>
                      {hasCommand && <CopyButton text={commandPreview} label="Copy remediation command" />}
                    </div>
                    <span>$</span>
                    <code>{commandPreview.split(" ").map((token, index) => <Fragment key={index}>{index > 0 && " "}<span>{token}</span></Fragment>)}</code>
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
                      <span><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3 2 20h20L12 3Z" /><path d="M12 10v4M12 17.5v.01" /></svg>HUMAN APPROVAL REQUIRED</span>
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
                  <span className="eyebrow">Durable workflow</span>
                  <h2>Agent execution trace</h2>
                </div>
                <span className="small-status">{incident ? `${incident.trace.length} retained events` : `checkpoint #${8 + demoReplay.stage} (demo)`}</span>
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
                  : showDemo ? demoSteps : retainedSteps
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
                <span>Trace ID</span>
                <code>{incident?.trace_id ?? (showDemo ? "tr_01JAZ8Y3PK7X4N" : "Not received")}</code>
                <span>All tool inputs, outputs and approval decisions are retained.</span>
              </div>
            </section>
          )}
        </section>

        <aside className="side-column">
          <section className="side-panel workflow-panel">
            <div className="side-title">
              <span>Agent workflow</span>
              <b><i /> {incident ? isAnalyzing ? "Diagnosing" : incidentStatus : showDemo ? demoReplay.done ? "Awaiting approval · demo" : "Running · demo" : diagnosisPhase}</b>
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
              <span>Live tool activity</span>
              <button onClick={openTrace}>View trace</button>
            </div>
            <div className="activity-feed">
              {incident ? incident.trace.filter((event) => event.event === "tool_result").map((event) => (
                <article key={event.sequence}>
                  <i>·</i>
                  <div><strong>{String(event.payload.tool ?? event.step)}</strong><span>Result retained · see trace</span></div>
                  <time>{event.duration_ms === null ? "—" : `${event.duration_ms}ms`}</time>
                </article>
              )) : showDemo ? demoTools.filter((tool) => demoReplay.stage >= tool.step).map((tool) => {
                const complete = demoReplay.stage > tool.step;
                return (
                  <article key={tool.name}>
                    <i className={complete ? "success" : "running-icon"}>{complete ? "✓" : "↻"}</i>
                    <div><strong>{tool.name}</strong><span>{tool.detail}</span></div>
                    <time>{complete ? tool.duration : "running"}</time>
                  </article>
                );
              }) : <p>No tool-result checkpoint has been received.</p>}
            </div>
          </section>

          <section className="side-panel guardrail-panel">
            <div className="side-title">
              <span>Guardrails</span>
              <b className="protected">Protected</b>
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
