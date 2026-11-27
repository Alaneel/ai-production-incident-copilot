from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import OrderedDict
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from typing import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import anyio
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import Error as PostgresError
from redis.exceptions import RedisError

from .agent_mcp import (
    INSTRUCTIONS as AGENT_INSTRUCTIONS,
    AgentToolError,
    AgentTools,
    DiagnoseArguments,
    negotiate_version,
    tool_definitions as agent_tool_definitions,
)
from .anthropic_reasoning import AnthropicMessagesHypothesisGenerator
from .alerts import AlertDeduplicator, AlertmanagerWebhook, alert_settings, console_link, triage_alerts
from .approval import ApprovalReceiptSigner
from .console_static import ConsoleStaticMiddleware, load_console
from .notifications import NotificationSettings, build_notification, send_notification
from .api_auth import ApiAuthorization
from .guardrails import GuardrailViolation, validate_input
from .langgraph_workflow import LangGraphIncidentWorkflow
from .mcp_client import McpTelemetryTools, McpToolError
from .models import (
    ApprovalRequest,
    IncidentQuestion,
    IncidentState,
    IncidentStatus,
    new_incident_id,
)
from .reasoning import (
    ModelGenerationError,
    OpenAIResponsesHypothesisGenerator,
)
from .store import (
    ApprovalConflictError,
    IncidentRepository,
    InMemoryIncidentStore,
    PostgresIncidentRepository,
)
from .task_state import (
    InMemoryTaskStateStore,
    RedisTaskStateStore,
    TaskStateStore,
)
from .tools import DemoTelemetryTools


TERMINAL_OR_PAUSED = {
    IncidentStatus.WAITING_APPROVAL,
    IncidentStatus.DIAGNOSED,
    IncidentStatus.INSUFFICIENT_EVIDENCE,
    IncidentStatus.REMEDIATION_FAILED,
    IncidentStatus.REMEDIATION_PENDING,
    IncidentStatus.RESOLVED,
    IncidentStatus.REJECTED,
}
logger = logging.getLogger("incident_copilot.api")
EVENT_CACHE_READ_TIMEOUT_SECONDS = 2.0
EVENT_CACHE_PUBLISH_TIMEOUT_SECONDS = 2.0
EVENT_CACHE_STARTUP_TIMEOUT_SECONDS = 2.0
DIAGNOSIS_STREAM_HEARTBEAT_SECONDS = 5.0
DIAGNOSIS_STREAM_MAX_FRAME_BYTES = 8 * 1024 * 1024
# Background diagnoses (alerts and agents) beyond this many are refused, not queued.
MAX_PENDING_BACKGROUND_DIAGNOSES = 10
# Background diagnoses that stopped early, remembered per process so readers
# are told the run failed instead of seeing a partial checkpoint as running.
MAX_REMEMBERED_FAILURES = 500
EVENT_DELIVERY_RESPONSE_HEADERS = {
    "X-Incident-Event-Delivery": {
        "description": "Event delivery after canonical persistence. checkpoint-fallback means delivery was not confirmed; it does not imply cache rollback. May be absent when publication was not attempted.",
        "schema": {"type": "string", "enum": ["event-store", "checkpoint-fallback"]},
    },
    "X-Incident-Event-Delivery-Reason": {
        "description": "Sanitized reason for checkpoint fallback; absent on confirmed event-store delivery. No provider details or credentials.",
        "schema": {"type": "string", "enum": ["cache-timeout", "cache-unavailable"]},
    },
}


