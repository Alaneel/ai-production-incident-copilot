from __future__ import annotations

import asyncio
import json
from typing import Protocol

from redis.asyncio import Redis

from .models import IncidentState, TraceEvent


class TaskStateStore(Protocol):
    async def publish(self, state: IncidentState) -> None: ...

    async def events_after(
        self, incident_id: str, sequence: int
    ) -> list[TraceEvent]: ...

    async def close(self) -> None: ...


class InMemoryTaskStateStore:
    def __init__(self) -> None:
        self._states: dict[str, IncidentState] = {}
        self._lock = asyncio.Lock()

    async def publish(self, state: IncidentState) -> None:
        async with self._lock:
            self._states[state.incident_id] = state.model_copy(deep=True)

    async def events_after(
        self, incident_id: str, sequence: int
    ) -> list[TraceEvent]:
        async with self._lock:
            state = self._states.get(incident_id)
            if state is None:
                return []
            return [
                event.model_copy(deep=True)
                for event in state.trace
                if event.sequence > sequence
            ]

    async def close(self) -> None:
        return None


class RedisTaskStateStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 86_400) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    @classmethod
    def from_url(cls, url: str) -> "RedisTaskStateStore":
        """Construct a reconnectable client without making Redis authoritative."""
        return cls(Redis.from_url(url, decode_responses=True))

    @classmethod
    async def connect(cls, url: str) -> "RedisTaskStateStore":
        store = cls.from_url(url)
        try:
            await store.redis.ping()
        except BaseException:
            await store.close()
            raise
        return store

    async def publish(self, state: IncidentState) -> None:
        state_key = f"incident:{state.incident_id}:task"
        event_key = f"incident:{state.incident_id}:events"
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.hset(
            state_key,
            mapping={
                "status": state.status.value,
                "current_step": state.current_step.value,
                "trace_id": state.trace_id,
                "trace_length": len(state.trace),
                "updated_at": state.updated_at.isoformat(),
            },
        )
        pipeline.expire(state_key, self.ttl_seconds)
        for event in state.trace:
            pipeline.zadd(
                event_key,
                {
                    json.dumps(
                        event.model_dump(mode="json"),
                        separators=(",", ":"),
                    ): event.sequence
                },
                nx=True,
            )
        pipeline.expire(event_key, self.ttl_seconds)
        await pipeline.execute()

    async def events_after(
        self, incident_id: str, sequence: int
    ) -> list[TraceEvent]:
        values = await self.redis.zrangebyscore(
            f"incident:{incident_id}:events",
            min=f"({sequence}",
            max="+inf",
        )
        return [TraceEvent.model_validate_json(value) for value in values]

    async def close(self) -> None:
        await self.redis.aclose()
