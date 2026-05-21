# Panda Agent — 多行业智能调度 Agent 需求文档

> 版本: v0.1 | 日期: 2026-05-19 | 作者: ANDL Team
> 参考架构: Hermes Agent (Nous Research)

---

## 1. 产品定位

### 1.1 一句话描述

**Panda Agent = 内置小模型路由 + 多行业大模型调度 + 向量知识库 + 云端备份**

### 1.2 核心价值

```
用户提问
    │
    ▼
┌─────────────────────────────────────┐
│  Panda Agent (本地/边缘)             │
│                                      │
│  ┌─────────────────────────────┐    │
│  │ 内置 0.8B 小模型 (Router)    │    │
│  │  └─ 意图识别 + 行业分类       │    │
│  └──────────┬──────────────────┘    │
│             │ dispatch               │
│     ┌───────┼───────┬────────┐      │
│     ▼       ▼       ▼        ▼      │
│  金融LLM 医疗LLM 法律LLM 通用LLM    │
│                                      │
│  ┌─────────────────────────────┐    │
│  │ 向量知识库 (ChromaDB)        │    │
│  │  └─ 企业知识 + 对话记忆      │    │
│  └─────────────────────────────┘    │
│                                      │
│  ┌─────────────────────────────┐    │
│  │ 云端备份 (S3/OSS)            │    │
│  │  └─ 知识库 + 配置 + 日志     │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘
```

**解决的问题：**
- 一个 Agent 覆盖多个垂直行业，不用装 N 个 APP
- 小模型本地路由，行业问题精准派发到专业大模型
- 企业知识本地存储（向量库），敏感数据不出内网
- 云端自动备份，换设备无缝恢复

---

## 2. 参考架构分析 (Hermes Agent)

### 2.1 可借鉴的模块

| Hermes 模块 | 路径 | Panda 借鉴 |
|-------------|------|-----------|
| **Provider 插件系统** | `plugins/model-providers/` | 行业大模型注册 (ProviderProfile) |
| **Auxiliary Client** | `agent/auxiliary_client.py` | 小模型路由 → 大模型调度的 fallback 链 |
| **Memory Provider** | `agent/memory_provider.py` | 向量知识库的抽象接口 |
| **Session Search** | `tools/session_search_tool.py` | FTS5 → 替换为 ChromaDB 向量检索 |
| **Skill System** | `~/.hermes/skills/` | 行业知识 SKILL.md (法规、术语、模板) |
| **SessionDB** | `hermes_state.py` | 对话持久化 + 云端同步 |
| **Memory Tool** | `tools/memory_tool.py` | 持久记忆的增删改查 |
| **CLI / Gateway** | `cli.py` / `gateway/` | 多平台接入 (Feishu/WeChat/Web) |
| **Config System** | `config.yaml` + `.env` | 模型/行业/备份 统一配置 |

### 2.2 关键差异

| 特性 | Hermes Agent | Panda Agent |
|------|-------------|-------------|
| 路由模型 | 配置指定主模型 | **内置 0.8B 本地小模型做路由** |
| 模型调度 | fallback 链 (同任务多模型) | **行业分类 → 精准派发** |
| 记忆系统 | 文件 MARKDOWN + FTS5 搜索 | **ChromaDB 向量检索** |
| 知识注入 | SKILL.md 文本 | **SKILL.md + 向量化嵌入** |
| 云端同步 | curator_backup.py (可选) | **内置 S3/OSS 自动备份** |
| 部署形态 | CLI + TUI + Gateway | **轻量 HTTP API + 嵌入式 SDK** |

---

## 3. 核心模块设计

### 3.1 模块一：内置小模型路由器 (Panda Router)

**目标**：本地运行 0.8B 模型，做意图识别和行业分类，不依赖外部 API。

**模型选型：**

