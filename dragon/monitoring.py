"""Dragon Agent — Prometheus-compatible metrics endpoint"""
from fastapi import APIRouter
import time, psutil
router = APIRouter(tags=["monitoring"])
_start = time.time()

@router.get("/metrics")
async def metrics():
    p = psutil.Process()
    m = p.memory_info()
    return (
        f"# HELP dragon_uptime_seconds Server uptime\n# TYPE dragon_uptime_seconds gauge\n"
        f"dragon_uptime_seconds {time.time()-_start:.0f}\n"
        f"# HELP dragon_memory_rss_bytes Process RSS\n# TYPE dragon_memory_rss_bytes gauge\n"
        f"dragon_memory_rss_bytes {m.rss}\n"
        f"# HELP dragon_cpu_percent CPU usage\n# TYPE dragon_cpu_percent gauge\n"
        f"dragon_cpu_percent {p.cpu_percent():.1f}\n"
    )
