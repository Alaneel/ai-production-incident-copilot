"""Small bounded pool for a local fault experiment, not a production pool."""
from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager, suppress

import psycopg
from psycopg.conninfo import conninfo_to_dict


class PoolExhausted(TimeoutError):
    pass


def local_parameters(url):
    params = conninfo_to_dict(url)
    host = params.get("host", "")
    if (not params.get("dbname") or params["dbname"].startswith(("postgres://", "postgresql://"))
            or not host or "," in host
            or any(key in params for key in ("service", "hostaddr"))
            or any(os.getenv(key) for key in ("PGSERVICE", "PGSERVICEFILE", "PGHOSTADDR"))
            or not (host in {"127.0.0.1", "localhost", "::1"}
                    or host.startswith(("/private/tmp/", "/tmp/")))):
        raise ValueError("Real pool experiments require an explicit dedicated local PostgreSQL target")
    # Do not let libpq environment defaults redirect this controlled experiment.
    params.update(hostaddr=("127.0.0.1" if host == "localhost" else host)
                  if not host.startswith("/") else "", application_name="incident-fault-lab-pool",
                  port=params.get("port", "5432"), connect_timeout="2", options="-c statement_timeout=2000")
    return params


class LocalPostgresPool:
    def __init__(self, *, size=4, acquire_timeout=0.5, observe=lambda active, maximum: None):
        if not 1 <= size <= 4 or not 0 < acquire_timeout <= 2:
            raise ValueError("Lab pool size/timeouts must be bounded")
        self.size, self.acquire_timeout, self.observe = size, acquire_timeout, observe
        self.connections = []
        self.available = asyncio.Queue(maxsize=size)
        self._held = None
        self._expiry = None
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def active(self):
        return len(self.connections) - self.available.qsize()

    def report(self):
        self.observe(self.active, self.size)

    async def open(self, url):
        params = local_parameters(url)
        if self.connections or self._closed:
            raise RuntimeError("A lab pool cannot be opened twice")
        try:
            for _ in range(self.size):
                connection = await psycopg.AsyncConnection.connect(autocommit=True, **params)
                self.connections.append(connection)
                self.available.put_nowait(connection)
        except BaseException:
            await self.close()
            raise
        self.report()

    @asynccontextmanager
    async def lease(self):
        if self._closed:
            raise RuntimeError("Lab pool is closed")
        try:
            connection = await asyncio.wait_for(self.available.get(), timeout=self.acquire_timeout)
        except TimeoutError:
            raise PoolExhausted("database connection acquisition timed out") from None
        self.report()
        try:
            yield connection
        finally:
            self.available.put_nowait(connection)
            self.report()

    async def query(self):
        async with self.lease() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                return (await cursor.fetchone())[0]

    async def _stop_fault(self):
        if self._expiry:
            self._expiry.cancel()
            with suppress(asyncio.CancelledError):
                await self._expiry
            self._expiry = None
        if self._held:
            await self._held.aclose()
            self._held = None

    async def exhaust(self, duration_seconds):
        if not 0 < duration_seconds <= 900:
            raise ValueError("Pool fault duration must be bounded")
        async with self._lock:
            await self._stop_fault()
            held = AsyncExitStack()
            try:
                for _ in range(self.size):
                    await held.enter_async_context(self.lease())
            except BaseException:
                await held.aclose()
                raise
            self._held = held

            async def expire():
                await asyncio.sleep(duration_seconds)
                async with self._lock:
                    await held.aclose()
                    self._held = None
                    self._expiry = None
            self._expiry = asyncio.create_task(expire())

    async def clear_fault(self):
        async with self._lock:
            await self._stop_fault()

    async def close(self):
        await self.clear_fault()
        self._closed = True
        await asyncio.gather(*(connection.close() for connection in self.connections))
