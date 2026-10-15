# Incident API authorization

The API has two explicit modes. `ORCHESTRATOR_AUTH_MODE=disabled` preserves
the local fixture/demo workflow and is **not suitable for a publicly exposed
production API**. `/health` reports the active mode without credentials.

For protected API integration set, on the server only:

```text
ORCHESTRATOR_AUTH_MODE=required
ORCHESTRATOR_READ_TOKEN=<distinct random server secret, 32–4096 printable characters>
ORCHESTRATOR_OPERATOR_TOKEN=<distinct random server secret, 32–4096 printable characters>
ORCHESTRATOR_OPERATOR_ACTOR=<credential-bound principal, 2–120 characters>
```

Required mode fails startup if configuration is missing, invalid or duplicated.
All `/v1/incidents` routes require an `Authorization: Bearer …` credential.
The read credential permits synchronous/streamed diagnosis and reads, including history and events;
it does not permit approval, operation-status inspection or recovery checks. The operator credential additionally permits
approval, `GET /v1/incidents/{incident_id}/operation-status` and
`POST /v1/incidents/{incident_id}/recovery-check`. The approval request's `actor` must exactly match the configured operator
principal before any database approval claim or graph resume. Missing/invalid
credentials return 401; wrong scope or actor returns 403. Credentials are never
included in incident state, traces or health output. Existing dry-run, signed
MCP receipt, immutable decision and idempotency gates remain in force.

After an approval decision/operation claim has been committed, recovery errors
never release that claim automatically. Guardrail/format errors return redacted
409, tool/timeouts return redacted 502, and checkpoint/repository failures return
redacted 503. Any may occur after dispatch; none establishes non-execution.
Repeated requests with the same or a new key are denied even if the API reads a
stale waiting checkpoint. Inspection/manual review is required; no automatic
unlock or replay endpoint is provided. Diagnosis failures likewise omit raw
tool/model diagnostics. MCP initialization and tool error text is not forwarded
through the client, and MCP schema errors omit rejected inputs and validator
context (which can contain receipts). Semantic validation locations/types remain
available without those sensitive values.

Recovery checking is a separate explicit request with no body or query
parameters. It derives the original approved target/operation from the durable
checkpoint and resumes only `wait_for_recovery`, never dispatch. A recorded real
non-replay execution acknowledgement, applied generation/template and matching
intent are required; simulation, lost responses and legacy completed records
cannot be upgraded through this endpoint. The requesting API principal is traced,
or explicitly `unauthenticated-demo` in disabled mode. No new approval receipt is
issued. Fresh rollout and post-execution SLI observations are retained and may
move pending recovery to resolved; they do not establish causal efficacy.
Same-incident PostgreSQL advisory locks span checkpoint read/resume/save, with
crash-safe transaction release and a per-worker pool-headroom bulkhead. Busy
checks return 409/capacity 503; backend/persistence failures are redacted.
Responses use `Cache-Control: no-store`. The console offers a single explicit
authenticated recheck, not polling. Standalone recovery-panel tokens are kept
only in component memory and cleared after a successful check. Shared console
credentials remain only in page memory until cleared, trust/mode changes or a
reload; no browser storage, URL, trace or public environment variable receives
the credential.

Operation inspection has no request body/target parameters and rejects query
parameters. It derives tool, service, namespace, revision, actor and idempotency
key from a checkpoint's matching approved execution intent, never a stale
repository fallback. Legacy/no-intent or inconsistent records return 409;
missing incidents return 404. MCP calls use operator scope even though the tool
is read-only. A failed/invalid observation returns redacted 502 without resume,
receipt renewal, approval-claim release, or mutation retry. Observations are
returned separately, with incident status unchanged. Latest deployment marker
matches are not rollout-health evidence; missing markers do not establish
non-execution. The fixture provider always reports unknown. This GET does not
append to or rewrite the incident trace. It now writes a separate inspection
audit before calling the tool and finalizes it with a normalized observation or
redacted error type. PostgreSQL mode persists the audit; memory mode remains
process-local. Audit-start failure returns 503 before any query, and completion
failure returns 503 with an unfinished record rather than inventing success.
The audit binds the original tool/target/key/approved actor and records the
requesting principal plus auth mode. Disabled mode is explicitly recorded as
`unauthenticated-demo`, not proof of human identity. No signed receipt, token,
provider note or raw diagnostic is stored. Readers and operators can retrieve
the latest 100 records via `GET /v1/incidents/{incident_id}/operation-inspections`;
the query/history responses set `Cache-Control: no-store`. Unfinished audit
records do not justify retrying an operation. MCP's tool-call audit is separate.
The console now exposes a manual
read-only inspection panel for incidents with a prepared destructive intent.
It never forwards browser-supplied targets or applies observations as incident
status updates. A separate manual history refresh now displays up to 100 retained
audit records and their original operation binding; completed/unavailable/
unfinished queries remain distinct from incident resolution. The UI labels
disabled demo authorization and process-local memory retention explicitly,
rather than calling every record durable or identity-verified. The explicitly
selected HTTP-loopback demo sends no bearer token. Protected development mode
uses the memory-only credential connection described below. A 401/403 never
falls back to anonymous/demo mode or another credential.

