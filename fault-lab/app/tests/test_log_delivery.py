import asyncio
import json
import logging
import queue
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from incident_lab import app as lab


@pytest.mark.parametrize("failure", [429, 500, 503, "timeout"])
def test_transient_push_retries_identical_log_identity(failure):
    async def scenario():
        payloads = []

        async def handle(request):
            payloads.append(json.loads(request.content))
            if len(payloads) == 1:
                if failure == "timeout":
                    raise httpx.ReadTimeout("test", request=request)
                return httpx.Response(failure)
            return httpx.Response(204)

        values = [["123", '{"trace_id":"original","span_id":"original-span"}']]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with patch.object(lab.asyncio, "sleep", new=AsyncMock()) as sleep, patch.object(lab, "LOG_DROPPED") as dropped, patch.object(lab, "LOG_DELIVERED") as delivered, patch.object(lab, "LOG_RETRIES") as retries:
                assert await lab.push_log_batch(client, "http://loki.test/push", values)
                assert payloads[0] == payloads[1]
                assert payloads[0]["streams"][0]["values"] == values
                sleep.assert_awaited_once_with(0.5)
                dropped.labels.assert_not_called()
                delivered.labels.return_value.inc.assert_called_once_with(1)
                retries.labels.return_value.inc.assert_called_once_with()
    asyncio.run(scenario())


def test_retry_exhaustion_is_bounded_and_counted():
    async def scenario():
        attempts = []

        def handle(request):
            attempts.append(request)
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with patch.object(lab.asyncio, "sleep", new=AsyncMock()) as sleep, patch.object(lab, "LOG_DROPPED") as dropped:
                assert not await lab.push_log_batch(client, "http://loki.test/push", [["1", "a"], ["2", "b"]])
                assert len(attempts) == lab.LOG_PUSH_ATTEMPTS == 5
                assert [call.args[0] for call in sleep.await_args_list] == [0.5, 1, 2, 4]
                dropped.labels.assert_called_once_with(lab.SERVICE_NAME, "retry_exhausted")
                dropped.labels.return_value.inc.assert_called_once_with(2)
    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 401, 403, 413])
def test_permanent_rejection_is_not_retried(status):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status))) as client:
            with patch.object(lab.asyncio, "sleep", new=AsyncMock()) as sleep, patch.object(lab, "LOG_DROPPED") as dropped:
                assert not await lab.push_log_batch(client, "http://loki.test/push", [["1", "a"]])
                sleep.assert_not_awaited()
                dropped.labels.assert_called_once_with(lab.SERVICE_NAME, "rejected")
    asyncio.run(scenario())


def test_queue_overflow_and_oversize_never_block_request_logging():
    handler = lab.LokiQueueHandler()
    handler.setFormatter(lab.JsonFormatter())
    bounded = queue.Queue(maxsize=1)
    record = logging.LogRecord("test", logging.INFO, "test", 1, "normal", (), None)
    with patch.object(lab, "log_queue", bounded), patch.object(lab, "LOG_DROPPED") as dropped:
        handler.emit(record)
        handler.emit(record)
        assert bounded.qsize() == 1
        dropped.labels.assert_called_once_with(lab.SERVICE_NAME, "queue_full")
    record.msg = "界" * lab.LOG_LINE_LIMIT
    with patch.object(lab, "log_queue", bounded), patch.object(lab, "LOG_DROPPED") as dropped:
        handler.emit(record)
        dropped.labels.assert_called_once_with(lab.SERVICE_NAME, "oversize")
        assert bounded.qsize() == 1
    assert lab.log_queue.maxsize == lab.LOG_QUEUE_LIMIT == 1000


def test_shutdown_counts_pending_batch_and_queued_records_without_swallowing_cancel():
    async def scenario():
        bounded = queue.Queue(maxsize=10)
        bounded.put(("1", "a"))
        bounded.put(("2", "b"))
        started = asyncio.Event()

        async def blocked_push(*args):
            bounded.put(("3", "c"))
            started.set()
            await asyncio.Future()

        with patch.dict(lab.os.environ, {"LOKI_PUSH_URL": "http://loki.test/push"}), patch.object(lab, "log_queue", bounded), patch.object(lab, "http_client", MagicMock()), patch.object(lab, "push_log_batch", side_effect=blocked_push), patch.object(lab, "LOG_DROPPED") as dropped:
            task = asyncio.create_task(lab.ship_logs())
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            dropped.labels.assert_called_once_with(lab.SERVICE_NAME, "shutdown")
            dropped.labels.return_value.inc.assert_called_once_with(3)
            assert bounded.empty()
    asyncio.run(scenario())