| 候选 | 大小 | 量化后 | 优势 |
|------|------|--------|------|
| Qwen3-0.6B | 0.6B | ~400MB (Q4_K_M) | 中文强，HuggingFace 可获取 |
| Qwen3-1.7B | 1.7B | ~1.0GB (Q4_K_M) | 路由准确率更高 |
| SmolLM2-0.5B | 0.5B | ~300MB | 英文为主 |
| **推荐: Qwen3-0.6B** | 0.6B | ~400MB | 中文最优，内存可控 |

**技术方案：**

```python
# 基于 llama.cpp Python 绑定 (llama-cpp-python)
from llama_cpp import Llama

class PandaRouter:
    def __init__(self, model_path="models/qwen3-0.6b-q4_k_m.gguf"):
        self.llm = Llama(
            model_path=model_path,
            n_ctx=512,          # 路由只需短上下文
            n_threads=4,        # 4 线程足够
            embedding=False,    # 省内存
        )
        self.industries = {
            "finance":  "金融大模型 (用于风控、投研、合规)",
            "medical":  "医疗大模型 (用于诊断、病历、药学)",
            "legal":    "法律大模型 (用于合同、诉讼、合规)",
            "education":"教育大模型 (用于教案、答疑、评估)",
            "general":  "通用大模型 (兜底)",
        }
    
    def classify(self, query: str) -> dict:
        """分类用户查询到对应行业"""
        prompt = f"""分析以下用户问题，判断属于哪个行业领域。
行业列表：{', '.join(self.industries.keys())}

用户问题：{query}

返回 JSON：{{"industry": "行业", "confidence": 0.0-1.0, "reason": "理由"}}"""
        
        response = self.llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=128,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return json.loads(response['choices'][0]['message']['content'])
```

**性能要求：**
- 分类延迟: < 200ms
- 内存占用: < 600MB（含模型）
- 准确率: > 90%（5 行业分类）

### 3.2 模块二：多行业大模型调度 (Panda Dispatch)

**目标**：根据路由器结果，将请求派发到对应的行业大模型。

**技术方案（参考 Hermes auxiliary_client 的 fallback 链）：**

```python
class PandaDispatcher:
    def __init__(self, config):
        self.providers = self._load_providers(config)
    
    def _load_providers(self, config) -> dict:
        """加载行业模型配置"""
        return {
            "finance": ModelProvider(
                name="金融大模型",
                provider=config.finance.provider,    # e.g. "deepseek"
                model=config.finance.model,           # e.g. "deepseek-chat"
                api_key=config.finance.api_key,
                base_url=config.finance.base_url,
                system_prompt=config.finance.system_prompt,
            ),
            "medical": ModelProvider(...),
            "legal": ModelProvider(...),
            "education": ModelProvider(...),
            "general": ModelProvider(  # 兜底
                provider="openrouter",
                model="openai/gpt-4o-mini",
            ),
        }
    
    async def dispatch(self, industry: str, messages: list, **kwargs):
        """派发到对应行业模型"""
        provider = self.providers.get(industry) or self.providers["general"]
        
        # 注入行业 system prompt
        if provider.system_prompt:
            messages.insert(0, {"role": "system", "content": provider.system_prompt})
        
        # 调用远程模型 (OpenAI 兼容 API)
        client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)
        response = client.chat.completions.create(
            model=provider.model,
            messages=messages,
            **kwargs,
        )
        return response
```

**配置示例 (config.yaml)：**

