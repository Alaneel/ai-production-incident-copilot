"""The advertised offline demo must stay offline in a tracing-enabled shell."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_demo_refuses_ambient_sdk_trace_transport():
    probe = r'''
import json, runpy, socket, sys
attempts = []
def refuse(*args, **kwargs):
    attempts.append("socket connection")
    raise AssertionError("Offline demo attempted socket transport")
socket.socket.connect = refuse
socket.socket.connect_ex = refuse
socket.create_connection = refuse
demo = sys.argv[1]
sys.argv = [demo]
from langsmith import tracing_context
try:
    with tracing_context(enabled=True):
        runpy.run_path(demo, run_name="__main__")
except SystemExit as result:
    if result.code not in (None, 0):
        raise
if attempts:
    raise AssertionError(f"Offline demo attempted {len(attempts)} connections")
print("OFFLINE_SOCKET_ATTEMPTS=0")
'''
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING": "true", "LANGCHAIN_TRACING_V2": "true",
        "LANGSMITH_OTEL_ENABLED": "true", "LANGSMITH_API_KEY": "synthetic-offline-test-key",
        "LANGSMITH_ENDPOINT": "http://127.0.0.1:9",
    }
    process = subprocess.run([sys.executable, "-B", "-c", probe, str(ROOT / "scripts/portfolio_demo.py")],
                             cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert process.stdout.endswith("OFFLINE_SOCKET_ATTEMPTS=0\n")
    result = json.loads(process.stdout.removesuffix("OFFLINE_SOCKET_ATTEMPTS=0\n"))
    assert result["cases"] == result["passed"] == 10
    assert result["model_calls"] == result["execution_attempts"] == 0
