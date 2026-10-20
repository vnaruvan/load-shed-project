"""Bounded public endpoint smoke/load test; writes deployment evidence as JSON."""
import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx


async def verify(base_url):
    async with httpx.AsyncClient(base_url=base_url, timeout=30,
                                 limits=httpx.Limits(max_connections=250)) as client:
        health = await client.get("/healthz")
        health.raise_for_status()
        work = await client.post("/work", params={"ms": 50})
        work.raise_for_status()
        dependency = await client.get("/client", params={"ms": 50})
        dependency.raise_for_status()

        async def request():
            return await client.get("/client", params={"ms": 700, "timeout_ms": 2000,
                                                       "priority": "low"})

        results = await asyncio.gather(*(request() for _ in range(250)))
        shed = [r for r in results if r.status_code == 429]
        assert shed, "No 429 observed; inspect replica count and concurrency"
        assert all(r.headers.get("retry-after") == "1" for r in shed)
        return {"verified_at": datetime.now(timezone.utc).isoformat(),
                "base_url": base_url, "health": health.status_code,
                "work": work.status_code, "client": dependency.status_code,
                "burst_requests": len(results),
                "burst_status_counts": dict(Counter(r.status_code for r in results)),
                "retry_after_verified": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--output", default="load-tests/results/gke-smoke.json")
    args = parser.parse_args()
    result = asyncio.run(verify(args.base_url))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
