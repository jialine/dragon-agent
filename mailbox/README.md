# 轻量事件总线（mailbox）— 带 agent 身份核验

让多个 agent（Dragon、Hermes、流水线脚本）之间异步互传任务/结果/事件，靠「信封 + 信箱 + 状态」解耦。

零第三方依赖，仅用 Python stdlib（sqlite3 + http.server + urllib + hashlib + secrets）。

## 为什么需要它

多个 agent 各自跑、互相之间不知道对方状态，全靠人肉 SSH 查 log。Dragon 崩了/卡了，要等发现才知道。mailbox 让 agent 之间能「喊话」：

- **状态透明**：Dragon 每章/每阶段发心跳，Hermes 实时看到进度，不用人肉查
- **升级机制**：Dragon 崩了/卡死/遇决策点，发 escalate 事件，Hermes 立刻接手
- **任务下发**：Hermes 派活给 Dragon，人不用当中转

## 架构

```
┌──────────┐   heartbeat/escalate   ┌──────────────────────┐
│  Dragon  │ ─────────────────────► │   agent_bus.db       │
│ (agent)  │                        │  (SQLite mailbox)    │
└──────────┘                        │                      │
                                    │  agent_mailbox 表    │
┌──────────┐   task/result          │  agents 表(身份核验) │
│  Hermes  │ ◄────────────────────► │                      │
│ (agent)  │                        └──────────────────────┘
└──────────┘                                 ▲
                                             │ 轮询(1s)
                                    ┌────────┴─────────┐
                                    │ hermes_listener  │
                                    │  (打印升级告警)   │
                                    └──────────────────┘

同机：直接写 SQLite（零网络）
跨机器：mailbox.py serve 起 HTTP，MAILBOX_HTTP 指向远端
```

## 组件

| 文件 | 作用 | 谁用 |
|------|------|------|
| `mailbox.py` | 核心：SQLite 存储 + HTTP API + CLI + 身份核验 | 所有 agent |
| `escalate.py` | 升级/心跳/完成通知钩子（带认证 + auto-load） | Dragon 端 import |
| `hermes_listener.py` | 轮询收件箱，打印升级告警 | Hermes 端 |
| `test_mailbox.py` | 26 个单元测试 | 开发者 |
| `install.sh` | 一键安装（curl \| bash） | 部署者 |

## 一键安装

```bash
curl -fsSL https://gitee.com/jialine/dragon-agent/raw/main/mailbox/install.sh | bash
# 或
./install.sh --dir /opt/dragon-mailbox --agents dragon-02,hermes --serve
```

做 6 步：预检 → 部署 → **跑单元测试（全绿才算成功）** → 注册 agent + 生成 secret（权限 600）→ 写 .gitignore → 可选起服务。

安装后：
- `secrets/<agent>.env` — 每个 agent 的身份文件（`export MAILBOX_AGENT_ID=...` + `export MAILBOX_AGENT_SECRET=...`）
- `.env` — 通用配置（`MAILBOX_DB` / `MAILBOX_PORT`）
- `.gitignore` — 已忽略 `.env` / `secrets/` / `agent_bus.db`

## 身份核验（防恶意非法投递）🔐

**每个 agent 必须先注册，拿到 `agent_id` + `secret`，投递/查收/认领/确认都必须核验身份。**

1. `agents` 表存 `agent_id` + secret 的 **sha256 哈希**（明文 secret 只在注册时返回一次，之后不存明文）
2. 投递必须带 `X-Agent-ID` + `X-Agent-Secret`（HTTP header 或 `MAILBOX_AGENT_ID`/`MAILBOX_AGENT_SECRET` 环境变量）
3. **防冒充**：认证通过的 `agent_id` 必须 == 投递的 `from_agent`（A 的密钥不能以 B 名义发）
4. `/heartbeat` 开放做健康检查，其余端点（`/send` `/inbox` `/claim` `/ack`）全部强制认证，未注册/错密钥/未认证一律 401 拒绝
5. `escalate.py` 未配置身份时拒绝投递

## 快速上手

### 1. 注册 agent（一次性，生成密钥）

```bash
python3 mailbox.py register --agent dragon-02,hermes,dragon-01
# 输出（secret 只显示这一次，妥善保存）：
#   dragon-02: adbd80da...
#   hermes: 9f895372...
#   dragon-01: e705c62d...
```

### 2. Dragon 端：发事件

```python
import sys, os
sys.path.insert(0, "/home/jialine/dragon-agent/mailbox")
os.environ.setdefault("MAILBOX_AGENT_ID", "dragon-02")  # 必须设身份，autoload 才能读到 secret
from escalate import heartbeat, escalate, task_done

# 每章/每阶段完成时发心跳
heartbeat("dragon-02", "三国求生指南", chapter=67, cog=60)

# 崩了/卡死/需决策时升级
try:
    ...生成逻辑...
except Exception as e:
    escalate("dragon-02", "三国求生指南", f"生成崩溃: {e}", chapter=67)
    raise
```

