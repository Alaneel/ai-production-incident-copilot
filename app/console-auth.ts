/** Memory-only development credentials. This is not a production SSO/proxy. */
export type ConsoleCredentials = {
  mode: "protected" | "local-demo";
  trustedEndpoint: string | null;
  readToken: string;
  operatorToken: string;
  operatorActor: string;
};
export type ConsoleScope = "read" | "operator";

export class ConsoleAuthError extends Error {
  constructor(message: string) { super(message); this.name = "ConsoleAuthError"; }
}

const loopback = new Set(["localhost", "127.0.0.1", "[::1]"]);
export function consoleBackendEndpoint(value: unknown): string | null {
  if (typeof value !== "string" || !value || value.trim() !== value || /[?#\\%]/.test(value)
      || /\/(?:\.|\.\.)(?:\/|$)/.test(value)
      || value.split("://")[1]?.split("/")[0].includes("@")) return null;
  try {
    const url = new URL(value);
    if ((url.protocol !== "https:" && !(url.protocol === "http:" && loopback.has(url.hostname)))
        || url.username || url.password || url.search || url.hash
        || !/^(?:\/[A-Za-z0-9_-]+)*\/?$/.test(url.pathname)) return null;
    return `${url.origin}${url.pathname.replace(/\/+$/, "")}`;
  } catch { return null; }
}
export function isLocalDemoBackend(value: unknown): boolean {
  const endpoint = consoleBackendEndpoint(value);
  return endpoint !== null && new URL(endpoint).protocol === "http:";
}
export function validConsoleApiToken(value: unknown): value is string {
  // Exact server contract: 32–4096 printable non-space ASCII characters.
  return typeof value === "string" && value.length >= 32 && value.length <= 4096
    && [...value].every(character => character.charCodeAt(0) >= 33 && character.charCodeAt(0) <= 126);
}
export function validConsoleOperatorActor(value: unknown): value is string {
  return typeof value === "string" && value.length >= 2 && value.length <= 120
    && value.trim() === value && !/[\u0000-\u001f\u007f]/.test(value);
}
export function consoleAuthorizationProblem(
  backend: unknown, credentials: ConsoleCredentials, scope: ConsoleScope,
): string | null {
  const endpoint = consoleBackendEndpoint(backend);
  if (!endpoint) return "Configure a valid HTTPS or HTTP loopback incident backend. No credential or request was sent.";
  if (credentials.trustedEndpoint !== endpoint) return "Confirm the exact trusted backend before sending credentials or requests.";
  if (credentials.mode === "local-demo") {
    if (!isLocalDemoBackend(endpoint)) return "Unauthenticated demo mode is restricted to HTTP loopback. No request was sent.";
    if (credentials.readToken || credentials.operatorToken) return "Clear credentials before using the explicitly unauthenticated local demo.";
    return null;
  }
  if (credentials.mode !== "protected") return "Select an explicit console authorization mode. No request was sent.";
  const token = scope === "operator" ? credentials.operatorToken : credentials.readToken;
  if (!validConsoleApiToken(token)) return `${scope === "operator" ? "Operator" : "Reader"} credential is required (32–4096 printable non-space ASCII characters). No request was sent.`;
  if (scope === "operator" && credentials.operatorToken === credentials.readToken) {
    return "Use distinct reader and operator credentials. No request was sent.";
  }
  return null;
}
export function consoleApprovalProblem(backend: unknown, credentials: ConsoleCredentials): string | null {
  return consoleAuthorizationProblem(backend, credentials, "operator")
    ?? consoleAuthorizationProblem(backend, credentials, "read")
    ?? (validConsoleOperatorActor(credentials.operatorActor) ? null
      : credentials.mode === "local-demo"
        ? "Enter a local demo audit actor (2–120 characters); identity is not verified. No decision was submitted."
        : "Enter the operator actor configured for this backend (2–120 characters). No decision was submitted.");
}
export function consoleAccessDenied(status: number, scope: ConsoleScope): string {
  return status === 401
    ? "API credential is missing, invalid or expired. Check the selected backend and credentials; no demo fallback was attempted."
    : scope === "operator"
      ? "Operator permission or a matching operator actor is required. This request was not accepted."
      : "This credential cannot access the requested incident data. No other credential or demo fallback was tried.";
}

function scopedRoute(endpoint: string, target: string, method: string): { scope: ConsoleScope; approval: boolean } {
  const base = new URL(endpoint), url = new URL(target);
  const prefix = `${base.pathname.replace(/\/$/, "")}/v1/incidents`;
  if (url.origin !== base.origin || url.username || url.password || url.search || url.hash) {
    throw new ConsoleAuthError("Request does not match the trusted backend. No credential was sent.");
  }
  if (url.pathname === prefix && method === "POST") return { scope: "read", approval: false };
  if (url.pathname === `${prefix}/stream` && method === "POST") return { scope: "read", approval: false };
  const suffix = url.pathname.startsWith(`${prefix}/`) ? url.pathname.slice(prefix.length + 1) : "";
  const [incident, operation, ...extra] = suffix.split("/");
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,120}$/.test(incident) || extra.length) {
    throw new ConsoleAuthError("Unsupported incident request route. No credential was sent.");
  }
  if (method === "GET" && (!operation || ["history", "events", "operation-inspections"].includes(operation))) {
    return { scope: "read", approval: false };
  }
  if (method === "GET" && operation === "operation-status") return { scope: "operator", approval: false };
  if (method === "POST" && operation === "recovery-check") return { scope: "operator", approval: false };
  if (method === "POST" && operation === "approval") return { scope: "operator", approval: true };
  throw new ConsoleAuthError("Unsupported incident request method or scope. No credential was sent.");
}

