from __future__ import annotations

import asyncio
import os
import secrets

import httpx


async def run() -> None:
    target = os.getenv("TRAFFIC_TARGET", "http://checkout-api:8090/v1/work")
    requests_per_second = max(0.2, float(os.getenv("TRAFFIC_RPS", "2")))
    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0)) as client:
        while True:
            try:
                await client.post(
                    target,
                    headers={"x-trace-id": f"load-{secrets.token_hex(6)}"},
                )
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1 / requests_per_second)


if __name__ == "__main__":
    asyncio.run(run())
