"""Explicit current synthetic samples for positive MCP/graph contract tests."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch


@contextmanager
def current_metric_fixture(registry):
    if registry.gateway is not None:
        raise ValueError("Current synthetic fixture cannot replace an external gateway")
    original = registry.tools["query_metrics"]

    async def handler(arguments, principal):
        result = await original.handler(arguments, principal)
        # Only test-owned synthetic timestamps change; MCP validation/auth,
        # transport and graph remain real. This is not live observability.
        result["series"] = [{**point, "timestamp":
            (arguments.end - timedelta(seconds=(len(result["series"]) - 1 - index) * 15)).isoformat()}
            for index, point in enumerate(result["series"])]
        return result

    with patch.dict(registry.tools, {"query_metrics": replace(original, handler=handler)}):
        yield
