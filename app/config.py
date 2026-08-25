import math
import os
from dataclasses import dataclass


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    max_inflight: int
    low_priority_max_inflight: int
    normal_priority_max_inflight: int
    upstream_base_url: str
    breaker_threshold: int
    breaker_cooldown_seconds: float
    http_max_connections: int
    http_max_keepalive_connections: int
    http_connect_timeout_seconds: float
    http_read_timeout_seconds: float
    http_write_timeout_seconds: float
    http_pool_timeout_seconds: float

    @classmethod
    def from_env(cls):
        maximum = _int("MAX_INFLIGHT", 50)
        low = _int("LOW_PRIORITY_MAX_INFLIGHT", 35, 0)
        normal = _int("NORMAL_PRIORITY_MAX_INFLIGHT", 45)
        if not low < normal < maximum:
            raise ValueError(
                "priority limits require LOW_PRIORITY_MAX_INFLIGHT < NORMAL_PRIORITY_MAX_INFLIGHT < MAX_INFLIGHT"
            )
        return cls(
            maximum,
            low,
            normal,
            os.getenv("UPSTREAM_BASE_URL", "http://127.0.0.1:8081").rstrip("/"),
            _int("BREAKER_FAILURE_THRESHOLD", 5),
            _float("BREAKER_COOLDOWN_SECONDS", 10),
            _int("HTTP_MAX_CONNECTIONS", 100),
            _int("HTTP_MAX_KEEPALIVE_CONNECTIONS", 20),
            _float("HTTP_CONNECT_TIMEOUT_SECONDS", 0.2),
            _float("HTTP_READ_TIMEOUT_SECONDS", 1),
            _float("HTTP_WRITE_TIMEOUT_SECONDS", 1),
            _float("HTTP_POOL_TIMEOUT_SECONDS", 0.1),
        )
