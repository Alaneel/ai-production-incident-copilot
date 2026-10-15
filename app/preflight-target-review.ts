type Action = "restart_deployment" | "rollback_release";
type Mode = "simulation" | "kubernetes-server-dry-run" | "unknown";
type Target = {
  tool: Action;
  service: string;
  namespace: string;
  cluster_context: string;
  deployment_uid: string;
  resource_version: string;
  deployment_template_sha256: string;
  revision: string | null;
  replicaset_uid: string | null;
  rollback_template_sha256: string | null;
};
type Review = {
  mode: Mode;
  known: boolean;
  label: string;
  detail: string;
  target: Target | null;
  simulationScope: { service: string; namespace: string; revision: string | null } | null;
  rollback: boolean;
};

const targetFields = ["tool", "service", "namespace", "cluster_context", "deployment_uid", "resource_version",
  "deployment_template_sha256", "revision", "replicaset_uid", "rollback_template_sha256"] as const;
const dnsName = /^[a-z0-9][a-z0-9-]{0,62}$/;
const sha256 = /^[0-9a-f]{64}$/;
const revisionPattern = /^[1-9][0-9]{0,11}$/;
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}
function textMatches(value: unknown, pattern: RegExp): value is string {
  // JavaScript's $ may match before a final newline; require the entire value.
  return typeof value === "string" && pattern.exec(value)?.[0] === value;
}
function identity(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 128 && !/\s/.test(value);
}
function action(value: unknown): value is Action {
  return value === "restart_deployment" || value === "rollback_release";
}
function targetContext(value: unknown, tool: Action, arguments_: Record<string, unknown>): Target | null {
  const target = record(value);
  if (!target || Object.keys(target).length !== targetFields.length
      || targetFields.some(field => !Object.hasOwn(target, field))
      || target.tool !== tool || target.service !== arguments_.service || target.namespace !== arguments_.namespace
      || !textMatches(target.service, dnsName) || !textMatches(target.namespace, dnsName)
      || !textMatches(target.cluster_context, sha256) || !textMatches(target.deployment_template_sha256, sha256)
      || !identity(target.deployment_uid) || !identity(target.resource_version)) return null;
  if (tool === "restart_deployment") {
    if (target.revision !== null || target.replicaset_uid !== null || target.rollback_template_sha256 !== null
        || (arguments_.revision !== undefined && arguments_.revision !== null)) return null;
  } else if (!textMatches(target.revision, revisionPattern) || target.revision !== arguments_.revision
      || !identity(target.replicaset_uid) || !textMatches(target.rollback_template_sha256, sha256)) return null;
  // Project only allowlisted fields. In particular, never render untrusted error/provider text.
  return Object.fromEntries(targetFields.map(field => [field, target[field]])) as Target;
}
function sameTarget(left: Target, right: Target): boolean {
  return targetFields.every(field => left[field] === right[field]);
}

/** Retained review metadata only; the server remains the execution authority. */
export function preflightTargetReview(incident: unknown): Review {
  const unknown: Review = {
    mode: "unknown", known: false, label: "Preflight unknown or inconsistent",
    detail: "No current, scoped preflight is available. Run a new diagnosis or renew preflight before approval.",
    target: null, simulationScope: null, rollback: false,
  };
  const state = record(incident), remediation = record(state?.remediation), arguments_ = record(remediation?.arguments);
  if (!remediation || !action(remediation.tool) || remediation.dry_run_passed !== true || !arguments_
      || !textMatches(arguments_.service, dnsName) || !textMatches(arguments_.namespace, dnsName)
      || !Object.hasOwn(arguments_, "target_context") || !Array.isArray(state?.trace)) return unknown;
  const tool = remediation.tool;
  const checks = state.trace.map(record).filter(item => item?.event === "tool_result"
    && record(item.payload)?.tool === tool && record(item.payload)?.dry_run === true);
  if (!checks.length || checks.some(item => !Number.isSafeInteger(item?.sequence) || Number(item?.sequence) < 0)) return unknown;
  checks.sort((left, right) => Number(right?.sequence) - Number(left?.sequence));
  if (checks.filter(item => item?.sequence === checks[0]?.sequence).length !== 1) return unknown;
  const result = record(checks[0]?.payload);
  const mode = remediation.preflight_mode;
  if (!result || result.accepted !== true || result.preflight_mode !== mode || !Object.hasOwn(result, "target_context")) return unknown;
  if (mode === "simulation") {
    const revision = tool === "rollback_release" ? arguments_.revision : null;
    if (arguments_.target_context !== null || result.target_context !== null
        || (tool === "rollback_release" && !textMatches(revision, /^[a-zA-Z0-9._-]{1,80}$/))
        || (tool === "restart_deployment" && arguments_.revision !== undefined && arguments_.revision !== null)) return unknown;
    return { mode, known: true, label: "Simulation preflight only",
      detail: "This proposal uses the simulation backend. No Kubernetes admission check or live target identity was verified.",
      target: null, rollback: tool === "rollback_release",
      simulationScope: { service: arguments_.service, namespace: arguments_.namespace, revision: revision as string | null } };
  }
  if (mode !== "kubernetes-server-dry-run") return unknown;
  const target = targetContext(arguments_.target_context, tool, arguments_);
  const retained = targetContext(result.target_context, tool, arguments_);
  if (!target || !retained || !sameTarget(target, retained)) return unknown;
  return { mode, known: true, label: "Kubernetes server-side dry-run reported",
    detail: "The retained preflight submitted the proposed patch with dryRun=All. It did not persist that patch or establish mitigation efficacy.",
    target, simulationScope: null, rollback: tool === "rollback_release" };
}