async def _publish_incident_events(request: Request, state: IncidentState, response: Response) -> None:
    """Called only after canonical state persistence; cache delivery is optional."""
    try:
        await asyncio.wait_for(_task_store(request).publish(state), timeout=EVENT_CACHE_PUBLISH_TIMEOUT_SECONDS)
    except (RedisError, TimeoutError) as error:
        # A lost acknowledgement may mean Redis committed the write. Do not
        # retry or claim cache rollback; clients can read canonical state/SSE.
        response.headers["X-Incident-Event-Delivery"] = "checkpoint-fallback"
        response.headers["X-Incident-Event-Delivery-Reason"] = "cache-timeout" if isinstance(error, TimeoutError) else "cache-unavailable"
    else:
        response.headers["X-Incident-Event-Delivery"] = "event-store"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.api_authorization = ApiAuthorization.from_environment(os.environ)
    # A configured bundle is verified against its manifest here; a modified or
    # incomplete one stops startup instead of serving a partial console.
    app.state.console = load_console(os.getenv("CONSOLE_STATIC_DIR"))
    # Repository pool capacity is ten; observers must leave connections for
    # canonical writes while their advisory-lock transactions are held.
    app.state.recovery_check_slots = asyncio.Semaphore(4)
    app.state.alert_settings = alert_settings(os.environ)
    app.state.alert_slots = asyncio.Semaphore(app.state.alert_settings["max_concurrent"])
    app.state.alert_dedupe = AlertDeduplicator(ttl_seconds=app.state.alert_settings["dedupe_seconds"])
    app.state.alert_tasks = set()
    app.state.failed_diagnoses = OrderedDict()
    app.state.notifications = NotificationSettings.from_environment(os.environ)
    async with AsyncExitStack() as stack:
        async with _runtime_lifespan(app, stack):
            try:
                yield
            finally:
                # Alert-started diagnoses are read-only; cancelling one leaves
                # its retained checkpoint prefix for read-only inspection.
                for task in list(app.state.alert_tasks):
                    task.cancel()
                if app.state.alert_tasks:
                    await asyncio.gather(*app.state.alert_tasks, return_exceptions=True)


@asynccontextmanager
async def _runtime_lifespan(app: FastAPI, stack: AsyncExitStack) -> AsyncIterator[None]:
    database_url = os.getenv("DATABASE_URL")
    redis_url = os.getenv("REDIS_URL")
    mcp_url = os.getenv("MCP_SERVER_URL")
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

    if database_url:
        checkpointer = await stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(database_url)
        )
        await checkpointer.setup()
        repository: IncidentRepository = await PostgresIncidentRepository.connect(
            database_url
        )
        persistence_mode = "postgres"
    else:
        checkpointer = InMemorySaver()
        repository = InMemoryIncidentStore()
        persistence_mode = "memory"

    stack.push_async_callback(repository.close)
    if redis_url:
        # Cache connectivity is optional; keep this client so subsequent requests
        # can reconnect naturally. Never substitute a misleading memory cache.
        redis_task_store = RedisTaskStateStore.from_url(redis_url)
        task_store: TaskStateStore = redis_task_store
        stack.push_async_callback(task_store.close)
        try:
            await asyncio.wait_for(redis_task_store.redis.ping(), timeout=EVENT_CACHE_STARTUP_TIMEOUT_SECONDS)
        except TimeoutError:
            event_cache_startup = "timeout"
        except RedisError:
            event_cache_startup = "unavailable"
        else:
            event_cache_startup = "ready"
        task_state_mode = "redis"
    else:
        task_store = InMemoryTaskStateStore()
        stack.push_async_callback(task_store.close)
        task_state_mode = "memory"
        event_cache_startup = "not_configured"

    if mcp_url:
        tools = McpTelemetryTools(
            url=mcp_url,
            read_token=os.getenv("MCP_READ_TOKEN", "local-read-token"),
            operator_token=os.getenv("MCP_OPERATOR_TOKEN", "local-operator-token"),
        )
        tool_mode = "mcp"
    else:
        tools = DemoTelemetryTools()
        tool_mode = "fixture"
    if isinstance(tools, McpTelemetryTools):
        stack.push_async_callback(tools.close)

    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL")
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    anthropic_model = os.getenv("ANTHROPIC_MODEL")
    hypothesis_generator = None
    # A provider is enabled only by its key AND an explicit model, so a key
    # that happens to be in the environment never starts paid calls alone.
    if openai_api_key and openai_model and anthropic_api_key and anthropic_model:
        raise RuntimeError(
            "Both OpenAI and Anthropic reasoning are configured; set only one of "
            "OPENAI_MODEL or ANTHROPIC_MODEL."
        )
    if anthropic_api_key and anthropic_model:
        hypothesis_generator = AnthropicMessagesHypothesisGenerator(
            api_key=anthropic_api_key,
            model=anthropic_model,
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
            effort=os.getenv("ANTHROPIC_EFFORT") or None,
        )
        reasoning_mode = f"anthropic:{anthropic_model}"
        stack.push_async_callback(hypothesis_generator.close)
    elif openai_api_key and openai_model:
        hypothesis_generator = OpenAIResponsesHypothesisGenerator(
            api_key=openai_api_key,
            model=openai_model,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        reasoning_mode = f"openai:{openai_model}"
        stack.push_async_callback(hypothesis_generator.close)
    else:
        reasoning_mode = "deterministic"

    workflow = LangGraphIncidentWorkflow(
        tools=tools,
        checkpointer=checkpointer,
        approval_signer=ApprovalReceiptSigner(
            os.getenv("MCP_APPROVAL_SECRET", "local-approval-secret-change-me")
        ),
        hypothesis_generator=hypothesis_generator,
    )
    app.state.workflow = workflow
    app.state.repository = repository
    app.state.task_store = task_store
    app.state.runtime_modes = {
        "api_auth": app.state.api_authorization.mode,
        "checkpoint": persistence_mode,
        "task_state": task_state_mode,
        "event_cache_startup": event_cache_startup,
        "tools": tool_mode,
        "reasoning": reasoning_mode,
        "console": "bundled" if app.state.console else "not_bundled",
    }

    yield


app = FastAPI(
    title="AI Production Incident Copilot",
    version="0.2.0",
    description="Durable LangGraph diagnosis with MCP tools and guarded remediation.",
    lifespan=lifespan,
)
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    ).split(",")
    if origin.strip()
]
@app.middleware("http")
async def authorize_incident_api(request: Request, call_next):
    if request.url.path.startswith("/v1/incidents") and request.method != "OPTIONS":
        try:
            request.state.authenticated_actor = request.app.state.api_authorization.authorize(
                request.headers.get("Authorization"),
                approval=(request.method == "POST" and request.url.path.endswith("/approval"))
                or request.url.path.endswith(("/operation-status", "/recovery-check")),
            )
        except HTTPException as error:
            return JSONResponse({"detail": error.detail}, status_code=error.status_code,
                                headers={**(error.headers or {}), "Cache-Control": "no-store"})
    response = await call_next(request)
    if request.url.path.endswith(("/operation-status", "/operation-inspections", "/recovery-check")):
        response.headers["Cache-Control"] = "no-store"
    return response