## Development console connection

`NEXT_PUBLIC_ORCHESTRATOR_URL` contains only the backend URL, never a credential.
The console accepts HTTPS or strict HTTP loopback URLs and asks the user to trust
that exact origin/base path before sending requests. Reader and Operator token
inputs are password fields; approval also requires the configured operator actor.
Reader credentials are used for diagnosis, incident reads and history. Operator
credentials are used for approval, operation inspection and recovery checks.
Changing authorization mode or revoking trust clears credentials. Tokens and
actor are not restored after a reload. URL state retains only the incident ID
and uncertain-approval/diagnosis markers; reentry permits a read-only state refresh, not
automatic approval replay.

The transport fixes the origin, allowed route/method and credential scope per
request. It omits cookies/referrers, prohibits redirects and uses `no-store`.
Approval actors must match the entered actor before sending and the server-bound
principal before any approval claim. HTTP 401/403 is explicit rejection; unknown
transport/409/5xx approval outcomes require read-only inspection. This is a
development integration, not a recommended distribution of shared production
service credentials to browsers.

## Streamed diagnosis

`POST /v1/incidents/stream` has the same reader authorization and question schema
as synchronous diagnosis. It is one explicit request, not a background job or a
replay endpoint. Input policy rejection occurs before streaming headers. SSE
`incident` events carry full retained state with `id` equal to trace length;
`complete` carries the final diagnosis/pause state. Each progress snapshot is
read back from LangGraph's saver and saved to the canonical repository before
delivery. Redis publication is optional and cannot upgrade a failed canonical
write. Errors use static redacted messages/codes, never raw tool/model/database
diagnostics. A keep-alive does not assert progress or completion.

Disconnect cancels and closes the owned graph iteration. The retained prefix may
be inspected via incident/history GET, but neither reconnect nor UI refresh
silently starts/resumes a diagnosis or approves an action. Stream authentication
uses a fetch Authorization header, not credentials in a URL. A partial
`waiting_approval` snapshot is not a confirmed completed diagnosis stream.
The API checks for an actual saved human-node interrupt before claiming an
approval decision, and the workflow rechecks it before resume. A proposal's
`status` and pending `next` node alone are insufficient. Premature approve/reject
requests return 409 without consuming an approval or operation claim. A partial
proposal that was disconnected before the interrupt is only a readable prefix,
not an actionable paused workflow.

This is token-bound service authorization, **not human SSO, per-user identity,
tenant isolation or proof that a real person reviewed the incident**. Use TLS
and a trusted authenticated server-side console proxy or identity-aware gateway
for production. Do not place either token in a `NEXT_PUBLIC_*` variable, browser
storage or client bundle. The existing direct-browser demo intentionally sends
no server credential. The protected development connection can use required mode,
but is not production human identity. A production
proxy must map authenticated users to appropriate principals and scopes; do not
trust an arbitrary browser-supplied email header.

`app/chatgpt-auth.ts` is only a starter header-reading helper. It does not verify
the origin of identity headers and is not currently used as an authorization
gate. It must not be used to grant operator access on a publicly reachable
server. A verified OIDC/SSO or identity-aware gateway contract and role mapping
must be selected before enabling a production identity-based console connection.

Compose passes these server variables through but defaults to disabled for
local compatibility. The Kubernetes base requires authentication and maps
read/operator credentials and the operator actor from Secret keys.
The live-fault verifier reads the same server variables from its local environment,
uses read permission for diagnosis and operator permission only for rejection,
and fails before injection when required configuration is absent. It never
executes production remediation. The Kind script supplies public test-only
credentials, checks 401/403 boundaries, and rejects the proposed action; those
credentials are not production secrets. Script/manifests have been statically
validated, not rerun on a cluster in this revision. Production human identity
and the console proxy remain unimplemented.
No running deployment or private `.env.local` was changed by this revision.
