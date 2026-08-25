import asyncio
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable


class BreakerState(IntEnum):
    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2


@dataclass(frozen=True)
class CallToken:
    generation: int
    probe: bool = False


class CircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown: float = 10.0, clock: Callable[[], float] = time.monotonic):
        if threshold < 1 or cooldown <= 0:
            raise ValueError("invalid breaker configuration")
        self.threshold, self.cooldown, self.clock = threshold, cooldown, clock
        self.state, self.failures, self.opened_at, self.generation = BreakerState.CLOSED, 0, 0.0, 0
        self._probe_running = False
        self._lock = asyncio.Lock()

    async def allow(self) -> CallToken | None:
        async with self._lock:
            if self.state is BreakerState.OPEN:
                if self.clock() - self.opened_at < self.cooldown:
                    return None
                self.state, self._probe_running = BreakerState.HALF_OPEN, False
                self.generation += 1
            if self.state is BreakerState.HALF_OPEN:
                if self._probe_running:
                    return None
                self._probe_running = True
                return CallToken(self.generation, True)
            return CallToken(self.generation)

    async def succeed(self, token: CallToken) -> bool:
        async with self._lock:
            if token.generation != self.generation:
                return False
            changed = self.state is BreakerState.HALF_OPEN and token.probe
            if changed:
                self.state, self._probe_running = BreakerState.CLOSED, False
                self.generation += 1
            self.failures = 0
            return changed

    async def fail(self, token: CallToken) -> bool:
        async with self._lock:
            if token.generation != self.generation:
                return False
            if self.state is BreakerState.HALF_OPEN and token.probe:
                self._open()
                return True
            self.failures += 1
            if self.state is BreakerState.CLOSED and self.failures >= self.threshold:
                self._open()
                return True
            return False

    def _open(self) -> None:
        self.state, self.opened_at, self._probe_running = BreakerState.OPEN, self.clock(), False
        self.generation += 1
