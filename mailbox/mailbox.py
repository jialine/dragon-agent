#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
轻量事件总线 mailbox.py — SQLite 持久化 + 极简 HTTP API + agent 身份核验
零第三方依赖，仅用 Python stdlib（sqlite3 + http.server + urllib + hashlib + secrets）。

用途：让多个 agent（Dragon、Hermes、流水线脚本）之间异步互传任务/结果/事件，
靠「信封 + 信箱 + 状态」解耦，不直接同步 RPC。

## 身份核验（防恶意非法投递）

每个 agent 必须先注册（拿到 agent_id + secret），投递消息时核验身份：

1. `agents` 表存 agent_id + secret 的 sha256 哈希（明文只注册时返回一次）
2. 投递/查收/认领/确认 都必须带 `X-Agent-ID` + `X-Agent-Secret`，哈希比对
3. **防冒充**：认证通过的 agent_id 必须 == 投递的 from_agent（A 的密钥不能以 B 名义发）
4. `/heartbeat` 开放做健康检查，其余端点全部强制认证

## HTTP API（默认端口 8091）

  POST /send       body: {from,to,type,correlation_id,payload} → {msg_id}
  GET  /inbox?agent=X&status=pending          → [消息列表]
  POST /claim      body: {msg_id,agent}
  POST /ack        body: {msg_id,status,result}
  GET  /heartbeat  → {ok,now}   （开放，无需认证）

  认证 header：X-Agent-ID / X-Agent-Secret

## CLI

  python3 mailbox.py register --agent dragon-02,hermes,dragon-01   # 注册并打印密钥（仅一次）
  python3 mailbox.py serve --port 8091 --db PATH
  python3 mailbox.py send --from A --to B --type task --correlation 项目 --payload '{}' --agent-id A --secret S
  python3 mailbox.py inbox --agent B --agent-id B --secret S
