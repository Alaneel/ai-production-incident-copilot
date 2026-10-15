from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx


class TelemetryGatewayError(RuntimeError):
    pass


class TelemetryGatewayClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float = 6.0,
        max_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.max_attempts = max_attempts
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def query_metrics(
        self,
        *,
        service: str,
        metric: str,
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/metrics/query",
            {
                "service": service,
                "metric": metric,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "step_seconds": step_seconds,
            },
        )

    async def search_logs(
        self,
        *,
        service: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/logs/search",
            {
                "service": service,
                "query": query,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": limit,
            },
        )

    async def search_traces(
        self,
        *,
        service: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/traces/search",
            {
                "service": service,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": limit,
            },
        )

    async def dependencies(self, service: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/services/{service}/dependencies",
        )

    async def observed_dependencies(self, service: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/services/{service}/observed-dependencies")

    async def deployments(self, service: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/v1/services/{service}/deployments",
        )

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "X-Trace-ID": f"mcp-gateway-{attempt}",
                    },
                )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise TelemetryGatewayError(
                        "Telemetry gateway response must be an object."
                    )
                return body
            except (
                httpx.TimeoutException,
                httpx.TransportError,
                json.JSONDecodeError,
            ) as error:
                last_error = error
                if attempt == self.max_attempts:
                    break
                await asyncio.sleep(0.05 * (2 ** (attempt - 1)))
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500 or attempt == self.max_attempts:
                    raise TelemetryGatewayError(
                        f"Telemetry gateway HTTP failure: {error.response.status_code}"
                    ) from error
                last_error = error
                await asyncio.sleep(0.05 * (2 ** (attempt - 1)))
        raise TelemetryGatewayError(
            "Telemetry gateway request exhausted retry budget."
        ) from last_error
