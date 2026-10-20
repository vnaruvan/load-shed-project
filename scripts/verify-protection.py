"""Verify dependency protections through a tunnel to exactly one idle API pod."""
import json
import time
from pathlib import Path

import httpx

with httpx.Client(base_url="http://127.0.0.1:18080", timeout=15) as client:
    failures = [client.get("/client", params={"fail_rate": 1}).status_code for _ in range(5)]
    assert failures == [502] * 5, failures
    short_circuit = client.get("/client").status_code
    assert short_circuit == 503, short_circuit
    time.sleep(11)
    recovery = client.get("/client").status_code
    assert recovery == 200, recovery
    timeout = client.get("/client", params={"ms": 500, "timeout_ms": 10}).status_code
    assert timeout == 504, timeout
    result = {"qualifying_failures": failures, "breaker_open": short_circuit,
              "recovery_after_cooldown": recovery, "upstream_timeout": timeout}
    path = Path("load-tests/results/gke-protection.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
