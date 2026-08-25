import asyncio
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from .admission import AdmissionController, Priority
from .breaker import CircuitBreaker
from .config import Settings

REQUESTS = Counter("load_shed_http_requests_total", "Requests", ["route", "method", "status"])
LATENCY = Histogram("load_shed_http_request_duration_seconds", "Request duration", ["route", "method"])
ADMISSION = Counter("load_shed_admission_requests_total", "Admission decisions", ["route", "priority", "decision"])
COMPLETED = Counter("load_shed_completed_requests_total", "Completed work", ["route", "priority", "outcome"])
INFLIGHT = Gauge("load_shed_admitted_inflight", "Admitted expensive work")
LIMIT = Gauge("load_shed_admission_limit", "Admission limit", ["kind"])
UPSTREAM = Counter("load_shed_upstream_requests_total", "Upstream outcomes", ["outcome"])
UPSTREAM_TIME = Histogram("load_shed_upstream_request_duration_seconds", "Upstream duration", ["outcome"])
CB_STATE = Gauge("load_shed_circuit_breaker_state", "0 closed, 1 open, 2 half-open")
CB_TRANSITIONS = Counter("load_shed_circuit_breaker_transitions_total", "Transitions", ["to_state"])


def _spin(ms: int):
    end, value = time.perf_counter() + ms / 1000, 0
    while time.perf_counter() < end:
        value = (value * 13 + 7) % 1_000_003


def create_app(settings=None, clock=time.monotonic):
    cfg = settings or Settings.from_env()
    admission = AdmissionController(
        cfg.max_inflight,
        cfg.low_priority_max_inflight,
        cfg.normal_priority_max_inflight,
    )
    breaker = CircuitBreaker(cfg.breaker_threshold, cfg.breaker_cooldown_seconds, clock)

    @asynccontextmanager
    async def lifespan(app):
        if not hasattr(app.state, "upstream_client"):
            timeout = httpx.Timeout(
                connect=cfg.http_connect_timeout_seconds,
                read=cfg.http_read_timeout_seconds,
                write=cfg.http_write_timeout_seconds,
                pool=cfg.http_pool_timeout_seconds,
            )
            limits = httpx.Limits(
                max_connections=cfg.http_max_connections, max_keepalive_connections=cfg.http_max_keepalive_connections
            )
            app.state.upstream_client = httpx.AsyncClient(timeout=timeout, limits=limits)
            app.state.owns_client = True
        LIMIT.labels("absolute").set(cfg.max_inflight)
        LIMIT.labels("low_priority").set(cfg.low_priority_max_inflight)
        LIMIT.labels("normal_priority").set(cfg.normal_priority_max_inflight)
        yield
        if getattr(app.state, "owns_client", False):
            await app.state.upstream_client.aclose()

    app = FastAPI(title="Load Shed API", lifespan=lifespan)
    app.state.settings, app.state.admission, app.state.breaker = cfg, admission, breaker

    @app.middleware("http")
    async def observe(request: Request, call_next):
        route = request.url.path if request.url.path in {"/healthz", "/work", "/client", "/metrics"} else "other"
        started, status = time.perf_counter(), 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            REQUESTS.labels(route, request.method, str(status)).inc()
            LATENCY.labels(route, request.method).observe(time.perf_counter() - started)

    async def acquire(route, priority):
        ADMISSION.labels(route, priority.value, "offered").inc()
        if not await admission.try_acquire(priority):
            ADMISSION.labels(route, priority.value, "shed").inc()
            raise HTTPException(429, "local admission capacity exhausted", headers={"Retry-After": "1"})
        ADMISSION.labels(route, priority.value, "admitted").inc()
        INFLIGHT.inc()

    async def release(route, priority, outcome):
        await admission.release()
        INFLIGHT.dec()
        COMPLETED.labels(route, priority.value, outcome).inc()

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/work")
    async def work(ms: int = Query(50, ge=1, le=2000), priority: Priority = Priority.NORMAL):
        await acquire("/work", priority)
        outcome = "error"
        try:
            await asyncio.to_thread(_spin, ms)
            outcome = "success"
            return {"ok": True, "ms": ms, "priority": priority.value}
        finally:
            await release("/work", priority, outcome)

    @app.get("/client")
    async def client(
        request: Request,
        ms: int = Query(50, ge=0, le=5000),
        fail_rate: float = Query(0, ge=0, le=1, allow_inf_nan=False),
        timeout_ms: int = Query(200, ge=1, le=10000),
        priority: Priority = Priority.NORMAL,
    ):
        await acquire("/client", priority)
        outcome = "error"
        try:
            token = await breaker.allow()
            CB_STATE.set(int(breaker.state))
            if token is None:
                UPSTREAM.labels("breaker_open").inc()
                raise HTTPException(503, "circuit breaker open")
            started, upstream_outcome = time.perf_counter(), "success"
            try:
                response = await request.app.state.upstream_client.get(
                    f"{cfg.upstream_base_url}/upstream",
                    params={"ms": ms, "fail_rate": fail_rate},
                    timeout=httpx.Timeout(timeout_ms / 1000),
                )
                response.raise_for_status()
            except httpx.PoolTimeout:
                upstream_outcome = "pool_timeout"
                if await breaker.fail(token):
                    CB_TRANSITIONS.labels("open").inc()
                raise HTTPException(504, "upstream connection pool timeout")
            except httpx.TimeoutException:
                upstream_outcome = "timeout"
                if await breaker.fail(token):
                    CB_TRANSITIONS.labels("open").inc()
                raise HTTPException(504, "upstream timeout")
            except httpx.HTTPStatusError:
                upstream_outcome = "http_error"
                if await breaker.fail(token):
                    CB_TRANSITIONS.labels("open").inc()
                raise HTTPException(502, "upstream HTTP error")
            except httpx.RequestError:
                upstream_outcome = "transport_error"
                if await breaker.fail(token):
                    CB_TRANSITIONS.labels("open").inc()
                raise HTTPException(502, "upstream transport error")
            else:
                if await breaker.succeed(token):
                    CB_TRANSITIONS.labels("closed").inc()
                outcome = "success"
                return {"ok": True, "ms": ms, "priority": priority.value}
            finally:
                UPSTREAM.labels(upstream_outcome).inc()
                UPSTREAM_TIME.labels(upstream_outcome).observe(time.perf_counter() - started)
                CB_STATE.set(int(breaker.state))
        finally:
            await release("/client", priority, outcome)

    return app


app = create_app()