# CORS wraps authorization so browsers can read 401/403 responses as well.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
    expose_headers=list(EVENT_DELIVERY_RESPONSE_HEADERS),
)
# Outermost: exact console bundle paths are answered here; nothing else is touched.
app.add_middleware(ConsoleStaticMiddleware)


def _workflow(request: Request) -> LangGraphIncidentWorkflow:
    return request.app.state.workflow


def _repository(request: Request) -> IncidentRepository:
    return request.app.state.repository


def _task_store(request: Request) -> TaskStateStore:
    return request.app.state.task_store


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    return {"status": "ok", **request.app.state.runtime_modes}


@app.post(
    "/v1/incidents",
    response_model=IncidentState,
    status_code=status.HTTP_201_CREATED,
    responses={201: {"headers": EVENT_DELIVERY_RESPONSE_HEADERS}},
)
async def create_incident(
    request_body: IncidentQuestion, request: Request, response: Response
) -> IncidentState:
    try:
        state = await _workflow(request).start(request_body)
        await _repository(request).save(state)
        await _publish_incident_events(request, state, response)
        return state
    except GuardrailViolation as error:
        raise HTTPException(status_code=400, detail="Incident request was rejected by input safety checks.") from error
    except McpToolError as error:
        raise HTTPException(status_code=502, detail="Incident tool service is unavailable; no diagnosis was confirmed.") from error
    except ModelGenerationError as error:
        raise HTTPException(status_code=502, detail="Model diagnosis is unavailable; no diagnosis was confirmed.") from error


@app.post("/v1/incidents/stream", response_class=StreamingResponse,
          responses={200: {"content": {"text/event-stream": {}}}})
