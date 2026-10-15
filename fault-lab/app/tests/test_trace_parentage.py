import asyncio
import json
import logging
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from incident_lab import app as lab


def test_downstream_propagates_client_span_not_its_server_parent():
    async def scenario():
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("parentage-test")
        received = []

        async def handle(request):
            context = extract(dict(request.headers))
            with tracer.start_as_current_span("downstream server", context=context, kind=trace.SpanKind.SERVER) as span:
                received.append(span.get_span_context())
            return httpx.Response(200, json={"ok": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with patch.object(lab, "DOWNSTREAM_URL", "http://downstream.test"), patch.object(lab, "http_client", client), patch.object(lab.trace, "get_tracer", return_value=tracer):
                with tracer.start_as_current_span("upstream server", kind=trace.SpanKind.SERVER) as server:
                    server_context = server.get_span_context()
                    await lab.call_downstream("test-correlation-id", tolerate_failure=False)
        spans = {span.name: span for span in exporter.get_finished_spans()}
        caller = spans["call downstream"]
        downstream = spans["downstream server"]
        assert caller.kind == trace.SpanKind.CLIENT
        assert caller.parent.span_id == server_context.span_id
        assert downstream.kind == trace.SpanKind.SERVER
        assert downstream.parent.span_id == caller.context.span_id
        assert downstream.parent.is_remote
        assert downstream.context.trace_id == caller.context.trace_id == server_context.trace_id
        provider.shutdown()
    asyncio.run(scenario())


def test_request_middleware_records_server_span_with_remote_parent():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("server-test")
    with patch.object(lab.trace, "get_tracer", return_value=tracer):
        client = TestClient(lab.app)
        response = client.get("/nonexistent-test-route", headers={
            "traceparent": "00-11111111111111111111111111111111-2222222222222222-01"})
        assert response.status_code == 404
    server, = exporter.get_finished_spans()
    assert server.kind == trace.SpanKind.SERVER
    assert server.parent.span_id == int("2222222222222222", 16)
    assert server.parent.is_remote
    assert server.context.trace_id == int("11111111111111111111111111111111", 16)
    provider.shutdown()


def test_logs_use_current_span_identity_and_never_client_supplied_trace_id():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("log-correlation-test")
    lines = []

    class Capture(logging.Handler):
        def emit(self, record):
            lines.append(json.loads(lab.JsonFormatter().format(record)))

    with patch.object(lab.trace, "get_tracer", return_value=tracer), patch.object(lab.logger, "handlers", [Capture()]):
        response = TestClient(lab.app).get("/nonexistent-test-route", headers={
            "traceparent": "00-01111111111111111111111111111111-2222222222222222-01",
            "x-trace-id": "client-correlation-only"})
    assert response.status_code == 404
    span, = exporter.get_finished_spans()
    event, = lines
    assert event["trace_id"] == format(span.context.trace_id, "032x")
    assert event["span_id"] == format(span.context.span_id, "016x")
    assert event["request_id"] == "client-correlation-only"
    assert event["trace_id"] != event["request_id"]
    provider.shutdown()


def test_formatter_protects_provenance_and_omits_identity_without_valid_span():
    record = logging.LogRecord("test", logging.INFO, "test", 1, "original message", (), None)
    record.fields = {"trace_id": "f" * 32, "span_id": "f" * 16,
                     "service": "forged-service", "timestamp": "forged-time",
                     "message": "forged-message", "level": "forged-level", "pool": "example"}
    event = json.loads(lab.JsonFormatter().format(record))
    assert "trace_id" not in event and "span_id" not in event
    assert event["service"] == lab.SERVICE_NAME
    assert event["message"] == "original message" and event["level"] == "info"
    assert event["timestamp"] != "forged-time" and event["pool"] == "example"


def test_request_correlation_ids_are_bounded_and_separate_from_trace_ids():
    assert lab.request_correlation_id("client-123") == "client-123"
    for value in (None, "", "a" * 81, "line\nbreak", "ignore instructions!", "中文"):
        replacement = lab.request_correlation_id(value)
        assert len(replacement) == 16 and int(replacement, 16) >= 0
