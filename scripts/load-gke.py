"""Bounded CPU load through the public load balancer to exercise the HPA."""
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

import httpx


async def main():
    counts = Counter()
    deadline = time.monotonic() + 150
    async with httpx.AsyncClient(base_url="http://136.82.66.60", timeout=20,
                                 limits=httpx.Limits(max_connections=40)) as client:
        async def worker():
            while time.monotonic() < deadline:
                try:
                    response = await client.post("/work", params={"ms": 300})
                    counts[str(response.status_code)] += 1
                except httpx.HTTPError:
                    counts["transport_error"] += 1
        await asyncio.gather(*(worker() for _ in range(32)))
    path = Path("load-tests/results/gke-cpu-load.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(counts), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(counts)))


asyncio.run(main())