async def create_incident_stream(request_body: IncidentQuestion, request: Request) -> StreamingResponse:
    """A single diagnosis run, streamed only after checkpoint/canonical writes.

    Disconnect cancels the run, not its retained prefix. There is no background
    replay, approval or automatic resume; GET/history remains the recovery path.
    """
    try:
        validate_input(request_body)
    except GuardrailViolation as error:
        raise HTTPException(status_code=400, detail="Incident request was rejected by input safety checks.") from error

    def frame(event: str, state: IncidentState) -> str:
        payload = json.dumps(state.model_dump(mode="json"), separators=(",", ":"))
        if len(payload.encode("utf-8")) > DIAGNOSIS_STREAM_MAX_FRAME_BYTES:
            raise ValueError("Diagnosis snapshot exceeds the delivery limit.")
        return f"id: {len(state.trace)}\nevent: {event}\ndata: {payload}\n\n"

    async def snapshots() -> AsyncIterator[str]:
        latest: IncidentState | None = None
        retained_id: str | None = None
        stream = _workflow(request).start_stream(request_body)
        pending: asyncio.Task | None = None
        try:
            while True:
                pending = asyncio.create_task(anext(stream))
                while not pending.done():
                    await asyncio.wait({pending}, timeout=DIAGNOSIS_STREAM_HEARTBEAT_SECONDS)
                    if not pending.done():
                        yield ": keep-alive\n\n"
                try:
                    state = pending.result()
                except StopAsyncIteration:
                    break
                finally:
                    pending = None
                # A saver read is authority; Redis is only the final delivery
                # cache. Never claim completion after a failed canonical write.
                retained_id = state.incident_id
                await _repository(request).save(state)
                latest = state
                yield frame("incident", state)
            if latest is None or latest.status not in {
                IncidentStatus.WAITING_APPROVAL, IncidentStatus.DIAGNOSED,
                IncidentStatus.INSUFFICIENT_EVIDENCE, IncidentStatus.REMEDIATION_FAILED,
            }:
                raise ValueError("Diagnosis did not retain a terminal or paused state.")
            delivery = Response()
            await _publish_incident_events(request, latest, delivery)
            yield frame("complete", latest)
        except Exception as error:
            if isinstance(error, GuardrailViolation):
                code, message = "input_rejected", "Diagnosis was stopped by safety checks."
            elif isinstance(error, McpToolError):
                code, message = "tool_unavailable", "A diagnostic tool is unavailable; diagnosis was not completed."
            elif isinstance(error, ModelGenerationError):
                code, message = "model_unavailable", "Model diagnosis is unavailable; diagnosis was not completed."
            elif isinstance(error, PostgresError):
                code, message = "persistence_unavailable", "Diagnosis persistence is unavailable; completion was not confirmed."
            else:
                code, message = "diagnosis_unavailable", "Diagnosis was interrupted; completion was not confirmed."
            payload = {"code": code, "message": message}
            if retained_id is not None:
                payload["incident_id"] = retained_id
            yield f"event: error\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
        finally:
            # Starlette disconnect cancellation must not leave an owned graph
            # task running after the UI has discarded its request generation.
            with anyio.CancelScope(shield=True):
                if pending is not None:
                    pending.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await pending
                await stream.aclose()

    return StreamingResponse(snapshots(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })


