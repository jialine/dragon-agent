#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hermes 端监听器 — 轮询 mailbox 收件箱，打印升级告警（带身份核验）。

身份核验：查收件箱必须带 agent_id + secret，从环境变量读取：
  MAILBOX_AGENT_ID      （如 hermes）
  MAILBOX_AGENT_SECRET

用法：
  python3 hermes_listener.py --db /path/agent_bus.db --agent hermes [--poll 1]
  python3 hermes_listener.py --http http://192.168.0.100:8091 --agent hermes
"""
import json
import os
import sqlite3
import sys
import time
from urllib.request import Request, urlopen

AGENT = "hermes"
AGENT_ID = os.environ.get("MAILBOX_AGENT_ID", AGENT)
AGENT_SECRET = os.environ.get("MAILBOX_AGENT_SECRET", "")


def inbox_local(db_path, agent):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM agent_mailbox WHERE status='pending' AND "
        "(to_agent='*' OR to_agent=? OR to_agent LIKE ? OR to_agent LIKE ? OR to_agent LIKE ?) "
        "ORDER BY id ASC LIMIT 100",
        (agent, agent + ",%", "%," + agent, "%," + agent + ",%")).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def inbox_http(http_url, agent):
    req = Request(http_url.rstrip("/") + "/inbox?agent=" + agent + "&status=pending",
                  headers={"X-Agent-ID": AGENT_ID, "X-Agent-Secret": AGENT_SECRET})
    with urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8")).get("messages", [])


def render(m):
    t = m["type"]
    mark = {"escalate": "🔴升级", "result": "✅完成", "task": "📋任务",
            "heartbeat": "💓心跳", "event": "ℹ️事件"}.get(t, t)
    payload = m.get("payload", "{}")
    try:
        payload = json.loads(payload)
    except Exception:
        pass
    return f"{mark} [{m['from_agent']} → {m['to_agent']}] 关联:{m['correlation_id'] or '-'} | {json.dumps(payload, ensure_ascii=False)}"


def main():
    args = sys.argv[1:]
    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default

    db = opt("--db", os.environ.get("MAILBOX_DB", ""))
    http = opt("--http", os.environ.get("MAILBOX_HTTP", ""))
    agent = opt("--agent", AGENT)
    poll = float(opt("--poll", "1"))

    if not db and not http:
        print("用法: hermes_listener.py --db PATH 或 --http URL", file=sys.stderr)
        sys.exit(1)

    seen = set()
    print(f"监听 mailbox（agent={agent}，间隔 {poll}s）...", flush=True)
    while True:
        try:
            msgs = inbox_http(http, agent) if http else inbox_local(db, agent)
            for m in msgs:
                if m["msg_id"] in seen:
                    continue
                seen.add(m["msg_id"])
                print(render(m), flush=True)
        except Exception as e:
            print(f"[listener] 轮询异常: {e}", file=sys.stderr, flush=True)
        time.sleep(poll)


if __name__ == "__main__":
    main()