```yaml
dispatch:
  router_model: "models/qwen3-0.6b-q4_k_m.gguf"
  router_threads: 4
  
  industries:
    finance:
      provider: "deepseek"
      model: "deepseek-chat"
      api_key: "${FINANCE_API_KEY}"
      base_url: "https://api.deepseek.com/v1"
      system_prompt: "你是金融行业专家，擅长风控分析、投资建议和合规审查。"
      
    medical:
      provider: "custom"
      model: "medical-llm-72b"
      api_key: "${MEDICAL_API_KEY}"
      base_url: "http://medical-llm.internal:8080/v1"
      system_prompt: "你是医疗行业专家，擅长诊断辅助、病历分析和药学咨询。"
      
    legal:
      provider: "zai"
      model: "glm-4"
      api_key: "${LEGAL_API_KEY}"
      system_prompt: "你是法律行业专家，擅长合同审查、诉讼分析和法规解读。"
      
    education:
      provider: "deepseek"
      model: "deepseek-chat"
      api_key: "${EDUCATION_API_KEY}"
      system_prompt: "你是教育行业专家，擅长教案设计、答疑辅导和学情评估。"
      
    general:
      provider: "openrouter"
      model: "openai/gpt-4o-mini"
      api_key: "${OPENROUTER_API_KEY}"

  # 超时与重试
  timeout_secs: 60
  max_retries: 2
  fallback_to_general: true   # 行业模型不可用时降级到 general
```

### 3.3 模块三：向量知识库 (Panda Memory)

**目标**：本地嵌入向量数据库，存储企业知识和对话记忆，支持语义检索。

**技术选型：**

| 方案 | 优势 | 劣势 |
|------|------|------|
| **ChromaDB** | 轻量、Python 原生、支持持久化 | 单机部署 |
| LanceDB | 列式存储、快 | 生态较新 |
| Qdrant | 高性能、支持过滤 | 资源占用高 |
| Milvus Lite | 功能完整 | 嵌入式版较小众 |
| **推荐: ChromaDB** | 最轻量，pip install 即用 | |

**技术方案：**

```python
import chromadb
from chromadb.utils import embedding_functions

class PandaMemory:
    def __init__(self, persist_dir="./panda_data/vectordb"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        
        # 使用本地 embedding 模型 (不依赖外部 API)
        self.embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="BAAI/bge-small-zh-v1.5"  # 中文优化, ~100MB
        )
        
        # 创建/获取集合
        self.knowledge = self.client.get_or_create_collection(
            name="enterprise_knowledge",
            embedding_function=self.embed_fn,
            metadata={"description": "企业知识库"}
        )
        self.memories = self.client.get_or_create_collection(
            name="conversation_memories",
            embedding_function=self.embed_fn,
            metadata={"description": "对话记忆"}
        )
    
    def add_knowledge(self, documents: list, metadatas: list = None, ids: list = None):
        """添加企业知识"""
        ids = ids or [f"doc_{i}" for i in range(len(documents))]
        self.knowledge.add(documents=documents, metadatas=metadatas, ids=ids)
    
    def search(self, query: str, collection: str = "knowledge", top_k: int = 5) -> list:
        """语义检索"""
        coll = self.knowledge if collection == "knowledge" else self.memories
        results = coll.query(query_texts=[query], n_results=top_k)
        return [
            {"doc": doc, "score": 1 - dist, "meta": meta}
            for doc, dist, meta in zip(
                results['documents'][0],
                results['distances'][0],
                results['metadatas'][0]
            )
        ]
    
    def remember(self, session_id: str, question: str, answer: str):
        """记忆对话"""
        self.memories.add(
            documents=[f"Q: {question}\nA: {answer}"],
            metadatas=[{"session_id": session_id, "timestamp": time.time()}],
            ids=[f"mem_{session_id}_{int(time.time())}"]
        )
    
    def recall(self, query: str, top_k: int = 5) -> list:
        """回忆相关对话"""
        return self.search(query, collection="memories", top_k=top_k)
```

**资源估算：**

| 组件 | 大小 |
|------|------|
| bge-small-zh 模型 | ~100MB |
| ChromaDB runtime | ~50MB |
| 1000 篇文档向量 | ~50MB |
| 10000 条对话记忆 | ~100MB |
| **总计** | **~300MB** |

### 3.4 模块四：云端备份 (Panda Backup)