async def _diagnose_alert(app: FastAPI, request_body: IncidentQuestion, incident_id: str, alertname: str) -> None:
    async with app.state.alert_slots:
        try:
            state = await app.state.workflow.start(request_body, incident_id=incident_id)
            await app.state.repository.save(state)
            with suppress(RedisError, TimeoutError):
                await asyncio.wait_for(app.state.task_store.publish(state), timeout=EVENT_CACHE_PUBLISH_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - background run; report class only
            logger.warning("alert diagnosis %s did not complete: %s", incident_id, type(error).__name__)
            failures = app.state.failed_diagnoses
            failures[incident_id] = type(error).__name__
            while len(failures) > MAX_REMEMBERED_FAILURES:
                failures.popitem(last=False)
            return
    # Nobody is watching an alert-started run: point a human at the result.
    # Sent only after the canonical save; delivery never alters the incident.
    notification = build_notification(state, alertname=alertname,
                                      console_url=console_link(app.state.alert_settings["console_url"], incident_id))
    if notification is not None and app.state.notifications.url is not None:
        delivered = await send_notification(app.state.notifications, notification)
        if not delivered:
            logger.warning("notification for %s was not acknowledged", incident_id)


def _start_background_diagnosis(app: FastAPI, request_body: IncidentQuestion, label: str) -> tuple[str, asyncio.Task]:
    incident_id = new_incident_id()
    task = asyncio.create_task(_diagnose_alert(app, request_body, incident_id, label))
    app.state.alert_tasks.add(task)
    task.add_done_callback(app.state.alert_tasks.discard)
    return incident_id, task


async def _read_incident(app: FastAPI, incident_id: str) -> IncidentState | None:
    state = await app.state.workflow.get(incident_id)
    return state if state is not None else await app.state.repository.get(incident_id)


def _agent_tools(app: FastAPI) -> AgentTools:
    async def start(arguments: DiagnoseArguments) -> tuple[str, asyncio.Task]:
        request_body = IncidentQuestion(question=arguments.question, environment=arguments.environment,
                                        service=arguments.service)
        try:
            validate_input(request_body)
        except GuardrailViolation:
            raise AgentToolError("The question was rejected by input safety checks.") from None
        if len(app.state.alert_tasks) >= MAX_PENDING_BACKGROUND_DIAGNOSES:
            raise AgentToolError("OpsPilot is busy with other diagnoses. Try again in a minute.")
        return _start_background_diagnosis(app, request_body, "Requested by an MCP client")

    async def list_recent(limit: int, incident_status: str | None) -> list[dict]:
        return await app.state.repository.list_recent(limit=limit, status=incident_status)

    return AgentTools(start=start, read=lambda incident_id: _read_incident(app, incident_id),
                      list_recent=list_recent, failure=app.state.failed_diagnoses.get,
                      console_link=lambda incident_id: console_link(app.state.alert_settings["console_url"], incident_id))


def _rpc(request_id, *, result=None, error=None, version=None, http_status=200) -> JSONResponse:
    body = {"jsonrpc": "2.0", "id": request_id}
    body.update({"result": result} if error is None else {"error": error})
    headers = {"Cache-Control": "no-store"}
    if version:
        headers["MCP-Protocol-Version"] = version
    return JSONResponse(body, status_code=http_status, headers=headers)


@app.post("/mcp")
async def agent_mcp(request: Request) -> Response:
    """MCP (Streamable HTTP, JSON responses) for external agents.

    Reader and operator credentials both work, and neither can approve
    through this endpoint: it has no approval or execution tool."""
    origin = request.headers.get("Origin")
    if origin is not None and origin not in cors_origins:
        return _rpc(None, error={"code": -32001, "message": "Origin is not allowed."}, http_status=403)
    try:
        request.app.state.api_authorization.authorize(request.headers.get("Authorization"), approval=False)
    except HTTPException as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status_code,
                            headers={**(error.headers or {}), "Cache-Control": "no-store"})
    try:
        message = await request.json()
    except ValueError:
        return _rpc(None, error={"code": -32700, "message": "Parse error."}, http_status=400)
    if isinstance(message, list):
        return _rpc(None, error={"code": -32600, "message": "JSON-RPC batching is not supported."}, http_status=400)
    if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str)
            or ("id" in message and (isinstance(message["id"], bool) or not isinstance(message["id"], (str, int))))):
        return _rpc(None, error={"code": -32600, "message": "Invalid JSON-RPC request."}, http_status=400)
    method, request_id = message["method"], message.get("id")
    version = request.headers.get("MCP-Protocol-Version")
    if request_id is None:
        return Response(status_code=202)  # notifications and responses need no reply
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _rpc(request_id, error={"code": -32602, "message": "params must be an object."})
    if method == "initialize":
        negotiated = negotiate_version(params.get("protocolVersion"))
        return _rpc(request_id, version=negotiated, result={
            "protocolVersion": negotiated,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "opspilot", "title": "OpsPilot incident copilot", "version": app.version},
            "instructions": AGENT_INSTRUCTIONS,
        })
    if method == "ping":
        return _rpc(request_id, result={}, version=version)
    if method == "tools/list":
        return _rpc(request_id, result={"tools": agent_tool_definitions()}, version=version)
    if method != "tools/call":
        return _rpc(request_id, error={"code": -32601, "message": f"Method not found: {method[:80]}"}, version=version)
    name, arguments = params.get("name"), params.get("arguments") or {}
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return _rpc(request_id, error={"code": -32602, "message": "Invalid tools/call parameters."}, version=version)
    try:
        output = await _agent_tools(request.app).call(name, arguments)
    except LookupError:
        return _rpc(request_id, error={"code": -32602, "message": f"Unknown tool: {name[:80]}"}, version=version)
    except AgentToolError as error:
        return _rpc(request_id, version=version, result={
            "content": [{"type": "text", "text": str(error)}], "isError": True})
    except (McpToolError, PostgresError, TimeoutError):
        return _rpc(request_id, version=version, result={
            "content": [{"type": "text", "text": "OpsPilot could not read its incident store or tools right now."}],
            "isError": True})
    logger.info(json.dumps({"event": "agent_mcp_call", "tool": name,
                            "incident_id": output.get("incident_id")}, separators=(",", ":")))
    return _rpc(request_id, version=version, result={
        "content": [{"type": "text", "text": json.dumps(output, separators=(",", ":"), default=str)}],
        "structuredContent": output,
        "isError": False,
    })


