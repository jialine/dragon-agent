#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
escalate 钩子 — Dragon 端升级通知模块（带身份核验）
Dragon 崩了/卡死/遇到决策点时，发一条 escalate 事件到 mailbox，让 Hermes 接手。

身份核验：投递必须带 agent_id + secret，从环境变量读取：
  MAILBOX_AGENT_ID   本 agent 的身份 ID（如 dragon-02）
  MAILBOX_AGENT_SECRET  本 agent 的密钥（由 mailbox.py register 生成）
未配置则拒绝投递（防止恶意非法投递）。

用法（Dragon 脚本里 import）：
  from escalate import escalate, heartbeat
  try:
      ... 生成逻辑 ...
  except Exception as e:
      escalate("dragon-02", "三国求生指南", f"生成崩溃: {e}")
      raise
  heartbeat("dragon-02", "三国求生指南", chapter=67, extra="认知值=60")
"""
import json
import os
import sqlite3
import time
import uuid
from urllib.request import Request, urlopen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("MAILBOX_DB", os.path.join(BASE_DIR, "agent_bus.db"))
HTTP_URL = os.environ.get("MAILBOX_HTTP", "")  # 如 "http://192.168.0.100:8091"
AGENT_ID = os.environ.get("MAILBOX_AGENT_ID", "")
AGENT_SECRET = os.environ.get("MAILBOX_AGENT_SECRET", "")


def _autoload():
    """环境变量未设时，自动从 secrets/<AGENT_ID>.env 加载身份（install.sh 生成的文件）。

    优先级：
    1. 环境变量 MAILBOX_AGENT_ID / MAILBOX_AGENT_SECRET 已设 → 直接用
    2. 只设了 AGENT_ID → 从 secrets/<AGENT_ID>.env 读 secret
    3. 都没设，secrets 目录里恰好只有一个 .env → 自动用它（单 agent 机器）
    """
    global AGENT_ID, AGENT_SECRET
    if AGENT_ID and AGENT_SECRET:
        return
    secrets_dir = os.environ.get("MAILBOX_SECRETS_DIR") or os.path.join(BASE_DIR, "secrets")
    env_file = None
    if AGENT_ID:
        f = os.path.join(secrets_dir, AGENT_ID + ".env")
        if os.path.exists(f):
            env_file = f
    if env_file is None and os.path.isdir(secrets_dir):
        cands = [x for x in os.listdir(secrets_dir) if x.endswith(".env")]
        if len(cands) == 1:
            env_file = os.path.join(secrets_dir, cands[0])
    if env_file and os.path.exists(env_file):
        for line in open(env_file, encoding="utf-8"):
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if line.startswith("MAILBOX_AGENT_ID="):
                AGENT_ID = line.split("=", 1)[1].strip()
            elif line.startswith("MAILBOX_AGENT_SECRET="):
                AGENT_SECRET = line.split("=", 1)[1].strip()


_autoload()


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _insert_local(from_agent, to_agent, type_, correlation_id, payload):
    """直接写本地 SQLite（同机最快，零网络）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_mailbox ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, msg_id TEXT UNIQUE NOT NULL, "
        "from_agent TEXT NOT NULL, to_agent TEXT NOT NULL, type TEXT NOT NULL, "
        "correlation_id TEXT DEFAULT '', payload TEXT DEFAULT '{}', status TEXT DEFAULT 'pending', "
        "result TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    msg_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO agent_mailbox(msg_id, from_agent, to_agent, type, correlation_id, payload, status, result, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (msg_id, from_agent, to_agent, type_, correlation_id,
         json.dumps(payload, ensure_ascii=False), "pending", "", _now(), _now()))
    conn.commit()
    conn.close()
    return msg_id


def _send_http(from_agent, to_agent, type_, correlation_id, payload):
    """走 HTTP 投递到远端 mailbox 服务（带认证 header）。"""
    body = json.dumps({"from": from_agent, "to": to_agent, "type": type_,
                       "correlation_id": correlation_id, "payload": payload}).encode("utf-8")
    req = Request(HTTP_URL.rstrip("/") + "/send", data=body,
                  headers={"Content-Type": "application/json",
                           "X-Agent-ID": AGENT_ID, "X-Agent-Secret": AGENT_SECRET})
    with urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8")).get("msg_id", "")


def _send(from_agent, to_agent, type_, correlation_id, payload):
    """优先本地，其次 HTTP。身份未配置则拒绝。绝不抛异常阻断主流程。"""
    # 身份核验：未配置 agent_id + secret 时，拒绝投递
    if not AGENT_ID or not AGENT_SECRET:
        print(f"[escalate] 拒绝投递：未配置 MAILBOX_AGENT_ID / MAILBOX_AGENT_SECRET", file=__import__("sys").stderr)
        return ""
    # 防冒充：调用的 from_agent 必须 == 本 agent 身份
    if from_agent != AGENT_ID:
        print(f"[escalate] 拒绝投递：身份 {AGENT_ID} 不能以 {from_agent} 名义投递", file=__import__("sys").stderr)
        return ""
    try:
        if HTTP_URL:
            return _send_http(from_agent, to_agent, type_, correlation_id, payload)
        return _insert_local(from_agent, to_agent, type_, correlation_id, payload)
    except Exception as e:
        print(f"[escalate] 投递失败(降级): {e}", file=__import__("sys").stderr)
        return ""


def escalate(from_agent, correlation_id, message, to_agent="hermes", **extra):
    """升级通知：Dragon 遇错/卡死/需决策时调用。"""
    payload = {"message": message, **extra}
    return _send(from_agent, to_agent, "escalate", correlation_id, payload)


def heartbeat(from_agent, correlation_id="", to_agent="hermes", **extra):
    """心跳/进度：每章或每个阶段完成时调用。"""
    payload = {"at": _now(), **extra}
    return _send(from_agent, to_agent, "heartbeat", correlation_id, payload)


def task_done(from_agent, correlation_id, result, to_agent="hermes", **extra):
    """任务完成通知。"""
    payload = {"result": result, **extra}
    return _send(from_agent, to_agent, "result", correlation_id, payload)


if __name__ == "__main__":
    # 自测
    import sys
    from_agent = AGENT_ID or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not from_agent:
        print("请设置 MAILBOX_AGENT_ID / MAILBOX_AGENT_SECRET 环境变量", file=sys.stderr)
        sys.exit(1)
    mid = heartbeat(from_agent, "测试项目", chapter=1)
    print("heartbeat msg_id:", mid)
    mid = escalate(from_agent, "测试项目", "测试升级")
    print("escalate msg_id:", mid)