**auto-load**：`escalate.py` 会自动从 `secrets/<AGENT_ID>.env` 读 secret，前提是 `MAILBOX_AGENT_ID` 环境变量已设（或 secrets 目录只有一个 .env）。

### 3. Hermes 端：监听收件箱

```bash
# 同机（直接读 SQLite）
python3 hermes_listener.py --db .../agent_bus.db --agent hermes
# 跨机器（轮询远端 HTTP）
python3 hermes_listener.py --http http://192.168.0.100:8091 --agent hermes
```

### 4. 起 HTTP 服务（跨机器通信用）

```bash
python3 mailbox.py serve --port 8091 --db .../agent_bus.db
```

## 消息类型

| type | 含义 | 触发 |
|------|------|------|
| `task` | 任务下发 | Hermes 派活给 Dragon |
| `result` | 任务完成 | Dragon 完成回报 |
| `event` | 通用事件 | 任意 |
| `heartbeat` | 心跳/进度 | 每章/每阶段 |
| `escalate` | 🔴 升级（崩/卡/需决策） | Dragon 遇错时 |

## HTTP API

| 端点 | 认证 | 说明 |
|------|------|------|
| `POST /send` | ✅ 必须 | `{from,to,type,correlation_id,payload}` → `{msg_id}` |
| `GET /inbox?agent=X&status=pending` | ✅ 必须 | `{messages:[...]}` |
| `POST /claim` | ✅ 必须 | `{msg_id,agent}` → `{ok}` |
| `POST /ack` | ✅ 必须 | `{msg_id,status,result}` → `{ok}` |
| `GET /heartbeat` | ⬜ 开放 | `{ok,now}` 健康检查 |

认证 header：`X-Agent-ID` / `X-Agent-Secret`

## 接入现有脚本（最小侵入）

```python
# 脚本顶部加一段，mailbox 缺失时静默降级（不影响主流程）
try:
    os.environ.setdefault("MAILBOX_AGENT_ID", "dragon-02")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mailbox"))
    from escalate import escalate, heartbeat, task_done as _task_done
    _MB_AGENT = os.environ.get("MAILBOX_AGENT_ID", "dragon-02")
    _MB_OK = True
except Exception:
    _MB_OK = False
    def escalate(*a, **k): return ""
    def heartbeat(*a, **k): return ""
    def _task_done(*a, **k): return ""
```

然后在关键点调用：启动时 `heartbeat(..., phase="启动")`、每章成功 `heartbeat(..., chapter=n)`、失败 `escalate(..., chapter=n)`、完成 `_task_done(...)`。

## 设计决策

- **不装 Redis**：当前 agent 数量（Dragon + Hermes + 几条流水线）到不了需要 Redis 的规模，SQLite 表 + 1s 轮询够用，且与小说生命周期引擎共享同一套心智模型。
- **身份核验是硬门槛**：secret 存哈希不存明文；防冒充；未注册/错密钥/未认证一律拒绝。
- **优先本地 INSERT，其次 HTTP**：同机直接写 SQLite（零网络开销），跨机器走 HTTP。
- **escalate 绝不阻断主流程**：投递失败只降级打印，不抛异常。
- **listener 不自动 claim**：只读 pending 不消费，由上层决定认领。
- **三层通信模型**：状态层（共享 DB）+ 事件层（mailbox）+ 能力层（MCP）。

## 环境变量

- `MAILBOX_DB`：SQLite 路径（默认 `mailbox/agent_bus.db`）
- `MAILBOX_HTTP`：远端 mailbox 服务 URL（跨机器时设置）
- `MAILBOX_AGENT_ID`：本 agent 身份 ID（autoload 依赖它找 secret）
- `MAILBOX_AGENT_SECRET`：本 agent 密钥（register 生成，或 autoload 从 secrets 读）
- `MAILBOX_SECRETS_DIR`：secrets 目录（默认 `mailbox/secrets`）

## FAQ

**Q: 跨机器怎么通？**
A: 一台机器 `mailbox.py serve` 起 HTTP，其他机器设 `MAILBOX_HTTP=http://那台:8091`。SQLite 文件不支持跨机器并发写，必须走 HTTP 或共享 NFS。

**Q: secret 泄漏了怎么办？**
A: 重新 `register` 覆盖（`INSERT OR REPLACE`），旧 secret 立即失效。

**Q: 为什么默认本地模式，不设 MAILBOX_HTTP？**
A: 设了 MAILBOX_HTTP 会强制走 HTTP 投递，但默认安装没起 serve 服务会 Connection refused。只有显式跨机器时才启用。

**Q: 怎么知道 agent 有没有收到？**
A: `GET /inbox?agent=X` 查收件箱，或跑 `hermes_listener.py` 实时看。

**Q: 消息会丢吗？**
A: SQLite 持久化，进程挂了消息还在（pending 状态）。listener 不自动 claim，不会误消费。