@app.post("/v1/incidents/alerts/alertmanager", status_code=status.HTTP_202_ACCEPTED)
@app.post("/v1/incidents/alerts/grafana", status_code=status.HTTP_202_ACCEPTED)
async def ingest_alertmanager(payload: AlertmanagerWebhook, request: Request) -> dict:
    """Alertmanager and Grafana Alerting webhook receiver. Grafana's webhook
    contact point sends the Alertmanager alert shape plus extra fields, which
    are ignored. Each firing alert with a service label starts one read-only
    diagnosis; remediation still waits for a human.

    Returns immediately: diagnoses run in the background and are read with
    GET /v1/incidents/{incident_id} or opened from console_url."""
    settings = request.app.state.alert_settings
    triage = triage_alerts(payload, service_labels=settings["service_labels"],
                           default_environment=settings["default_environment"])
    accepted: list[dict] = []
    skipped = list(triage.skipped)
    dedupe: AlertDeduplicator = request.app.state.alert_dedupe
    for item in triage.requests:
        existing = dedupe.existing(item.key)
        if existing:
            skipped.append({"alertname": item.alertname, "service": item.service,
                            "reason": "already diagnosing", "incident_id": existing})
            continue
        if len(accepted) >= settings["max_per_webhook"]:
            skipped.append({"alertname": item.alertname, "service": item.service, "reason": "per-notification limit reached"})
            continue
        try:
            validate_input(item.request)
        except GuardrailViolation:
            skipped.append({"alertname": item.alertname, "service": item.service, "reason": "rejected by input safety checks"})
            continue
        incident_id = new_incident_id()
        dedupe.remember(item.key, incident_id)
        task = asyncio.create_task(_diagnose_alert(request.app, item.request, incident_id, item.alertname))
        request.app.state.alert_tasks.add(task)
        task.add_done_callback(request.app.state.alert_tasks.discard)
        accepted.append({"incident_id": incident_id, "alertname": item.alertname, "service": item.service,
                         "environment": item.request.environment,
                         "console_url": console_link(settings["console_url"], incident_id)})
    return {"accepted": accepted, "skipped": skipped}


@app.get("/v1/incidents")
async def list_incidents(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    incident_status: IncidentStatus | None = Query(default=None, alias="status"),
) -> dict:
    """Recently updated incidents, newest first, as bounded summaries.

    Lists what the canonical store has retained. A diagnosis still running in
    the background appears once its first state is saved."""
    incidents = await _repository(request).list_recent(
        limit=limit, status=incident_status.value if incident_status else None)
    return {"incidents": incidents, "count": len(incidents)}


