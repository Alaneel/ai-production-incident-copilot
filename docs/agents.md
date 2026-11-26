# Use OpsPilot from your coding or ops agent

OpsPilot serves an [MCP](https://modelcontextprotocol.io) endpoint, so Claude Code, Cursor or any other MCP client can call it. An agent can diagnose an incident, read the evidence, and point a human at a proposed fix. **It can't approve or execute anything.**

That split is the point. The agent gets read-only access to cited production evidence. Any mutation still goes through OpsPilot's approval boundary: a named human approves it, a signed receipt covers it, execution rechecks the exact target, and recovery is verified separately.

```text
agent ──MCP──▶ OpsPilot /mcp ──▶ read-only diagnosis (metrics, logs, traces, deploys, runbooks)
                    │
                    └─▶ proposed action ──▶ console inbox / Slack ──▶ named human approves
                                                                     └─▶ receipt-checked execution ──▶ recovery check
```

## Connect

The orchestrator serves the endpoint at `/mcp`: `http://localhost:8080/mcp` with the Compose stack. It uses the Streamable HTTP transport with plain JSON responses.

When the orchestrator requires API authentication (`ORCHESTRATOR_AUTH_MODE=required`), give the agent the **reader** token. The operator token works too, but it doesn't add any capability on this endpoint: no tool approves.

### Claude Code

```bash
claude mcp add --transport http opspilot http://localhost:8080/mcp \
  --header "Authorization: Bearer $OPSPILOT_READER_TOKEN"
```

Or commit a project-scoped `.mcp.json` so everyone on the team gets it:

```json
{
  "mcpServers": {
    "opspilot": {
      "type": "http",
      "url": "https://opspilot-api.internal/mcp",
      "headers": { "Authorization": "Bearer ${OPSPILOT_READER_TOKEN}" }
    }
  }
}
```

### Cursor and other clients

Cursor reads `.cursor/mcp.json` with the same `url` and `headers` shape. For any other client, use its "HTTP" or "Streamable HTTP" server type with the URL and an `Authorization` header.

## Tools

| Tool | What it does | Changes production? |
|---|---|---|
| `diagnose_incident` | Starts a diagnosis from a plain-language question, with an optional `service` and `environment`, and waits up to `wait_seconds` (default 20, maximum 25) for the result. | No. It starts a read-only investigation. |
| `get_incident` | Reads an incident's status, top three hypotheses, cited evidence, proposed action, approval decision and recovery status. | No |
| `list_incidents` | Lists recent incidents, optionally filtered by status, for example `waiting_approval`. | No |

Every result has a `console_url` for the human, and a `next_step` sentence the agent can act on. An example session:

```text
> diagnose_incident {"question": "Why is payment-service slow today?", "service": "payment-service"}

status:      waiting_approval
hypothesis:  Database connection pool exhaustion (0.87), with cited evidence ids
proposed:    Restore payment-service container images from revision 141
next_step:   A proposed action is waiting for a named human to approve or reject it in the
             OpsPilot console. Nothing has been changed. This endpoint cannot approve;
             share console_url with the on-call engineer.
console_url: https://opspilot.internal/?incident=INC-518097
```

If `NOTIFY_WEBHOOK_URL` is set, an agent-started diagnosis that needs a human posts the same message to your channel as an alert-started one ([notifications](integrate.md#notify-a-channel-when-a-human-is-needed)).

## Safety properties

- **No approval path.** The endpoint has no approve, reject or execute tool, and no tool accepts a receipt, actor or idempotency key. A test pins this.
- **Proposals come from evidence, not from the agent.** An agent can describe what it suspects, but OpsPilot derives any proposed action from its own evidence and playbooks. An agent can't talk it into a mutation.
- **Same input checks.** Questions pass the same prompt-injection and policy-bypass checks as the API. A question that asks to skip approval is refused.
- **Evidence is marked untrusted.** Log lines and runbook text can contain instructions. Results carry `evidence_is_untrusted: true`, and the server instructions tell the agent to treat evidence as data.
- **Bounded results.** A result holds at most 3 hypotheses and 12 evidence items, cited items first, with long text clipped. It's sized for a context window and never includes credentials or raw query output.
- **Bounded load.** At most 10 diagnoses run in the background at once, shared with Alertmanager-started ones. Further requests get an "OpsPilot is busy" tool error instead of queueing.
- **Origin check.** A request with a browser `Origin` not in `CORS_ALLOWED_ORIGINS` is refused, which blocks DNS-rebinding attacks from a web page.

## Raw evidence tools

Agents that want to run their own queries can also connect to the MCP server behind the orchestrator (`http://localhost:8082/mcp` in Compose) with `MCP_READ_TOKEN`. It exposes `query_metrics`, `search_logs`, `query_traces`, `get_service_dependencies`, `get_recent_deployments` and the runbook search. Its `restart_deployment` and `rollback_release` tools only dry-run with a reader token. Executing needs the operator scope **and** a signed human approval receipt, which only the orchestrator issues after a recorded approval.

## Limits

- Answers are deterministic unless the orchestrator has a model configured. See the [project reference](project-reference.md).
- The endpoint answers each request in full. It doesn't stream progress or push notifications, so for a long diagnosis, call `get_incident` again.
- Like the API, it uses scoped tokens rather than per-user identity. OAuth for MCP clients is not implemented.
