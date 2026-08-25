#!/usr/bin/env python3
"""Fail-fast, single-process priority admission evidence test."""

import argparse
import asyncio
import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
from prometheus_client.parser import text_string_to_metric_families


def command(*args: str) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"unavailable: {exc}"


def parse_metrics(text: str) -> dict:
    result = {
        "offered": defaultdict(float),
        "admitted": defaultdict(float),
        "shed": defaultdict(float),
        "completed": defaultdict(float),
        "inflight": 0.0,
    }
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name == "load_shed_admission_requests_total":
                decision = sample.labels["decision"]
                result[decision][sample.labels["priority"]] += sample.value
            elif sample.name == "load_shed_completed_requests_total":
                result["completed"][sample.labels["priority"]] += sample.value
            elif sample.name == "load_shed_admitted_inflight":
                result["inflight"] += sample.value
    return {key: dict(value) if isinstance(value, defaultdict) else value for key, value in result.items()}


def delta(after: dict, before: dict, key: str, priority: str) -> int:
    return round(after[key].get(priority, 0) - before[key].get(priority, 0))


async def run(args: argparse.Namespace) -> dict:
    if not 0 <= args.low < args.normal < args.maximum:
        raise AssertionError("require 0 <= low < normal < maximum")
    timeout = httpx.Timeout((args.hold_ms + 10_000) / 1000)
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=timeout, limits=httpx.Limits(max_connections=200)
    ) as client:

        async def snapshot() -> dict:
            response = await client.get("/metrics")
            response.raise_for_status()
            return parse_metrics(response.text)

        before = await snapshot()
        if before["inflight"] != 0:
            raise AssertionError("evidence endpoint must start idle and target exactly one API process")
        maximum_observed = 0

        async def wait_for_inflight(expected: int) -> None:
            nonlocal maximum_observed
            for _ in range(500):
                current = round((await snapshot())["inflight"])
                maximum_observed = max(maximum_observed, current)
                if current > args.maximum:
                    raise AssertionError(f"absolute ceiling exceeded: observed {current}")
                if current == expected:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError(f"in-flight count did not reach {expected}")

        async def request(priority: str) -> httpx.Response:
            return await client.get(
                "/client",
                params={"priority": priority, "ms": args.hold_ms, "timeout_ms": args.hold_ms + 5_000},
            )

        admitted = []
        admitted.extend(asyncio.create_task(request("low")) for _ in range(args.low))
        await wait_for_inflight(args.low)
        probes = {"low": await request("low")}

        admitted.extend(asyncio.create_task(request("normal")) for _ in range(args.normal - args.low))
        await wait_for_inflight(args.normal)
        probes["normal"] = await request("normal")

        admitted.extend(asyncio.create_task(request("high")) for _ in range(args.maximum - args.normal))
        await wait_for_inflight(args.maximum)
        probes["high"] = await request("high")

        for priority, response in probes.items():
            if response.status_code != 429 or response.headers.get("Retry-After") != "1":
                raise AssertionError(f"{priority} overflow must be 429 with Retry-After: 1")
        accepted_responses = await asyncio.gather(*admitted)
        if any(response.status_code != 200 for response in accepted_responses):
            raise AssertionError("an admitted request did not complete successfully")
        await wait_for_inflight(0)
        after = await snapshot()

    expected_admitted = {"low": args.low, "normal": args.normal - args.low, "high": args.maximum - args.normal}
    reconciliation = {}
    for priority, expected in expected_admitted.items():
        observed = {key: delta(after, before, key, priority) for key in ("offered", "admitted", "shed", "completed")}
        expected_metrics = {"offered": expected + 1, "admitted": expected, "shed": 1, "completed": expected}
        if observed != expected_metrics:
            raise AssertionError(f"metric reconciliation failed for {priority}: {observed} != {expected_metrics}")
        reconciliation[priority] = observed
    if maximum_observed != args.maximum:
        raise AssertionError(f"expected to observe absolute limit {args.maximum}, saw {maximum_observed}")
    return {"maximum_inflight_observed": maximum_observed, "metrics_delta": reconciliation}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--low", type=int, default=35)
    parser.add_argument("--normal", type=int, default=45)
    parser.add_argument("--maximum", type=int, default=50)
    parser.add_argument("--hold-ms", type=int, default=4_000)
    parser.add_argument("--output-dir", type=Path, default=Path("load-tests/results"))
    args = parser.parse_args()
    started = datetime.now(UTC)
    result = asyncio.run(run(args))
    diff = subprocess.run(["git", "diff", "--binary", "HEAD"], capture_output=True).stdout
    evidence = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "configuration": vars(args) | {"output_dir": str(args.output_dir)},
        "git_commit": command("git", "rev-parse", "HEAD"),
        "git_status": command("git", "status", "--short"),
        "working_tree_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "versions": {
            "docker": command("docker", "version", "--format", "{{.Server.Version}}"),
            "kind": command("kind", "version"),
            "kubectl": command("kubectl", "version", "--client"),
        },
        "result": result,
        "passed": True,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / f"priority-{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    destination.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"PASS: {destination}")


if __name__ == "__main__":
    main()
