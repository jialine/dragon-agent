#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mailbox 单元测试 — 覆盖身份核验、投递/查收/认领/确认全流程、以及所有攻击场景。

运行：
  python3 test_mailbox.py            # 全量
  python3 test_mailbox.py -v         # 详细输出

零第三方依赖（纯 unittest + 临时 SQLite 文件）。
"""
import json
import os
import sys
import tempfile
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mailbox import Mailbox, AuthError, make_handler


class TestMailboxAuth(unittest.TestCase):
    """身份核验相关测试（核心安全）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "test.db")
        self.mb = Mailbox(self.db)
        # 注册三个 agent
        self.keys = self.mb.register(["dragon-02", "hermes", "dragon-01"])
        self.d02 = self.keys["dragon-02"]
        self.hm = self.keys["hermes"]
        self.d01 = self.keys["dragon-01"]

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 正常流程 ----
    def test_legal_send(self):
        mid = self.mb.send("dragon-02", "hermes", "escalate", "测试",
                           {"chapter": 67}, agent_id="dragon-02", secret=self.d02)
        self.assertTrue(mid)

    def test_legal_inbox(self):
        self.mb.send("dragon-02", "hermes", "escalate", "测试", {"x": 1},
                     agent_id="dragon-02", secret=self.d02)
        msgs = self.mb.inbox("hermes", agent_id="hermes", secret=self.hm)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["from_agent"], "dragon-02")

    def test_broadcast_inbox(self):
        """to_agent='*' 广播，任何认证 agent 都能查到。"""
        self.mb.send("dragon-02", "*", "event", "广播", {"x": 1},
                     agent_id="dragon-02", secret=self.d02)
        msgs = self.mb.inbox("hermes", agent_id="hermes", secret=self.hm)
        self.assertEqual(len(msgs), 1)

    def test_claim_ack_flow(self):
        mid = self.mb.send("dragon-02", "hermes", "task", "测试", {},
                           agent_id="dragon-02", secret=self.d02)
        # claim
        self.assertTrue(self.mb.claim(mid, "hermes", agent_id="hermes", secret=self.hm))
        # claim 后不再出现在 pending
        self.assertEqual(len(self.mb.inbox("hermes", agent_id="hermes", secret=self.hm)), 0)
        # ack
        self.assertTrue(self.mb.ack(mid, "done", "完成", agent_id="hermes", secret=self.hm))

    def test_register_secret_unique(self):
        """不同 agent 的 secret 不能相同。"""
        self.assertNotEqual(self.d02, self.hm)
        self.assertNotEqual(self.d02, self.d01)

    # ---- 攻击场景（全部必须拒绝）----
    def test_reject_no_auth(self):
        with self.assertRaises(AuthError):
            self.mb.send("dragon-02", "hermes", "escalate", "测试", {})

    def test_reject_wrong_secret(self):
        with self.assertRaises(AuthError):
            self.mb.send("dragon-02", "hermes", "escalate", "测试", {},
                         agent_id="dragon-02", secret="wrongsecret")

    def test_reject_impersonation(self):
        """用 hermes 的密钥以 dragon-02 名义投递 → 必须拒绝。"""
        with self.assertRaises(AuthError) as cm:
            self.mb.send("dragon-02", "hermes", "escalate", "测试", {},
                         agent_id="hermes", secret=self.hm)
        self.assertIn("冒充", str(cm.exception))

    def test_reject_unregistered_agent(self):
        with self.assertRaises(AuthError):
            self.mb.send("attacker", "hermes", "escalate", "测试", {},
                         agent_id="attacker", secret="fakefakefake")

    def test_reject_empty_agent_id(self):
        with self.assertRaises(AuthError):
            self.mb.send("dragon-02", "hermes", "escalate", "测试", {},
                         agent_id="", secret=self.d02)

    def test_reject_empty_secret(self):
        with self.assertRaises(AuthError):
            self.mb.send("dragon-02", "hermes", "escalate", "测试", {},
                         agent_id="dragon-02", secret="")

    def test_reject_inbox_no_auth(self):
        with self.assertRaises(AuthError):
            self.mb.inbox("hermes")

    def test_reject_claim_no_auth(self):
        with self.assertRaises(AuthError):
            self.mb.claim("somemsgid", "hermes")

    def test_reject_ack_no_auth(self):
        with self.assertRaises(AuthError):
            self.mb.ack("somemsgid", "done")

    # ---- 防冒充细节 ----
    def test_cannot_send_as_other_even_if_registered(self):
        """dragon-01 注册了，但也不能以 dragon-02 名义发（身份必须==from）。"""
        with self.assertRaises(AuthError):
            self.mb.send("dragon-02", "hermes", "escalate", "测试", {},
                         agent_id="dragon-01", secret=self.d01)

    # ---- 边界 ----
    def test_register_empty(self):
        with self.assertRaises(ValueError):
            self.mb.register([])

    def test_multi_recipient_inbox(self):
        """to_agent 逗号分隔多收件人，每个都能查到。"""
        self.mb.send("hermes", "dragon-02,dragon-01", "task", "测试", {},
                     agent_id="hermes", secret=self.hm)
        self.assertEqual(len(self.mb.inbox("dragon-02", agent_id="dragon-02", secret=self.d02)), 1)
        self.assertEqual(len(self.mb.inbox("dragon-01", agent_id="dragon-01", secret=self.d01)), 1)

    def test_verify_method(self):
        self.assertTrue(self.mb.verify("dragon-02", self.d02))
        self.assertFalse(self.mb.verify("dragon-02", "bad"))
        self.assertFalse(self.mb.verify("nobody", self.d02))
        self.assertFalse(self.mb.verify("", ""))

    def test_heartbeat_open(self):
        hb = self.mb.heartbeat()
        self.assertTrue(hb["ok"])


