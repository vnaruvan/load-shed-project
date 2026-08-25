import asyncio
from enum import Enum


class Priority(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class AdmissionController:
    def __init__(self, limit: int, low_priority_limit: int, normal_priority_limit: int):
        if limit < 1 or not 0 <= low_priority_limit < normal_priority_limit < limit:
            raise ValueError("priority limits require 0 <= low < normal < absolute limit")
        self.limit = limit
        self.low_priority_limit = low_priority_limit
        self.normal_priority_limit = normal_priority_limit
        self._inflight = 0
        self._lock = asyncio.Lock()

    @property
    def inflight(self) -> int:
        return self._inflight

    async def try_acquire(self, priority: Priority) -> bool:
        async with self._lock:
            threshold = {
                Priority.LOW: self.low_priority_limit,
                Priority.NORMAL: self.normal_priority_limit,
                Priority.HIGH: self.limit,
            }[priority]
            if self._inflight >= threshold:
                return False
            self._inflight += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._inflight <= 0:
                raise RuntimeError("permit released more than once")
            self._inflight -= 1
