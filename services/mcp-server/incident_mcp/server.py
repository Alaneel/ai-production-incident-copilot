from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .gateway import TelemetryGatewayClient, TelemetryGatewayError
from .deployments import KubernetesDeploymentReader
from .kubernetes import (
    KubernetesRemediationError,
    KubernetesRemediator,
    in_cluster_api_url,
)
from .security import (
    ApprovalReceiptVerifier,
    AuthorizationError,
    Principal,
    TokenAuthenticator,
)
from .tools import ToolRegistry

PROTOCOL_VERSION = "2025-06-18"
ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.getenv(
        "MCP_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
}

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger("incident-mcp")

authenticator = TokenAuthenticator(
    read_token=os.getenv("MCP_READ_TOKEN", "local-read-token"),
    operator_token=os.getenv("MCP_OPERATOR_TOKEN", "local-operator-token"),
)
approval_verifier = ApprovalReceiptVerifier(
    os.getenv("MCP_APPROVAL_SECRET", "local-approval-secret-change-me")
)
gateway_url = os.getenv("TELEMETRY_GATEWAY_URL")
gateway = (
    TelemetryGatewayClient(
        base_url=gateway_url,
        token=os.getenv("TELEMETRY_GATEWAY_TOKEN", "local-development-token"),
    )
    if gateway_url
    else None
)
remediator = (
    KubernetesRemediator(
        api_url=in_cluster_api_url(),
        allowed_namespaces={
            item.strip()
            for item in os.getenv(
                "MCP_ALLOWED_NAMESPACES", "incident-copilot"
            ).split(",")
            if item.strip()
        },
    )
    if os.getenv("KUBERNETES_REMEDIATION_ENABLED", "false").lower() == "true"
    else None
)
registry = ToolRegistry(
    approval_verifier,
    gateway=gateway,
    remediator=remediator,
    deployment_reader=(KubernetesDeploymentReader(
        include_events=os.getenv("KUBERNETES_DEPLOYMENT_EVENTS_ENABLED", "false").lower() == "true",
        api_url=in_cluster_api_url(), allowed_namespaces={item.strip() for item in os.getenv(
            "MCP_ALLOWED_NAMESPACES", "incident-copilot").split(",") if item.strip()})
        if os.getenv("KUBERNETES_DEPLOYMENT_DISCOVERY_ENABLED", "false").lower() == "true" else None),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        if gateway is not None:
            await gateway.close()
        if remediator is not None:
            await remediator.close()
        if registry.deployment_reader is not None:
            await registry.deployment_reader.close()


app = FastAPI(
    title="Incident Copilot MCP Server",
    version="0.2.0",
    lifespan=lifespan,
)
_rate_windows: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = asyncio.Lock()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "protocol_version": PROTOCOL_VERSION}


@app.post("/mcp")
async def mcp(
    request: Request,
    authorization: str | None = Header(default=None),
    origin: str | None = Header(default=None),
) -> Response:
    started = time.perf_counter()
    request_id: str | int | None = None
    method = "unknown"
    principal: Principal | None = None
    tool_name: str | None = None
    tool_read_only: bool | None = None
    outcome = "error"
    try:
        _validate_origin(origin)
        principal = authenticator.authenticate(authorization)
        await _check_rate_limit(principal.subject)
        message = await request.json()
        if isinstance(message, list):
            return _error(None, -32600, "JSON-RPC batching is not supported.")
        request_id = message.get("id")
        method = message.get("method", "")
        _validate_json_rpc(message)

        if request_id is None:
            outcome = "accepted"
            if method == "notifications/initialized":
                return Response(status_code=202)
            return Response(status_code=202)

        if method == "initialize":
            outcome = "success"
            return _result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "incident-copilot-tools",
                        "version": "0.1.0",
                    },
                    "instructions": (
                        "Use read-only evidence tools freely. Production actions require "
                        "explicit operator scope and a signed human approval receipt."
                    ),
                },
            )
        if method == "ping":
            outcome = "success"
            return _result(request_id, {})
        if method == "tools/list":
            outcome = "success"
            return _result(request_id, {"tools": registry.list_definitions()})
        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return _error(request_id, -32602, "Invalid tools/call parameters.")
            tool_name = name
            tool = registry.tools.get(name)
            tool_read_only = tool.read_only if tool is not None else None
            output = await registry.call(name, arguments, principal)
            outcome = "success"
            serialized = json.dumps(output, separators=(",", ":"), default=str)
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": serialized}],
                    "structuredContent": output,
                    "isError": False,
                },
            )
        return _error(request_id, -32601, f"Method not found: {method}")
    except (AuthorizationError, PermissionError):
        return _error(request_id, -32001, "MCP authorization denied.", http_status=403)
    except ValidationError as error:
        return _error(
            request_id,
            -32602,
            "Tool arguments failed JSON Schema validation.",
            data={"issues": error.errors(include_url=False, include_input=False, include_context=False)},
        )
    except (
        ValueError,
        asyncio.TimeoutError,
        TelemetryGatewayError,
        KubernetesRemediationError,
    ):
        return _error(request_id, -32602, "Tool request could not be completed.")
    except json.JSONDecodeError:
        return _error(None, -32700, "Parse error.")
    finally:
        logger.info(
            json.dumps(
                {
                    "event": "mcp_request",
                    "request_id": request_id,
                    "method": method,
                    "tool": tool_name,
                    "read_only": tool_read_only,
                    "principal": principal.subject if principal else None,
                    "outcome": outcome,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                },
                separators=(",", ":"),
            )
        )


def _validate_origin(origin: str | None) -> None:
    if origin is not None and origin not in ALLOWED_ORIGINS:
        raise AuthorizationError("Origin is not allowed.")


def _validate_json_rpc(message: Any) -> None:
    if not isinstance(message, dict):
        raise ValueError("JSON-RPC message must be an object.")
    if message.get("jsonrpc") != "2.0":
        raise ValueError("jsonrpc must equal 2.0.")
    if not isinstance(message.get("method"), str):
        raise ValueError("method must be a string.")
    if "id" in message and not isinstance(message["id"], (str, int)):
        raise ValueError("id must be a string or integer.")


async def _check_rate_limit(subject: str) -> None:
    async with _rate_lock:
        now = time.monotonic()
        window = _rate_windows[subject]
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= 120:
            raise AuthorizationError("Rate limit exceeded.")
        window.append(now)


def _result(request_id: str | int, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        headers={"MCP-Protocol-Version": PROTOCOL_VERSION},
    )


def _error(
    request_id: str | int | None,
    code: int,
    message: str,
    *,
    data: Any | None = None,
    http_status: int = 200,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": error},
        status_code=http_status,
        headers={"MCP-Protocol-Version": PROTOCOL_VERSION},
    )
