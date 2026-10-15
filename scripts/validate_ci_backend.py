"""Run the full CI backend contract and reject skipped cases."""
from __future__ import annotations
import subprocess
import sys
import pytest

class NoSkippedCases:
    def __init__(self):
        self.skipped = []
    def pytest_runtest_logreport(self, report):
        if report.skipped:
            self.skipped.append(report.nodeid)
    def pytest_collectreport(self, report):
        if report.skipped:
            self.skipped.append(report.nodeid)

for args in (["docker", "compose", "version"], ["kubectl", "version", "--client"], ["redis-server", "--version"]):
    subprocess.run(args, check=True, timeout=15)
checker = NoSkippedCases()
result = pytest.main(["services/orchestrator/tests", "services/mcp-server/tests", "evals/tests", "fault-lab/app/tests", "deploy/kubernetes/tests", "tests/test_portfolio_demo_offline.py", "--rootdir=.", "-q", "-rs", "-p", "no:cacheprovider"], plugins=[checker])
if checker.skipped:
    print("Required CI cases skipped:", *checker.skipped, sep="\n", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(result)