@app.get("/v1/incidents/{incident_id}", response_model=IncidentState)
async def get_incident(incident_id: str, request: Request) -> IncidentState:
    state = await _workflow(request).get(incident_id)
    if state is None:
        state = await _repository(request).get(incident_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return state


@app.get(
    "/v1/incidents/{incident_id}/history",
    response_model=list[IncidentState],
)
async def get_incident_history(
    incident_id: str, request: Request
) -> list[IncidentState]:
    history = await _workflow(request).history(incident_id)
    if not history:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return history


@app.get("/v1/incidents/{incident_id}/operation-status")
async def inspect_operation_status(incident_id: str, request: Request) -> dict:
    if request.query_params:
        raise HTTPException(status_code=400, detail="Inspection does not accept target overrides.")
    audit_id = f"inspection_{uuid4().hex}"
    recorded = False
    repository = _repository(request)

    async def record_request(context: dict):
        nonlocal recorded
        await repository.begin_inspection(audit_id, {
            **context, "request_principal": request.state.authenticated_actor or "unauthenticated-demo",
            "auth_mode": request.app.state.api_authorization.mode, "read_only": True,
        })
        recorded = True

    try:
        result = await _workflow(request).inspect_operation(incident_id, on_prepared=record_request)
    except GuardrailViolation as error:
        raise HTTPException(status_code=409, detail="No matching approved execution intent is available.") from error
    except PostgresError as error:
        raise HTTPException(status_code=503, detail="Inspection audit is unavailable; no operation was retried.") from error
    except (McpToolError, TimeoutError, ValueError) as error:
        if recorded:
            try:
                await repository.finish_inspection(audit_id, {
                    "status": "unavailable", "error_type": type(error).__name__,
                    "completed_at": datetime.now(UTC).isoformat(),
                })
            except PostgresError as audit_error:
                raise HTTPException(status_code=503, detail="Inspection audit completion is unavailable; no operation was retried.") from audit_error
        raise HTTPException(status_code=502, detail="Operation inspection is unavailable; execution was not retried.") from error
    if result is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    try:
        await repository.finish_inspection(audit_id, {
            "status": "completed", "observation": result["observation"],
            "completed_at": datetime.now(UTC).isoformat(),
        })
    except PostgresError as error:
        raise HTTPException(status_code=503, detail="Inspection audit completion is unavailable; no operation was retried.") from error
    return {**result, "inspection_audit_id": audit_id}


@app.get("/v1/incidents/{incident_id}/operation-inspections")
async def get_operation_inspections(incident_id: str, request: Request) -> list[dict]:
    if await _repository(request).get(incident_id) is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return await _repository(request).inspection_history(incident_id)


@app.post("/v1/incidents/{incident_id}/recovery-check", response_model=IncidentState,
          responses={200: {"headers": EVENT_DELIVERY_RESPONSE_HEADERS}})
async def recheck_incident_recovery(incident_id: str, request: Request, response: Response) -> IncidentState:
    if request.query_params or await request.body():
        raise HTTPException(status_code=400, detail="Recovery inspection accepts no action or target overrides.")
    slots = request.app.state.recovery_check_slots
    if slots.locked():
        raise HTTPException(status_code=503, detail="Recovery inspection capacity is busy. Remediation was not retried.")
    try:
        # Fresh checkpoint inspection and canonical persistence are both inside
        # the repository's cross-worker lock. Crash/rollback releases the lock.
        async with slots, _repository(request).recovery_observation_lock(incident_id) as acquired:
            if not acquired:
                raise HTTPException(status_code=409, detail="A recovery observation is already in progress. Remediation was not retried.")
            state = await _workflow(request).recheck_recovery(incident_id,
                request_actor=request.state.authenticated_actor or "unauthenticated-demo")
            if state is None:
                raise HTTPException(status_code=404, detail="Incident not found.")
            await _repository(request).save(state)
            await _publish_incident_events(request, state, response)
            return state
    except GuardrailViolation as error:
        raise HTTPException(status_code=409, detail="No matching durable recovery observation is waiting. Remediation was not retried.") from error
    except (McpToolError, TimeoutError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Recovery inspection is unavailable. Remediation was not retried.") from error
    except PostgresError as error:
        raise HTTPException(status_code=503, detail="Recovery observation persistence is unavailable. Remediation was not retried.") from error


@app.post(
    "/v1/incidents/{incident_id}/approval",
    response_model=IncidentState,
    responses={200: {"headers": EVENT_DELIVERY_RESPONSE_HEADERS}},
)
async def decide_remediation(
    incident_id: str, request_body: ApprovalRequest, request: Request, response: Response
) -> IncidentState:
    actor = request.state.authenticated_actor
    if actor is not None and request_body.actor != actor:
        raise HTTPException(status_code=403, detail="Approval actor must match the authenticated operator.")
    current = await _workflow(request).get(incident_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    if current.status != IncidentStatus.WAITING_APPROVAL:
        if current.status in {IncidentStatus.RESOLVED, IncidentStatus.REJECTED}:
            return current
        raise HTTPException(
            status_code=409, detail="Incident is not waiting for approval."
        )

    if not await _workflow(request).is_waiting_for_approval(incident_id):
        raise HTTPException(status_code=409,
                            detail="Diagnosis has not reached a retained approval interrupt. No decision was claimed.")

    repository = _repository(request)
    try:
        claimed = await repository.claim_approval(current, request_body)
    except ApprovalConflictError as error:
        raise HTTPException(status_code=409, detail="Approval conflicts with a previously recorded decision.") from error
    except PostgresError as error:
        raise HTTPException(
            status_code=503, detail="Approval could not be durably recorded; execution was not started."
        ) from error
    if not claimed:
        raise HTTPException(status_code=409, detail="An approval decision is already claimed for this incident.")

    try:
        state = await _workflow(request).resume_with_approval(
            incident_id,
            request_body,
        )
        await repository.save(state)
        await _publish_incident_events(request, state, response)
        return state
    except (GuardrailViolation, ValueError) as error:
        raise HTTPException(status_code=409, detail="Approval recovery requires review; the recorded decision and operation lock were retained.") from error
    except (McpToolError, TimeoutError) as error:
        raise HTTPException(status_code=502, detail="Approval recovery is unavailable; execution may have started. Refresh status instead of resubmitting.") from error
    except PostgresError as error:
        raise HTTPException(status_code=503, detail="Approval persistence is unavailable; execution may have started. The operation lock was retained.") from error


@app.get("/v1/incidents/{incident_id}/events")
async def stream_incident_events(
    incident_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    if await _workflow(request).get(incident_id) is None:
        raise HTTPException(status_code=404, detail="Incident not found.")

    async def event_stream() -> AsyncIterator[str]:
        sequence = after
        idle_polls = 0
        last_replay_reason = None
        while idle_polls < 30:
            if await request.is_disconnected():
                return
            state = await _workflow(request).get(incident_id)
            if state is None:
                return
            retained = [event for event in state.trace if event.sequence > sequence]
            replay_reason = None
            try:
                events = await asyncio.wait_for(
                    _task_store(request).events_after(incident_id, sequence),
                    timeout=EVENT_CACHE_READ_TIMEOUT_SECONDS,
                )
            except (RedisError, ValueError, TimeoutError) as error:
                events = retained
                replay_reason = ("task_store_timeout" if isinstance(error, TimeoutError) else
                    "task_store_unavailable" if isinstance(error, RedisError) else "task_store_invalid")
            else:
                # Redis is a delivery cache, not the authority for persisted
                # trace identity. Missing/partial/divergent cache data must not
                # skip retained events or advance the cursor past a gap.
                if events != retained:
                    events = retained
                    replay_reason = "task_store_incomplete"
            if replay_reason and replay_reason != last_replay_reason:
                yield (
                    "event: replay_source\n"
                    f"data: {json.dumps({'source': 'workflow_checkpoint', 'checkpoint_backend': request.app.state.runtime_modes['checkpoint'], 'reason': replay_reason, 'read_only': True})}\n\n"
                )
            last_replay_reason = replay_reason
            if events:
                idle_polls = 0
                for event in events:
                    sequence = max(sequence, event.sequence)
                    yield (
                        f"id: {event.sequence}\n"
                        f"event: trace\n"
                        f"data: {json.dumps(event.model_dump(mode='json'), separators=(',', ':'))}\n\n"
                    )
            else:
                idle_polls += 1
                yield ": keep-alive\n\n"

            if (
                state is not None
                and state.status in TERMINAL_OR_PAUSED
                and sequence >= max((event.sequence for event in state.trace), default=0)
            ):
                yield (
                    "event: state\n"
                    f"data: {json.dumps({'status': state.status.value, 'current_step': state.current_step.value})}\n\n"
                )
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
