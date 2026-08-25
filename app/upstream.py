import asyncio
import random

from fastapi import FastAPI, HTTPException, Query

app = FastAPI(title="Failure-injection upstream")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/upstream")
async def upstream(ms: int = Query(50, ge=0, le=5000), fail_rate: float = Query(0, ge=0, le=1, allow_inf_nan=False)):
    await asyncio.sleep(ms / 1000)
    if random.random() < fail_rate:
        raise HTTPException(503, "injected upstream failure")
    return {"ok": True, "ms": ms}