**目标**：向量知识库、配置、日志定时同步到云端 (S3/OSS/MinIO)。

**技术方案：**

```python
import boto3
import schedule
import tarfile
from pathlib import Path

class PandaBackup:
    def __init__(self, config):
        self.s3 = boto3.client(
            's3',
            endpoint_url=config.backup.endpoint,  # OSS/MinIO endpoint
            aws_access_key_id=config.backup.access_key,
            aws_secret_access_key=config.backup.secret_key,
        )
        self.bucket = config.backup.bucket
        self.prefix = config.backup.prefix  # e.g. "panda/backups/"
        self.local_dir = Path("./panda_data")
    
    def backup(self):
        """打包并上传到云端"""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        archive = f"/tmp/panda_backup_{timestamp}.tar.gz"
        
        # 打包
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(self.local_dir, arcname="panda_data")
        
        # 上传
        key = f"{self.prefix}panda_backup_{timestamp}.tar.gz"
        self.s3.upload_file(archive, self.bucket, key)
        
        # 保留最近 7 个备份
        self._cleanup_old_backups(keep=7)
        
        logger.info(f"Backup uploaded: s3://{self.bucket}/{key}")
    
    def restore(self, backup_key: str = None):
        """从云端恢复"""
        if not backup_key:
            # 找最新备份
            resp = self.s3.list_objects_v2(
                Bucket=self.bucket, Prefix=self.prefix
            )
            backups = sorted(
                [obj['Key'] for obj in resp.get('Contents', [])],
                reverse=True
            )
            if not backups:
                raise Exception("No backup found")
            backup_key = backups[0]
        
        # 下载
        archive = "/tmp/panda_restore.tar.gz"
        self.s3.download_file(self.bucket, backup_key, archive)
        
        # 解压
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(path=self.local_dir.parent)
        
        logger.info(f"Restored from: s3://{self.bucket}/{backup_key}")
    
    def start_scheduler(self, interval_hours: int = 6):
        """定时备份"""
        schedule.every(interval_hours).hours.do(self.backup)
        while True:
            schedule.run_pending()
            time.sleep(60)
```

**支持的后端：**

| 后端 | endpoint_url | 适用场景 |
|------|-------------|----------|
| AWS S3 | (默认) | 海外部署 |
| 阿里云 OSS | `https://oss-cn-shanghai.aliyuncs.com` | 国内首选 |
| MinIO | `http://minio.internal:9000` | 私有化部署 |
| 腾讯云 COS | `https://cos.ap-shanghai.myqcloud.com` | 国内备选 |

---

## 4. 完整架构

```
┌──────────────── Panda Agent ────────────────────┐
│                                                   │
│  ┌─────────────────────────────────────────┐     │
│  │         HTTP API (FastAPI)               │     │
│  │  POST /chat         用户对话             │     │
│  │  POST /knowledge    知识库管理           │     │
│  │  GET  /status       状态查询             │     │
│  │  POST /backup       手动备份             │     │
│  │  POST /restore      从云端恢复           │     │
│  └──────────────┬──────────────────────────┘     │
│                  │                                 │
│  ┌───────────────▼──────────────────────────┐    │
│  │          PandaRouter (0.8B)               │    │
│  │  llama-cpp-python + Qwen3-0.6B-Q4_K_M    │    │
│  │  意图识别 + 行业分类 (< 200ms)             │    │
│  └───────────────┬──────────────────────────┘    │
│                  │ industry                        │
│  ┌───────────────▼──────────────────────────┐    │
│  │        PandaDispatcher                    │    │
│  │  金融LLM │ 医疗LLM │ 法律LLM │ ...        │    │
│  │  (OpenAI-compatible API dispatch)         │    │
│  └───────────────┬──────────────────────────┘    │
│                  │                                 │
│  ┌───────────────▼──────────────────────────┐    │
│  │         PandaMemory (ChromaDB)            │    │
│  │  ├─ enterprise_knowledge                 │    │
│  │  └─ conversation_memories                │    │
│  │  embedding: bge-small-zh-v1.5            │    │
│  └──────────────────────────────────────────┘    │
│                                                   │
│  ┌──────────────────────────────────────────┐    │
│  │       PandaBackup (S3/OSS/MinIO)          │    │
│  │  └─ 定时备份 + 按需恢复                   │    │
│  └──────────────────────────────────────────┘    │
└───────────────────────────────────────────────────┘
```

