#!/usr/bin/env python3
"""
Dragon Agent API — Concurrent Load Test
========================================

Tests 100 concurrent requests against /v1/chat and reports
min, avg, p95, and max latency.

Usage:
    python scripts/load_test.py [--url http://localhost:8000] [--concurrency 100]

Requirements:
    pip install httpx
"""

import asyncio
import time
import argparse
from typing import List, Dict, Any

import httpx

DEFAULT_URL = "http://localhost:8000"
DEFAULT_CONCURRENCY = 100
REQUEST_TIMEOUT = 120  # seconds


async def one_request(
    client: httpx.AsyncClient,
    base_url: str,
    idx: int,
    sem: asyncio.Semaphore,
) -> Dict[str, Any]:
    """Send a single chat request and measure latency."""
    async with sem:
        t0 = time.monotonic()
        try:
            resp = await client.post(
                f"{base_url}/v1/chat",
                json={
                    "messages": [
                        {"role": "user", "content": f"Load test query #{idx}: Hello, how are you?"}
                    ]
                },
                timeout=REQUEST_TIMEOUT,
            )
            elapsed = time.monotonic() - t0
            return {
                "ok": resp.status_code == 200,
                "status": resp.status_code,
                "time": elapsed,
                "idx": idx,
            }
        except httpx.TimeoutException:
            elapsed = time.monotonic() - t0
            return {"ok": False, "status": 0, "time": elapsed, "idx": idx, "error": "timeout"}
        except Exception as e:
            elapsed = time.monotonic() - t0
            return {"ok": False, "status": 0, "time": elapsed, "idx": idx, "error": str(e)[:80]}


def percentile(sorted_values: List[float], pct: float) -> float:
    """Compute the p-th percentile from sorted list."""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * (pct / 100.0)
    f_idx = int(k)
    c = k - f_idx
    if f_idx + 1 < len(sorted_values):
        return sorted_values[f_idx] + c * (sorted_values[f_idx + 1] - sorted_values[f_idx])
    return sorted_values[f_idx]


async def main():
    parser = argparse.ArgumentParser(description="Dragon Agent load test")
    parser.add_argument("--url", default=DEFAULT_URL, help="API base URL")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help="Number of concurrent requests")
    args = parser.parse_args()

    base_url = args.url
    n = args.concurrency

    print("╔══════════════════════════════════════════════╗")
    print("║   Dragon Agent — Load Test                   ║")
    print("╠══════════════════════════════════════════════╣")
    print(f"║  URL:         {base_url:<30} ║")
    print(f"║  Concurrency: {n:<30} ║")
    print("╚══════════════════════════════════════════════╝")
    print()

    async with httpx.AsyncClient() as client:
        # ── Health check ──────────────────────────────────
        print("▶ Health check...")
        try:
            r = await client.get(f"{base_url}/health", timeout=10)
            if r.status_code == 200:
                data = r.json()
                status = data.get("status", "?")
                print(f"  Health: {status} ✓")
            else:
                print(f"  Health: HTTP {r.status_code} ⚠")
        except Exception as e:
            print(f"  Health: FAILED — {e} ✗")
            print("  Aborting — API not reachable.")
            return

        # ── Warm-up request ───────────────────────────────
        print("▶ Warm-up request...")
        try:
            r = await client.post(
                f"{base_url}/v1/chat",
                json={"messages": [{"role": "user", "content": "Hello"}]},
                timeout=REQUEST_TIMEOUT,
            )
            print(f"  Warm-up: HTTP {r.status_code} ({r.elapsed.total_seconds():.2f}s)")
        except Exception as e:
            print(f"  Warm-up failed: {e} — continuing anyway...")

        # ── Concurrent load test ─────────────────────────
        print(f"\n▶ Sending {n} concurrent requests...")
        sem = asyncio.Semaphore(n)
        start = time.monotonic()

        tasks = [one_request(client, base_url, i, sem) for i in range(n)]
        results = await asyncio.gather(*tasks)

        total_time = time.monotonic() - start

        # ── Analyze results ──────────────────────────────
        ok_results = [r for r in results if r["ok"]]
        fail_results = [r for r in results if not r["ok"]]
        times = sorted([r["time"] for r in ok_results])

        print(f"\n{'='*50}")
        print(f"  RESULTS")
        print(f"{'='*50}")
        print(f"  Total requests:  {n}")
        if n > 0:
            print(f"  Successful:      {len(ok_results)} ({100*len(ok_results)/n:.1f}%)")
            print(f"  Failed:          {len(fail_results)} ({100*len(fail_results)/n:.1f}%)")
        else:
            print(f"  Successful:      0")
            print(f"  Failed:          0")
        print(f"  Total wall time: {total_time:.2f}s")
        if total_time > 0 and ok_results:
            print(f"  Throughput:      {len(ok_results)/total_time:.1f} req/s")
        print()

        if fail_results:
            print(f"  ── Failures ──")
            status_counts: Dict[int, int] = {}
            error_msgs: List[str] = []
            for f in fail_results:
                s = f.get("status", 0)
                status_counts[s] = status_counts.get(s, 0) + 1
                if f.get("error"):
                    error_msgs.append(f["error"])
            for s, c in sorted(status_counts.items()):
                label = f"HTTP {s}" if s > 0 else "Network Error"
                print(f"    {label}: {c}")
            if error_msgs:
                unique_errors = list(set(error_msgs))[:5]
                for e in unique_errors:
                    print(f"    ── {e}")
            print()

        if ok_results:
            print(f"  ── Latency (seconds) ──")
            print(f"  {'Min:':<8} {times[0]:.3f}s")
            print(f"  {'Avg:':<8} {sum(times)/len(times):.3f}s")
            print(f"  {'P50:':<8} {percentile(times, 50):.3f}s")
            print(f"  {'P95:':<8} {percentile(times, 95):.3f}s")
            print(f"  {'P99:':<8} {percentile(times, 99):.3f}s")
            print(f"  {'Max:':<8} {times[-1]:.3f}s")
            print()

            # ── Latency distribution ──────────────────────
            buckets = [0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, float("inf")]
            bucket_labels = ["<0.5s", "0.5-1s", "1-2s", "2-5s", "5-10s", "10-30s", "30-60s", ">60s"]
            print(f"  ── Distribution ──")
            prev = 0.0
            max_count = max(1, len(times))
            for upper, label in zip(buckets, bucket_labels):
                count = sum(1 for t in times if prev <= t < upper)
                bar = "█" * max(1, count * 50 // max_count)
                print(f"  {label:<8} {count:>4}  {bar}")
                prev = upper
            print()

        print(f"{'='*50}")
        print(f"  Load test complete.")


if __name__ == "__main__":
    asyncio.run(main())
