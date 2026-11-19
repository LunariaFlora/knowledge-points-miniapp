from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(len(ordered) * percentage) - 1)
    return ordered[index]


def request_once(url: str, timeout: float) -> tuple[bool, float, str]:
    started = time.perf_counter()
    try:
        request = Request(url, headers={"User-Agent": "knowledge-points-runtime-test/1.0"})
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            ok = response.status == 200 and bool(body)
            error = "" if ok else f"HTTP {response.status} or empty body"
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        ok, error = False, str(exc)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return ok, elapsed_ms, error


def run_stage(base_url: str, concurrency: int, requests: int, timeout: float) -> dict:
    urls = [f"{base_url}/api/health", f"{base_url}/admin"]
    started = time.perf_counter()
    results: list[tuple[bool, float, str]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(request_once, urls[index % len(urls)], timeout) for index in range(requests)]
        for future in as_completed(futures):
            results.append(future.result())
    wall_seconds = time.perf_counter() - started
    durations = [item[1] for item in results]
    errors = [item[2] for item in results if not item[0]]
    return {
        "concurrency": concurrency,
        "requests": requests,
        "successes": requests - len(errors),
        "errors": len(errors),
        "errorRate": round(len(errors) / requests, 4),
        "requestsPerSecond": round(requests / wall_seconds, 2),
        "p50Ms": round(percentile(durations, 0.50), 2),
        "p95Ms": round(percentile(durations, 0.95), 2),
        "p99Ms": round(percentile(durations, 0.99), 2),
        "maxMs": round(max(durations, default=0), 2),
        "sampleErrors": errors[:3],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a bounded read-only load test against the mini-program backend")
    parser.add_argument("--base-url", default="http://127.0.0.1:8770")
    parser.add_argument("--concurrency", default="5,20,50")
    parser.add_argument("--requests-per-stage", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--p95-limit-ms", type=float, default=1000.0)
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    stages = [int(value.strip()) for value in args.concurrency.split(",") if value.strip()]
    report = {
        "baseUrl": base_url,
        "thresholds": {"errorRateBelow": 0.01, "p95BelowMs": args.p95_limit_ms},
        "stages": [run_stage(base_url, stage, args.requests_per_stage, args.timeout) for stage in stages],
    }
    report["passed"] = all(
        stage["errorRate"] < 0.01 and stage["p95Ms"] < args.p95_limit_ms for stage in report["stages"]
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
