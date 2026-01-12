import time
import random
from typing import Optional
import asyncio
import httpx

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import Counter, Histogram, Gauge
from prometheus_fastapi_instrumentator import Instrumentator
app = FastAPI(title="Load Shed API")
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

UPSTREAM_LATENCY = Histogram(
    "upstream_request_duration_seconds",
    "Upstream request latency in seconds",
    ["result"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

UPSTREAM_REQUESTS = Counter(
    "upstream_requests_total",
    "Upstream requests by result",
    ["result"],
)

CB_STATE = Gauge("circuit_breaker_state", "0 closed, 1 open")
CB_OPEN_UNTIL = Gauge("circuit_breaker_open_until_epoch", "Epoch time until breaker closes")
CB_OPEN = False
CB_UNTIL = 0.0
CB_FAIL_COUNT = 0
CB_FAIL_THRESHOLD = 5
CB_OPEN_SECONDS = 10.0


INFLIGHT = Gauge("http_inflight_requests", "In-flight requests")
WORK_ITEMS = Counter("work_items_total", "Total work items processed", ["outcome"])
QUEUE_DEPTH = Gauge("work_queue_depth", "Synthetic queue depth")

DEFAULT_QUEUE_LIMIT = 50

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    path = request.scope.get("route").path if request.scope.get("route") else request.url.path
    method = request.method

    INFLIGHT.inc()
    start = time.time()
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        INFLIGHT.dec()
        elapsed = time.time() - start
        LATENCY.labels(method=method, path=path).observe(elapsed)
        REQUESTS.labels(method=method, path=path, status=str(status_code)).inc()

@app.get("/healthz")
def healthz():
    return {"ok": True}

def cpu_spin(milliseconds: int) -> None:
    end = time.time() + (milliseconds / 1000.0)
    x = 0
    while time.time() < end:
        x = (x * 13 + 7) % 1000003

@app.post("/work")
def work(
    ms: int = 50,
    fail_rate: float = 0.02,
    queue_depth: Optional[int] = None,
    queue_limit: int = DEFAULT_QUEUE_LIMIT,
):
    if ms < 1 or ms > 2000:
        raise HTTPException(status_code=400, detail="ms must be between 1 and 2000")

    if fail_rate < 0 or fail_rate > 1:
        raise HTTPException(status_code=400, detail="fail_rate must be between 0 and 1")

    if queue_depth is None:
        queue_depth = random.randint(0, 100)

    QUEUE_DEPTH.set(queue_depth)

    if queue_depth > queue_limit:
        WORK_ITEMS.labels(outcome="shed").inc()
        raise HTTPException(status_code=429, detail="load shed: queue depth too high")

    cpu_spin(ms)

    if random.random() < fail_rate:
        WORK_ITEMS.labels(outcome="error").inc()
        raise HTTPException(status_code=500, detail="simulated failure")

    WORK_ITEMS.labels(outcome="ok").inc()
    return {"status": "ok", "ms": ms, "queue_depth": queue_depth}

@app.get("/upstream")
def upstream(ms: int = 50, fail_rate: float = 0.0):
    if ms < 1 or ms > 5000:
        raise HTTPException(status_code=400, detail="ms must be between 1 and 5000")
    if fail_rate < 0 or fail_rate > 1:
        raise HTTPException(status_code=400, detail="fail_rate must be between 0 and 1")
    cpu_spin(ms)
    if random.random() < fail_rate:
        raise HTTPException(status_code=503, detail="upstream failure")
    return {"ok": True, "ms": ms}
CB_LOCK = asyncio.Lock()
UPSTREAM_BASE_URL = "http://127.0.0.1:8080"
@app.get("/client")
async def client(ms: int = 50, fail_rate: float = 0.0, timeout_ms: int = 200):
    if ms < 1 or ms > 5000:
        raise HTTPException(status_code=400, detail="ms must be between 1 and 5000")
    if fail_rate < 0 or fail_rate > 1:
        raise HTTPException(status_code=400, detail="fail_rate must be between 0 and 1")
    if timeout_ms < 1 or timeout_ms > 10000:
        raise HTTPException(status_code=400, detail="timeout_ms must be between 1 and 10000")

    global CB_OPEN, CB_UNTIL, CB_FAIL_COUNT

    now = time.time()

    async with CB_LOCK:
        if CB_OPEN and now < CB_UNTIL:
            CB_STATE.set(1)
            CB_OPEN_UNTIL.set(CB_UNTIL)
            UPSTREAM_REQUESTS.labels(result="breaker_open").inc()
            raise HTTPException(status_code=503, detail="circuit breaker open")
        if CB_OPEN and now >= CB_UNTIL:
            CB_OPEN = False
            CB_FAIL_COUNT = 0
            CB_STATE.set(0)
            CB_OPEN_UNTIL.set(0)

    url = f"{UPSTREAM_BASE_URL}/upstream"
    params = {"ms": ms, "fail_rate": fail_rate}
    timeout = httpx.Timeout(timeout_ms / 1000.0)

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
        elapsed = time.time() - start
        UPSTREAM_LATENCY.labels(result="ok").observe(elapsed)
        UPSTREAM_REQUESTS.labels(result="ok").inc()
        async with CB_LOCK:
            CB_FAIL_COUNT = 0
            CB_STATE.set(0)
            CB_OPEN_UNTIL.set(0)
        return {"ok": True, "ms": ms, "timeout_ms": timeout_ms}
    except httpx.TimeoutException:
        elapsed = time.time() - start
        UPSTREAM_LATENCY.labels(result="timeout").observe(elapsed)
        UPSTREAM_REQUESTS.labels(result="timeout").inc()
        async with CB_LOCK:
            CB_FAIL_COUNT += 1
            if CB_FAIL_COUNT >= CB_FAIL_THRESHOLD:
                CB_OPEN = True
                CB_UNTIL = time.time() + CB_OPEN_SECONDS
                CB_STATE.set(1)
                CB_OPEN_UNTIL.set(CB_UNTIL)
        raise HTTPException(status_code=504, detail="upstream timeout")
    except httpx.HTTPStatusError:
        elapsed = time.time() - start
        UPSTREAM_LATENCY.labels(result="error").observe(elapsed)
        UPSTREAM_REQUESTS.labels(result="error").inc()
        async with CB_LOCK:
            CB_FAIL_COUNT += 1
            if CB_FAIL_COUNT >= CB_FAIL_THRESHOLD:
                CB_OPEN = True
                CB_UNTIL = time.time() + CB_OPEN_SECONDS
                CB_STATE.set(1)
                CB_OPEN_UNTIL.set(CB_UNTIL)
        raise HTTPException(status_code=502, detail="upstream error")
    except httpx.RequestError:
        elapsed = time.time() - start
        UPSTREAM_LATENCY.labels(result="transport").observe(elapsed)
        UPSTREAM_REQUESTS.labels(result="transport").inc()
        async with CB_LOCK:
            CB_FAIL_COUNT += 1
            if CB_FAIL_COUNT >= CB_FAIL_THRESHOLD:
                CB_OPEN = True
                CB_UNTIL = time.time() + CB_OPEN_SECONDS
                CB_STATE.set(1)
                CB_OPEN_UNTIL.set(CB_UNTIL)
        raise HTTPException(status_code=502, detail="upstream transport error")

