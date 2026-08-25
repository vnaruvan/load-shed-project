import asyncio

import httpx
import pytest

from app.config import Settings
from app.main import create_app


def settings(limit=3, low=1, normal=2):
    return Settings(limit, low, normal, "http://upstream", 5, 10, 10, 5, 0.1, 0.1, 0.1, 0.1)


async def client_for(app, handler):
    app.state.upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_validation_and_upstream_outcomes():
    async def ok(request):
        return httpx.Response(200, json={"ok": True})

    app = create_app(settings())
    client = await client_for(app, ok)
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/client?priority=unknown")).status_code == 422
    assert (await client.get("/client?fail_rate=nan")).status_code == 422
    assert (await client.get("/client?priority=high")).status_code == 200
    await client.aclose()
    await app.state.upstream_client.aclose()


@pytest.mark.asyncio
async def test_each_priority_stops_before_the_next_reserved_capacity():
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(request):
        entered.set()
        await release.wait()
        return httpx.Response(200)

    app = create_app(settings())
    client = await client_for(app, slow)
    first = asyncio.create_task(client.get("/client?priority=low"))
    await entered.wait()
    shed = await client.get("/client?priority=low")
    normal = asyncio.create_task(client.get("/client?priority=normal"))
    await asyncio.sleep(0)
    normal_shed = await client.get("/client?priority=normal")
    high = asyncio.create_task(client.get("/client?priority=high"))
    await asyncio.sleep(0)
    absolute = await client.get("/client?priority=high")
    assert shed.status_code == 429 and shed.headers["retry-after"] == "1"
    assert normal_shed.status_code == 429 and normal_shed.headers["retry-after"] == "1"
    assert absolute.status_code == 429
    release.set()
    assert (await first).status_code == 200
    assert (await normal).status_code == 200
    assert (await high).status_code == 200
    assert app.state.admission.inflight == 0
    await client.aclose()
    await app.state.upstream_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("exc,status", [(httpx.ConnectError("x"), 502), (httpx.ReadTimeout("x"), 504)])
async def test_transport_and_timeout_release(exc, status):
    async def fail(request):
        raise exc

    app = create_app(settings())
    client = await client_for(app, fail)
    assert (await client.get("/client")).status_code == status
    assert app.state.admission.inflight == 0
    await client.aclose()
    await app.state.upstream_client.aclose()


@pytest.mark.asyncio
async def test_cancellation_releases_permit():
    entered = asyncio.Event()

    async def hang(request):
        entered.set()
        await asyncio.Event().wait()

    app = create_app(settings())
    client = await client_for(app, hang)
    task = asyncio.create_task(client.get("/client"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert app.state.admission.inflight == 0
    await client.aclose()
    await app.state.upstream_client.aclose()