"""
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DB_PATH = os.environ.get("MAILBOX_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_bus.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_mailbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  msg_id TEXT UNIQUE NOT NULL,
  from_agent TEXT NOT NULL,
  to_agent TEXT NOT NULL,
  type TEXT NOT NULL,
  correlation_id TEXT DEFAULT '',
  payload TEXT DEFAULT '{}',
  status TEXT DEFAULT 'pending',
  result TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mailbox_to_status ON agent_mailbox(to_agent, status);
CREATE INDEX IF NOT EXISTS idx_mailbox_correlation ON agent_mailbox(correlation_id);
CREATE TABLE IF NOT EXISTS agents (
  agent_id TEXT PRIMARY KEY,
  secret_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def _hash(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class AuthError(PermissionError):
    pass


class Mailbox:
    def __init__(self, db_path=DB_PATH):
        self.db = db_path
        self._ensure_schema()

    def _conn(self):
        c = sqlite3.connect(self.db, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def _ensure_schema(self):
        c = self._conn()
        c.executescript(SCHEMA)
        c.commit()
        c.close()

    def _now(self):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    # ---------- 身份核验 ----------
    def register(self, agent_ids):
        """注册 agent，返回 {agent_id: secret}。明文 secret 只在此返回一次，之后只存哈希。"""
        ids = [a.strip() for a in agent_ids if a and a.strip()]
        if not ids:
            raise ValueError("至少提供一个 agent_id")
        result = {}
        c = self._conn()
        for aid in ids:
            secret = secrets.token_hex(32)
            c.execute("INSERT OR REPLACE INTO agents(agent_id, secret_hash, created_at) VALUES(?,?,?)",
                      (aid, _hash(secret), self._now()))
            result[aid] = secret
        c.commit()
        c.close()
        return result

    def verify(self, agent_id, secret):
        """核验 agent_id + secret 是否匹配。"""
        if not agent_id or not secret:
            return False
        c = self._conn()
        row = c.execute("SELECT secret_hash FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        c.close()
        return bool(row and row["secret_hash"] == _hash(secret))

    def _require_auth(self, agent_id, secret):
        """强制认证：任何未注册/密钥错误的身份直接拒绝。"""
        if not self.verify(agent_id, secret):
            raise AuthError(f"身份核验失败: agent_id={agent_id or '(空)'} 未注册或密钥错误")

    # ---------- 消息操作 ----------
    def send(self, from_agent, to_agent, type_, correlation_id="", payload=None,
             status="pending", agent_id=None, secret=None):
        """投递消息。必须认证，且认证身份 == from_agent（防冒充）。"""
        self._require_auth(agent_id, secret)
        if agent_id != from_agent:
            raise AuthError(f"身份冒充拒绝：认证身份 {agent_id} 不能以 {from_agent} 名义投递")
        msg_id = uuid.uuid4().hex
        payload_s = json.dumps(payload, ensure_ascii=False) if payload is not None else "{}"
        c = self._conn()
        c.execute(
            "INSERT INTO agent_mailbox(msg_id, from_agent, to_agent, type, correlation_id, payload, status, result, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (msg_id, from_agent, to_agent, type_, correlation_id, payload_s, status, "", self._now(), self._now()))
        c.commit()
        c.close()
        return msg_id

    def inbox(self, agent, status="pending", limit=100, agent_id=None, secret=None):
        """查收件箱（需认证）。"""
        self._require_auth(agent_id, secret)
        c = self._conn()
        q = ("SELECT * FROM agent_mailbox WHERE status=? AND "
             "(to_agent='*' OR to_agent=? OR to_agent LIKE ? OR to_agent LIKE ? OR to_agent LIKE ?) "
             "ORDER BY id ASC LIMIT ?")
        rows = c.execute(q, (status, agent, agent + ",%", "%," + agent, "%," + agent + ",%", limit)).fetchall()
        c.close()
        return [dict(r) for r in rows]

    def claim(self, msg_id, agent, agent_id=None, secret=None):
        """认领消息（需认证）。"""
        self._require_auth(agent_id, secret)
        c = self._conn()
        cur = c.execute(
            "UPDATE agent_mailbox SET status='claimed', updated_at=? WHERE msg_id=? AND status='pending'",
            (self._now(), msg_id))
        c.commit()
        ok = cur.rowcount > 0
        c.close()
        return ok

    def ack(self, msg_id, status="done", result="", agent_id=None, secret=None):
        """确认完成（需认证）。"""
        self._require_auth(agent_id, secret)
        c = self._conn()
        cur = c.execute(
            "UPDATE agent_mailbox SET status=?, result=?, updated_at=? WHERE msg_id=?",
            (status, result, self._now(), msg_id))
        c.commit()
        ok = cur.rowcount > 0
        c.close()
        return ok

    def heartbeat(self):
        return {"ok": True, "now": self._now()}


def make_handler(mailbox):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self):
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

        def _auth(self):
            return (self.headers.get("X-Agent-ID", "").strip(),
                    self.headers.get("X-Agent-Secret", "").strip())

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                b = self._read_body()
                aid, sec = self._auth()
                if path == "/send":
                    mid = mailbox.send(
                        b.get("from", ""), b.get("to", "*"), b.get("type", "event"),
                        b.get("correlation_id", ""), b.get("payload"), b.get("status", "pending"),
                        agent_id=aid, secret=sec)
                    self._send(200, {"ok": True, "msg_id": mid})
                elif path == "/claim":
                    self._send(200, {"ok": mailbox.claim(b.get("msg_id", ""), b.get("agent", ""),
                                                        agent_id=aid, secret=sec)})
                elif path == "/ack":
                    self._send(200, {"ok": mailbox.ack(b.get("msg_id", ""), b.get("status", "done"),
                                                       b.get("result", ""), agent_id=aid, secret=sec)})
                else:
                    self._send(404, {"ok": False, "error": "unknown endpoint"})
            except AuthError as e:
                self._send(401, {"ok": False, "error": str(e)})
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/heartbeat":
                self._send(200, mailbox.heartbeat())
                return
            try:
                aid, sec = self._auth()
                if u.path == "/inbox":
                    agent = q.get("agent", [""])[0]
                    status = q.get("status", ["pending"])[0]
                    self._send(200, {"ok": True, "messages": mailbox.inbox(agent, status, agent_id=aid, secret=sec)})
                else:
                    self._send(404, {"ok": False, "error": "unknown endpoint"})
            except AuthError as e:
                self._send(401, {"ok": False, "error": str(e)})
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})

    return Handler


def serve(port=8091, db_path=DB_PATH):
    mb = Mailbox(db_path)
    handler = make_handler(mb)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    print(f"mailbox 服务已启动: http://0.0.0.0:{port}  (db={db_path})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止。", flush=True)


def _cli():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    def pop_opt(name, default=None):
        for i, a in enumerate(args):
            if a == name and i + 1 < len(args):
                v = args[i + 1]
                del args[i:i + 2]
                return v
        return default

    cmd = args[0]

    if cmd == "serve":
        port = int(pop_opt("--port", "8091"))
        db = pop_opt("--db", DB_PATH)
        serve(port, db)
        return

    db = pop_opt("--db", DB_PATH)
    mb = Mailbox(db)

    if cmd == "register":
        ids = pop_opt("--agent", "")
        if not ids:
            print("用法: mailbox.py register --agent dragon-02,hermes,dragon-01", file=sys.stderr)
            sys.exit(1)
        result = mb.register(ids.split(","))
        print("注册成功，请把每个 secret 配置到对应 agent 的环境变量 AGENT_SECRET：")
        for aid, secret in result.items():
            print(f"  {aid}: {secret}")
    elif cmd == "send":
        def get(name):
            return pop_opt(name, "")
        try:
            mid = mb.send(
                get("--from"), get("--to"), get("--type") or "event",
                get("--correlation"), json.loads(get("--payload") or "{}"),
                agent_id=get("--agent-id"), secret=get("--secret"))
            print(mid)
        except AuthError as e:
            print(f"拒绝: {e}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "inbox":
        agent = pop_opt("--agent", "")
        status = pop_opt("--status", "pending")
        try:
            for m in mb.inbox(agent, status, agent_id=pop_opt("--agent-id", ""), secret=pop_opt("--secret", "")):
                print(json.dumps(m, ensure_ascii=False))
        except AuthError as e:
            print(f"拒绝: {e}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "heartbeat":
        print(json.dumps(mb.heartbeat(), ensure_ascii=False))
    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    _cli()