class TestMailboxHTTP(unittest.TestCase):
    """HTTP 层认证测试（真正的攻击面）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "http_test.db")
        cls.mb = Mailbox(cls.db)
        cls.keys = cls.mb.register(["dragon-02", "hermes"])
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.mb))
        cls.port = cls.httpd.server_address[1]
        import threading
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _post(self, path, body, headers=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", path, json.dumps(body),
                     headers={"Content-Type": "application/json", **(headers or {})})
        r = conn.getresponse()
        data = json.loads(r.read().decode("utf-8"))
        code = r.status
        conn.close()
        return code, data

    def _get(self, path, headers=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", path, headers=headers or {})
        r = conn.getresponse()
        data = json.loads(r.read().decode("utf-8"))
        code = r.status
        conn.close()
        return code, data

    def _auth(self, agent):
        return {"X-Agent-ID": agent, "X-Agent-Secret": self.keys[agent]}

    def test_http_send_no_auth_401(self):
        code, data = self._post("/send", {"from": "dragon-02", "to": "hermes", "type": "event"})
        self.assertEqual(code, 401)

    def test_http_send_impersonation_401(self):
        code, data = self._post("/send", {"from": "dragon-02", "to": "hermes", "type": "event"},
                                headers=self._auth("hermes"))
        self.assertEqual(code, 401)
        self.assertIn("冒充", data["error"])

    def test_http_send_legal_200(self):
        code, data = self._post("/send", {"from": "dragon-02", "to": "hermes", "type": "escalate",
                                          "correlation_id": "测试", "payload": {"chapter": 67}},
                                headers=self._auth("dragon-02"))
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])

    def test_http_inbox_no_auth_401(self):
        code, data = self._get("/inbox?agent=hermes")
        self.assertEqual(code, 401)

    def test_http_inbox_legal_200(self):
        self._post("/send", {"from": "dragon-02", "to": "hermes", "type": "event", "payload": {"x": 1}},
                   headers=self._auth("dragon-02"))
        code, data = self._get("/inbox?agent=hermes", headers=self._auth("hermes"))
        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(data["messages"]), 1)

    def test_http_heartbeat_open(self):
        code, data = self._get("/heartbeat")
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])

    def test_http_claim_ack_flow(self):
        self._post("/send", {"from": "dragon-02", "to": "hermes", "type": "task", "payload": {}},
                   headers=self._auth("dragon-02"))
        _, inbox = self._get("/inbox?agent=hermes", headers=self._auth("hermes"))
        mid = inbox["messages"][0]["msg_id"]
        code, data = self._post("/claim", {"msg_id": mid, "agent": "hermes"}, headers=self._auth("hermes"))
        self.assertEqual(code, 200)
        code, data = self._post("/ack", {"msg_id": mid, "status": "done", "result": "ok"},
                                headers=self._auth("hermes"))
        self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
