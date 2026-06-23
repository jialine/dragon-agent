#!/usr/bin/env python3
"""Simple concurrent load test for Dragon Agent API"""
import asyncio, time, httpx

API = "http://localhost:8000"
N = 20  # requests
C = 5   # concurrent

async def one(client, i, sem):
    async with sem:
        t0 = time.monotonic()
        try:
            r = await client.post(f"{API}/v1/chat", json={"messages":[{"role":"user","content":f"test {i}"}]}, timeout=60)
            return {"ok": r.status_code==200, "time": time.monotonic()-t0}
        except Exception as e:
            return {"ok": False, "time": time.monotonic()-t0, "error": str(e)[:50]}

async def main():
    async with httpx.AsyncClient() as c:
        # health
        r = await c.get(f"{API}/health")
        print(f"Health: {r.status_code}")
        # load
        sem = asyncio.Semaphore(C)
        results = await asyncio.gather(*[one(c, i, sem) for i in range(N)])
        ok = [r for r in results if r["ok"]]
        fail = [r for r in results if not r["ok"]]
        times = sorted([r["time"] for r in ok]) if ok else [0]
        print(f"OK: {len(ok)}/{N} | Fail: {len(fail)}")
        if ok: print(f"Min: {times[0]:.2f}s Avg: {sum(times)/len(times):.2f}s P95: {times[int(len(times)*0.95)]:.2f}s Max: {times[-1]:.2f}s")

if __name__ == "__main__":
    asyncio.run(main())