---

## 5. 项目结构

```
panda-agent/
├── pyproject.toml                    # Python 项目配置
├── README.md
├── config.yaml.example               # 配置模板
├── .env.example                      # API Key 模板
│
├── panda/
│   ├── __init__.py
│   ├── main.py                       # FastAPI 入口
│   ├── config.py                     # 配置加载
│   │
│   ├── router/
│   │   ├── __init__.py
│   │   ├── classifier.py             # 行业分类器 (0.8B 模型)
│   │   └── prompt.py                 # 分类 prompt 模板
│   │
│   ├── dispatch/
│   │   ├── __init__.py
│   │   ├── dispatcher.py             # 大模型调度器
│   │   └── providers.py              # 行业模型注册
│   │
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── vectordb.py               # ChromaDB 向量库
│   │   └── embedding.py              # Embedding 模型管理
│   │
│   ├── backup/
│   │   ├── __init__.py
│   │   ├── cloud.py                  # S3/OSS 备份
│   │   └── scheduler.py              # 定时备份调度
│   │
│   ├── skills/                        # 行业知识 SKILL.md
│   │   ├── finance.md
│   │   ├── medical.md
│   │   ├── legal.md
│   │   └── education.md
│   │
│   └── utils/
│       ├── __init__.py
│       └── logger.py
│
├── models/                            # 本地模型文件
│   └── qwen3-0.6b-q4_k_m.gguf       # 路由小模型 (~400MB)
│
├── panda_data/                        # 持久化数据 (gitignore)
│   ├── vectordb/                     # ChromaDB 数据
│   ├── config.yaml                   # 运行时配置
│   └── logs/
│
├── tests/
│   ├── test_router.py
│   ├── test_dispatcher.py
│   ├── test_memory.py
│   └── test_backup.py
│
└── docker/
    ├── Dockerfile
    └── docker-compose.yml
```

---

## 6. API 设计

### 6.1 对话接口

```
POST /v1/chat
Content-Type: application/json

{
  "messages": [
    {"role": "user", "content": "帮我分析这只股票的风险"}
  ],
  "session_id": "sess_abc123",
  "stream": false
}

Response:
{
  "industry": "finance",
  "confidence": 0.95,
  "model": "deepseek-chat",
  "response": "根据分析，该股票当前...",
  "knowledge_used": [
    {"doc": "股票风险评估框架.pdf", "score": 0.89},
    {"doc": "行业分析报告.md", "score": 0.82}
  ]
}
```

### 6.2 知识库管理

```
POST /v1/knowledge
Content-Type: application/json

{
  "documents": ["文档内容1", "文档内容2"],
  "metadatas": [{"source": "内部文件"}, {"source": "行业报告"}]
}

GET /v1/knowledge/search?q=风险评估&top_k=5
```

### 6.3 备份管理

```
POST /v1/backup          # 手动触发备份
GET  /v1/backup/status   # 查看备份状态
POST /v1/restore         # 从云端恢复
```

---

## 7. 部署方案

### 7.1 最小部署（单机）

```yaml
# docker-compose.yml
services:
  panda-agent:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./models:/app/models
      - ./panda_data:/app/panda_data
      - ./config.yaml:/app/config.yaml
    environment:
      - PANDA_CONFIG=/app/config.yaml
    deploy:
      resources:
        limits:
          memory: 2G    # 0.8B模型 400MB + ChromaDB 300MB + Python
          cpus: '2'
```

