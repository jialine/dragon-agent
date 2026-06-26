# Dragon Agent — 功能缺口分析

> 更新: 2026-06-26 | 状态: 核心完成，仅剩工程化

---

## 旧版 P0 项目 — 全部已实现 ✅

旧版 GAPS.md 标记为 P0 的 4 项现已全部完成：

| P0 项目 | 文件 | LOC | 状态 |
|---------|------|-----|------|
| Fact Checker | `factcheck.py` | 515 | ✅ 已实现 |
| Consensus | `consensus.py` | 397 | ✅ 已实现 |
| Source Attribution | 内嵌 `consensus.py` | — | ✅ 已实现 |
| Hallucination Metrics | `hallmetrics.py` | 436 | ✅ 已实现 |

## 旧版 P1 项目 — 全部已实现 ✅

| P1 项目 | 文件 | LOC | 状态 |
|---------|------|-----|------|
| Auth | `api/auth.py` | 618 | ✅ 已实现 |
| Billing | `api/billing.py` | 606 | ✅ 已实现 |
| API Key Mgmt | `api/apikeys.py` | 352 | ✅ 已实现 |
| Confidence Calibration | `confidence.py` | 479 | ✅ 已实现 |

---

## 当前真实缺口

### P1: 工程化 (3 项)

| 项目 | 说明 | 估价 |
|------|------|------|
| **Monitoring** | Prometheus metrics 端点，目前仅 18 LOC stub | 2 天 |
| **Docker** | Dockerfile + docker-compose | 2 天 |
| **CI/CD** | Gitee Actions 自动测试 | 1 天 |

### P2: 体验 (3 项)

| 项目 | 说明 | 估价 |
|------|------|------|
| **Web UI** | 管理面板 (70 LOC stub) | 1-2 周 |
| **行业 SKILL.md** | 金融/医疗/法律/教育知识模板 | 1-2 周 |
| **生产压测** | locust/k6 100 并发 | 2 天 |

---

## 汇总

| 优先级 | 数量 | 状态 |
|--------|:--:|:--:|
| P0 (核心缺失) | 0 项 | ✅ 全部完成 |
| P1 (产品化) | 0 项 | ✅ 全部完成 |
| P1 (工程化) | 3 项 | ❌ Docker/Monitoring/CI |
| P2 (体验) | 3 项 | ❌ WebUI/SKILL/压测 |

**总缺口: 6 项 (vs 旧版 12 项), 估价 2-4 周 (vs 旧版 17 周)**
