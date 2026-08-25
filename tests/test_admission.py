import asyncio

import pytest

from app.admission import AdmissionController, Priority


@pytest.mark.asyncio
async def test_priority_reservation_and_absolute_limit_are_atomic():
    gate = AdmissionController(6, 3, 5)
    assert all([await gate.try_acquire(Priority.LOW) for _ in range(3)])
    assert not await gate.try_acquire(Priority.LOW)
    assert await gate.try_acquire(Priority.NORMAL)
    assert await gate.try_acquire(Priority.NORMAL)
    assert not await gate.try_acquire(Priority.NORMAL)
    assert await gate.try_acquire(Priority.HIGH)
    assert not await gate.try_acquire(Priority.HIGH)
    assert gate.inflight == 6
    for _ in range(6):
        await gate.release()
    assert gate.inflight == 0


@pytest.mark.asyncio
async def test_concurrent_calls_never_exceed_limit():
    gate = AdmissionController(7, 4, 6)
    results = await asyncio.gather(*(gate.try_acquire(Priority.HIGH) for _ in range(100)))
    assert sum(results) == 7 and gate.inflight == 7
    await asyncio.gather(*(gate.release() for _ in range(7)))


@pytest.mark.asyncio
async def test_release_guard():
    gate = AdmissionController(3, 1, 2)
    with pytest.raises(RuntimeError):
        await gate.release()
