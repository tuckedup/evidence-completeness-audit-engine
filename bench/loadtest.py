"""Diverse-key open-loop HTTP benchmark with cold, mixed, and warm scenarios."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(pct * len(ordered)) - 1))
    return ordered[index]


def load_payloads(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if source.suffix.lower() == ".jsonl":
        payloads = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line]
    else:
        raw = json.loads(source.read_text(encoding="utf-8"))
        payloads = raw if isinstance(raw, list) else [raw]
    if not payloads:
        raise ValueError("at least one benchmark payload is required")
    return payloads


def with_nonce(payload: dict[str, Any], nonce: str) -> dict[str, Any]:
    return {
        **payload,
        "metadata": {**payload.get("metadata", {}), "benchmark_nonce": nonce},
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    payloads = load_payloads(args.payloads)
    if not 0 <= args.hit_ratio <= 1:
        raise ValueError("hit_ratio must be between zero and one")
    run_id = args.run_id or str(uuid.uuid4())
    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(
        max_connections=args.connections,
        max_keepalive_connections=args.connections,
    )
    latencies: list[float] = []
    server_latencies: list[float] = []
    errors: list[str] = []
    cache_headers: dict[str, int] = {}
    semaphore = asyncio.Semaphore(args.connections)

    def payload_for(index: int) -> dict[str, Any]:
        base = payloads[index % len(payloads)]
        if args.scenario == "cold":
            return with_nonce(base, f"{run_id}:cold:{index}")
        if args.scenario == "mixed":
            # Bresenham-style spacing gives the requested aggregate fraction
            # for any run length without clustering all misses at the end.
            target_misses = round(args.requests * (1.0 - args.hit_ratio))
            is_miss = ((index + 1) * target_misses // args.requests) > (
                index * target_misses // args.requests
            )
            if is_miss:
                return with_nonce(base, f"{run_id}:miss:{index}")
        return base

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        async def post(payload: dict[str, Any]) -> httpx.Response:
            async with semaphore:
                response = await client.post(args.url, json=payload)
                response.raise_for_status()
                return response

        prewarmed = 0
        if args.scenario in {"warm", "mixed"}:
            responses = await asyncio.gather(*(post(payload) for payload in payloads))
            prewarmed = len(responses)
        for index in range(args.warmup):
            await post(with_nonce(payloads[index % len(payloads)], f"{run_id}:warmup:{index}"))

        loop = asyncio.get_running_loop()
        start = loop.time() + 0.25

        async def one(index: int) -> None:
            scheduled = start + index / args.rate
            while (remaining := scheduled - loop.time()) > 0:
                await asyncio.sleep(remaining)
            try:
                response = await post(payload_for(index))
                done = loop.time()
                cache = response.headers.get("x-ecsae-cache", "absent")
                cache_headers[cache] = cache_headers.get(cache, 0) + 1
                latencies.append((done - scheduled) * 1000)
                server_value = response.headers.get("x-ecsae-server-ms")
                if server_value is not None:
                    server_latencies.append(float(server_value))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        await asyncio.gather(*(one(index) for index in range(args.requests)))
        duration = loop.time() - start

    expected_hit_fraction = {
        "cold": 0.0,
        "mixed": args.hit_ratio,
        "warm": 1.0,
    }[args.scenario]
    return {
        "schema_version": "2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "model": "open-loop fixed-rate; latency = completion - scheduled_send",
        "scenario": args.scenario,
        "expected_hit_fraction": expected_hit_fraction,
        "payload_source": str(Path(args.payloads).resolve()),
        "distinct_base_payloads": len(payloads),
        "prewarmed_payloads": prewarmed,
        "url": args.url,
        "requested": args.requests,
        "completed": len(latencies),
        "errors": len(errors),
        "error_examples": errors[:10],
        "target_rate_per_second": args.rate,
        "achieved_rate_per_second": len(latencies) / max(duration, 1e-9),
        "duration_seconds": duration,
        "connections": args.connections,
        "excluded_warmup_requests": args.warmup,
        "cache_headers": cache_headers,
        "latency_ms": {
            "min": min(latencies) if latencies else None,
            "mean": statistics.fmean(latencies) if latencies else None,
            "p50": percentile(latencies, 0.50) if latencies else None,
            "p95": percentile(latencies, 0.95) if latencies else None,
            "p99": percentile(latencies, 0.99) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "server_handler_latency_ms": {
            "observations": len(server_latencies),
            "mean": statistics.fmean(server_latencies) if server_latencies else None,
            "p50": percentile(server_latencies, 0.50) if server_latencies else None,
            "p95": percentile(server_latencies, 0.95) if server_latencies else None,
            "p99": percentile(server_latencies, 0.99) if server_latencies else None,
            "max": max(server_latencies) if server_latencies else None,
        },
        "client_to_server_p95_ratio": (
            percentile(latencies, 0.95) / percentile(server_latencies, 0.95)
            if latencies and server_latencies and percentile(server_latencies, 0.95) > 0
            else None
        ),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/audit")
    parser.add_argument("--payloads", default="data/corpus/ctg-randomized-2000.jsonl")
    parser.add_argument("--scenario", choices=("cold", "mixed", "warm"), required=True)
    parser.add_argument("--hit-ratio", type=float, default=0.9)
    parser.add_argument("--requests", type=int, default=20_000)
    parser.add_argument("--rate", type=float, default=100)
    parser.add_argument("--connections", type=int, default=128)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--run-id")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"cache": report["cache_headers"], **report["latency_ms"]}, sort_keys=True))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
