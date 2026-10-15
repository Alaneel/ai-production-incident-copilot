"""Exercise actual framework lifespan in a fresh process (global provider is single-set)."""
import json
import os
import subprocess
import sys


PROGRAM = '''
import json,os
from unittest.mock import patch
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
os.environ['SERVICE_NAME']='payment-service'
os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']='http://127.0.0.1:1'
os.environ['INCIDENT_LAB_DB_POOL_BACKEND']='simulation'
os.environ['DOWNSTREAM_URL']=''
os.environ['LOKI_PUSH_URL']=''
from incident_lab import app as lab
exporter=InMemorySpanExporter()
with patch.object(lab,'OTLPSpanExporter',return_value=exporter), patch.object(lab,'BatchSpanProcessor',SimpleSpanProcessor):
 with TestClient(lab.app) as client:
  assert client.get('/health').status_code==200
  assert client.get('/metrics').status_code==200
  response=client.get('/v1/work',headers={'traceparent':'00-01111111111111111111111111111111-2222222222222222-01','x-request-id':'startup-test'})
  assert response.status_code==200
  spans=exporter.get_finished_spans()
  assert len(spans)==1, [span.name for span in spans]
  span=spans[0]
  assert span.resource.attributes['service.name']=='payment-service'
  assert span.name=='GET /v1/work' and span.kind.name=='SERVER'
  assert span.context.trace_id==int('01111111111111111111111111111111',16)
  assert span.parent.span_id==int('2222222222222222',16) and span.parent.is_remote
  assert span.attributes['http.request.id']=='startup-test'
  print(json.dumps({'ok':True,'spans':len(spans),'service':span.resource.attributes['service.name']}))
'''


def test_framework_startup_does_not_preempt_lab_tracing_provider():
    # Do not inherit endpoint credentials, pool URLs or exporter configuration.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("OTEL_", "INCIDENT_LAB_", "OPENAI_"))}
    result = subprocess.run([sys.executable, "-c", PROGRAM], env=env,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"ok": True, "spans": 1, "service": "payment-service"}
    assert "Overriding of current TracerProvider" not in result.stderr


if __name__ == "__main__":
    # Also runnable with the packaged image's actual FastAPI/OpenTelemetry versions.
    test_framework_startup_does_not_preempt_lab_tracing_provider()