export function createConsoleTransport(
  backend: string | undefined, credentials: ConsoleCredentials, transport: typeof fetch = fetch,
): typeof fetch {
  // Capture the exact configuration for one request; never fall through to a
  // different origin, scope, credential or anonymous connection on failure.
  const bound = { ...credentials };
  return async (input, options = {}) => {
    const endpoint = consoleBackendEndpoint(backend);
    if (!endpoint || typeof input !== "string") throw new ConsoleAuthError("A trusted incident backend URL is required. No request was sent.");
    const method = (options.method ?? "GET").toUpperCase();
    const route = scopedRoute(endpoint, input, method);
    const problem = route.approval ? consoleApprovalProblem(backend, bound)
      : consoleAuthorizationProblem(backend, bound, route.scope);
    if (problem) throw new ConsoleAuthError(problem);
    if (method === "POST" && new URL(input).pathname.endsWith("/recovery-check") && options.body != null) {
      throw new ConsoleAuthError("Recovery inspection accepts no request body or target override. No request was sent.");
    }
    if (route.approval) {
      let body: unknown;
      try { body = typeof options.body === "string" ? JSON.parse(options.body) : null; } catch { body = null; }
      if (!body || typeof body !== "object" || Array.isArray(body)
          || (body as Record<string, unknown>).actor !== bound.operatorActor) {
        throw new ConsoleAuthError("Approval actor must match the configured console actor. No decision was submitted.");
      }
    }
    const headers = new Headers(options.headers);
    headers.delete("Authorization");
    if (bound.mode === "protected") headers.set("Authorization", `Bearer ${route.scope === "operator" ? bound.operatorToken : bound.readToken}`);
    return transport(input, { ...options, method, headers, redirect: "error", credentials: "omit",
      cache: "no-store", referrerPolicy: "no-referrer" });
  };
}

export async function createConsoleIncident<T>(
  backend: string | undefined, question: string, transport: typeof fetch,
): Promise<{ incident: T | null; error: string | null }> {
  try {
    if (!backend) throw new ConsoleAuthError("Configure the incident backend before diagnosis. No request was sent.");
    const response = await transport(`${backend}/v1/incidents`, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, environment: "prod-sg" }),
      signal: AbortSignal.timeout(60_000) });
    if (response.status === 401 || response.status === 403) return { incident: null, error: consoleAccessDenied(response.status, "read") };
    if (!response.ok) throw new Error("Diagnosis unavailable");
    const raw: unknown = await response.json();
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("Invalid diagnosis response");
    const state = raw as Record<string, unknown>;
    if (typeof state.incident_id !== "string" || !/^INC-[A-Za-z0-9_-]{1,100}$/.test(state.incident_id)
        || typeof state.status !== "string" || !["diagnosed", "waiting_approval", "insufficient_evidence", "remediation_failed"].includes(state.status)
        || !Array.isArray(state.services) || !state.services.length || !Array.isArray(state.evidence)
        || !Array.isArray(state.hypotheses) || !Array.isArray(state.trace)) throw new Error("Invalid diagnosis response");
    return { incident: state as T, error: null };
  } catch (error) {
    return { incident: null, error: error instanceof ConsoleAuthError ? error.message
      : "Diagnosis is unavailable or returned an invalid response. No demo fallback was used; no diagnosis was confirmed." };
  }
}
