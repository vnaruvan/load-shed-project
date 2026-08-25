import pytest

from app.breaker import BreakerState, CircuitBreaker


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


@pytest.mark.asyncio
async def test_full_breaker_lifecycle_and_single_probe():
    clock, breaker = Clock(), CircuitBreaker(5, 10, Clock())
    clock = breaker.clock
    token = await breaker.allow()
    assert await breaker.succeed(token) is False
    for _ in range(5):
        token = await breaker.allow()
        await breaker.fail(token)
    assert breaker.state is BreakerState.OPEN and await breaker.allow() is None
    clock.now = 10
    probe = await breaker.allow()
    assert probe.probe and breaker.state is BreakerState.HALF_OPEN and await breaker.allow() is None
    assert await breaker.succeed(probe) and breaker.state is BreakerState.CLOSED


@pytest.mark.asyncio
async def test_failed_probe_reopens_and_stale_completion_is_ignored():
    clock, breaker = Clock(), CircuitBreaker(1, 5, Clock())
    clock = breaker.clock
    stale = await breaker.allow()
    await breaker.fail(stale)
    assert not await breaker.succeed(stale)
    clock.now = 5
    probe = await breaker.allow()
    assert await breaker.fail(probe)
    assert breaker.state is BreakerState.OPEN