### 7.2 扩展部署（多行业后端）

```
                    ┌──────────────┐
                    │  Panda Agent │  (2C/2G)
                    │  :8000       │
                    └──┬───┬───┬──┘
                       │   │   │
          ┌────────────▼───▼───▼────────────┐
          │       行业大模型集群               │
          │                                  │
          │  金融 LLM :8081  (GPU 服务器)     │
          │  医疗 LLM :8082  (GPU 服务器)     │
          │  法律 LLM :8083  (API 代理)       │
          │  通用 LLM       (OpenRouter)      │
          └──────────────────────────────────┘
```

---

## 8. 开发计划

| 阶段 | 时间 | 交付物 |
|------|------|--------|
| **P0: 路由 + 调度** | 2 周 | 0.8B 模型集成、行业分类 prompt、多模型 dispatch |
| **P1: 向量知识库** | 2 周 | ChromaDB 集成、bge embedding、语义检索 API |
| **P2: 云端备份** | 1 周 | S3/OSS 备份、定时调度、一键恢复 |
| **P3: 行业知识** | 1 周 | 金融/医疗/法律/教育 SKILL.md |
| **P4: Web UI** | 2 周 | 管理面板、对话界面、知识库管理 |
| **P5: 生产加固** | 2 周 | 多平台接入、监控、压测、文档 |

---

## 9. 关键风险与对策

| 风险 | 对策 |
|------|------|
| 0.8B 模型分类不准 | 支持人工修正 → 微调（LoRA） |
| 向量库内存增长 | ChromaDB 持久化 + 定期清理过期记忆 |
| 云端备份失败 | 本地保留最近 7 个备份 + 重试机制 |
| 行业模型不可用 | 自动降级到 general 兜底模型 |
| 数据隐私合规 | 向量库本地存储，仅备份加密数据到云端 |

---

## 10. 与 Hermes Agent 的关系

```
Hermes Agent                      Panda Agent
─────────────                     ─────────────
通用 AI Agent 平台                垂直行业智能调度 Agent
                                                                  
✅ Provider 插件体系       ──→    ✅ 行业模型注册 (复用模式)
✅ Memory Provider 抽象    ──→    ✅ ChromaDB 向量库 (替换后端)
✅ Session Search (FTS5)   ──→    ✅ 向量语义检索 (升级)
✅ Auxiliary Client        ──→    ✅ 小模型路由 + 大模型调度
✅ Skill System            ──→    ✅ 行业 SKILL.md (复用格式)
✅ config.yaml             ──→    ✅ 统一配置 (复用格式)
❌ 云端备份                 ──→    ✅ S3/OSS 内置备份 (新增)
❌ 本地小模型               ──→    ✅ llama-cpp-python (新增)
```

---

**附录 A: 依赖清单**

```toml
[project]
dependencies = [
    "llama-cpp-python>=0.3.0",      # 本地小模型推理
    "chromadb>=0.5.0",               # 向量数据库
    "sentence-transformers>=3.0",    # Embedding 模型
    "fastapi>=0.115",                # HTTP API
    "uvicorn>=0.32",                 # ASGI server
    "openai>=1.50",                  # OpenAI 兼容客户端
    "boto3>=1.35",                   # S3/OSS SDK
    "schedule>=1.2",                 # 定时任务
    "pyyaml>=6.0",                   # 配置解析
    "pydantic>=2.0",                 # 数据校验
    "python-dotenv>=1.0",            # 环境变量
]
```

**附录 B: 硬件要求**

| 配置 | 最低 | 推荐 |
|------|------|------|
| CPU | 2 核 | 4 核 |
| 内存 | 2 GB | 4 GB |
| 磁盘 | 5 GB | 20 GB (含模型+知识库) |
| 网络 | 外网 (调用大模型 API) | 内网 + 外网 |
