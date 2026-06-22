# Dragon Agent — 多行业智能调度 Agent 需求文档

> 版本: v0.1 | 日期: 2026-05-19 | 作者: ANDL Team
> 参考架构: Hermes Agent (Nous Research)

---

## 1. 产品定位

### 1.1 一句话描述

**Dragon Agent = To-C Editor 深度绑定 AgileMind Engine (灵思引擎) SaaS API × 卖 Token**

### 1.2 商业模式

```
终端用户 (To C)
    │
    ▼
┌─────────────────────────────────────┐
│  Dragon Agent (Editor)               │
│  开箱即用 · 网络版/U盘版 · 即插即用    │
│                                      │
│  ┌─────────────────────────────┐    │
│  │ 内置 0.8B 小模型 (Router)    │    │
│  │  └─ 意图识别 + 行业分类       │    │
│  └──────────┬──────────────────┘    │
│             │ dispatch               │
│             ▼                        │
│  ┌─────────────────────────────┐    │
│  │ AgileMind Engine API        │    │
│  │ (灵思引擎 SaaS Token API)   │    │
│  │ 122B MoE · 33 tok/s · 256K  │    │
│  └──────────┬──────────────────┘    │
│             │ fallback               │
│     ┌───────┼───────┬────────┐      │
│     ▼       ▼       ▼        ▼      │
│  DeepSeek OpenAI Anthropic Gemini    │
│                                      │
│  ┌─────────────────────────────┐    │
│  │ 向量知识库 (ChromaDB)        │    │
│  │  └─ 企业知识 + 对话记忆      │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘
```

### 1.3 核心价值

- **开箱即用**：网络版一键安装，U盘版即插即用
- **深度绑定 AgileMind API**：默认走自家 122B MoE 推理，按 Token 计费
- **智能路由**：0.8B 本地小模型做行业分类，精准派发
- **云端兜底**：AgileMind 不可用时自动 fallback 到 DeepSeek/OpenAI 等
- **成本优化**：上下文压缩减少 API Token 消耗

---

## 2. 参考架构分析 (Hermes Agent)

### 2.1 可借鉴的模块

| Hermes 模块 | 路径 | Dragon 借鉴 |
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

| 特性 | Hermes Agent | Dragon Agent |
|------|-------------|-------------|
| 路由模型 | 配置指定主模型 | **内置 0.8B 本地小模型做路由** |
| 模型调度 | fallback 链 (同任务多模型) | **行业分类 → 精准派发** |
| 记忆系统 | 文件 MARKDOWN + FTS5 搜索 | **ChromaDB 向量检索** |
| 知识注入 | SKILL.md 文本 | **SKILL.md + 向量化嵌入** |
| 云端同步 | curator_backup.py (可选) | **内置 S3/OSS 自动备份** |
| 部署形态 | CLI + TUI + Gateway | **轻量 HTTP API + 嵌入式 SDK** |

---

## 3. 核心模块设计

### 3.1 模块一：内置小模型路由器 (Dragon Router)

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

class DragonRouter:
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

### 3.2 模块二：多行业大模型调度 (Dragon Dispatch)

**目标**：根据路由器结果，将请求派发到对应的行业大模型。

**技术方案（参考 Hermes auxiliary_client 的 fallback 链）：**

```python
class DragonDispatcher:
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
provider:
  default: "agilemind"          # 默认走 AgileMind Engine API
  agilemind:
    model: "qwen3.5-122b-a10b"  # 122B MoE, 256K context
    # API Key: export AGILEMIND_API_KEY=your-key
    # API URL:  export AGILEMIND_API_URL=https://api.agilemind.ai/v1

dispatch:
  industries:
    finance:
      provider: "agilemind"
      model: "qwen3.5-122b-a10b"
      system_prompt: "你是金融行业专家，擅长风控分析、投资建议和合规审查。"
      
    medical:
      provider: "agilemind"
      model: "qwen3.5-122b-a10b"
      system_prompt: "你是医疗行业专家，擅长诊断辅助、病历分析和药学咨询。"
      
    legal:
      provider: "agilemind"
      model: "qwen3.5-122b-a10b"
      system_prompt: "你是法律行业专家，擅长合同审查、诉讼分析和法规解读。"
      
    education:
      provider: "agilemind"
      model: "qwen3.5-122b-a10b"
      system_prompt: "你是教育行业专家，擅长教案设计、答疑辅导和学情评估。"
      
    general:
      provider: "agilemind"
      model: "qwen3.5-122b-a10b"

  # Fallback chain
  fallback_providers:
    - provider: "deepseek"
      model: "deepseek-chat"
    - provider: "openai"
      model: "gpt-4o"

  timeout_secs: 60
  max_retries: 2
  fallback_to_general: true
```

### 3.3 模块三：向量知识库 (Dragon Memory)

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

class DragonMemory:
    def __init__(self, persist_dir="./dragon_data/vectordb"):
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

### 3.4 模块四：请求前上下文压缩 (Dragon Context Compressor)

**目标**：在每次调用行业大模型前，自动提取并压缩历史对话中的相关信息，形成精简摘要注入请求，大幅减少上送的上下文 token 数，降低 API 成本。

**为什么需要：**
- 行业大模型通常按 token 计费，上下文越长成本越高
- 多轮对话容易累积数千 token 的历史，但并非全部与当前问题相关
- 通过语义检索 + 摘要压缩，只送入「对当前问题有用的信息」

**技术方案：**

```python
class DragonContextCompressor:
    """请求前上下文压缩器 —— 减少上送 token，降低成本"""
    
    def __init__(self, memory: DragonMemory, router: DragonRouter):
        self.memory = memory          # 向量知识库 (ChromaDB)
        self.router = router          # 路由小模型 (用于摘要生成)
    
    def compress(
        self, 
        messages: list,               # 完整对话历史
        current_query: str,           # 当前用户问题
        max_context_tokens: int = 512 # 压缩后上下文上限
    ) -> dict:
        """
        从完整对话历史中提取与当前问题相关的信息，压缩为精简上下文。
        返回: {
            "summary": str,             # 精简摘要文本
            "relevant_memories": list,  # 相关历史片段
            "knowledge_hits": list,     # 知识库检索结果
            "original_tokens": int,     # 压缩前 token 数
            "compressed_tokens": int,   # 压缩后 token 数
            "compression_ratio": float, # 压缩比
        }
        """
        # Step 1: 语义检索相关历史
        relevant_memories = self.memory.recall(current_query, top_k=5)
        
        # Step 2: 检索企业知识库
        knowledge_hits = self.memory.search(
            current_query, collection="knowledge", top_k=3
        )
        
        # Step 3: 用小模型生成精简摘要
        context_snippets = [
            m["doc"] for m in relevant_memories
        ] + [k["doc"] for k in knowledge_hits]
        
        if context_snippets:
            summary_prompt = f"""以下是历史对话中与当前问题相关的信息：
{chr(10).join(f'- {s[:200]}' for s in context_snippets)}

当前用户问题：{current_query}

请用 ≤{max_context_tokens} tokens 的中文总结上述相关信息，只保留对回答当前问题有用的内容。"""
            
            summary = self.router.llm.create_chat_completion(
                messages=[{"role": "user", "content": summary_prompt}],
                max_tokens=max_context_tokens,
                temperature=0.1,
            )
            summary_text = summary['choices'][0]['message']['content']
        else:
            summary_text = ""
        
        # Step 4: 统计压缩效果
        original = sum(len(m["content"]) for m in messages)
        compressed = len(summary_text)
        
        return {
            "summary": summary_text,
            "relevant_memories": relevant_memories,
            "knowledge_hits": knowledge_hits,
            "original_tokens": original // 2,   # 粗略估算 (中文 ~2 char/token)
            "compressed_tokens": compressed // 2,
            "compression_ratio": original / max(compressed, 1),
        }
```

**集成到请求流程：**

```python
# main.py — 修改后的 chat 接口
@app.post("/v1/chat")
async def chat(request: ChatRequest):
    # 1. 路由分类
    route_result = router.classify(request.messages[-1]["content"])
    
    # 2. 【新增】上下文压缩
    ctx = compressor.compress(
        messages=request.messages,
        current_query=request.messages[-1]["content"],
    )
    
    # 3. 构建精简后的 messages
    compact_messages = []
    if ctx["summary"]:
        compact_messages.append({
            "role": "system",
            "content": f"[历史上下文摘要] {ctx['summary']}"
        })
    compact_messages.append(request.messages[-1])  # 只送当前问题
    
    # 4. 派发到行业模型（送精简后的 messages）
    response = dispatcher.dispatch(
        industry=route_result["industry"],
        messages=compact_messages,
    )
    
    return {
        **response,
        "compression": {
            "original_tokens": ctx["original_tokens"],
            "compressed_tokens": ctx["compressed_tokens"],
            "ratio": f"{ctx['compression_ratio']:.1f}x",
            "knowledge_used": [k["doc"][:80] for k in ctx["knowledge_hits"]],
        }
    }
```

**压缩效果预估：**

| 场景 | 历史 token | 压缩后 | 压缩比 | 单次节省 |
|------|-----------|--------|--------|---------|
| 5 轮对话 | ~800 | ~150 | 5.3x | ~650 tokens |
| 20 轮对话 | ~3200 | ~200 | 16x | ~3000 tokens |
| 含长文档 | ~8000 | ~300 | 26x | ~7700 tokens |

> 按 DeepSeek API 价格 ¥0.001/1K tokens 计：20 轮对话每次节省 ~¥0.003，日千次请求省 ¥3，年省 ~¥1,100。

**资源占用：**
- 路由小模型已有（0.8B），无额外显存开销
- 摘要生成延迟 < 200ms（小模型本地推理）
- ChromaDB 检索 < 50ms

### 3.5 模块五：云端备份 (Dragon Backup)

**目标**：向量知识库、配置、日志定时同步到云端 (S3/OSS/MinIO)。

**技术方案：**

```python
import boto3
import schedule
import tarfile
from pathlib import Path

class DragonBackup:
    def __init__(self, config):
        self.s3 = boto3.client(
            's3',
            endpoint_url=config.backup.endpoint,  # OSS/MinIO endpoint
            aws_access_key_id=config.backup.access_key,
            aws_secret_access_key=config.backup.secret_key,
        )
        self.bucket = config.backup.bucket
        self.prefix = config.backup.prefix  # e.g. "dragon/backups/"
        self.local_dir = Path("./dragon_data")
    
    def backup(self):
        """打包并上传到云端"""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        archive = f"/tmp/dragon_backup_{timestamp}.tar.gz"
        
        # 打包
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(self.local_dir, arcname="dragon_data")
        
        # 上传
        key = f"{self.prefix}dragon_backup_{timestamp}.tar.gz"
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
        archive = "/tmp/dragon_restore.tar.gz"
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
┌──────────────── Dragon Agent ────────────────────┐
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
│  │          DragonRouter (0.8B)               │    │
│  │  llama-cpp-python + Qwen3-0.6B-Q4_K_M    │    │
│  │  意图识别 + 行业分类 (< 200ms)             │    │
│  └───────────────┬──────────────────────────┘    │
│                  │ industry                        │
│  ┌───────────────▼──────────────────────────┐    │
│  │        DragonDispatcher                    │    │
│  │  金融LLM │ 医疗LLM │ 法律LLM │ ...        │    │
│  │  (OpenAI-compatible API dispatch)         │    │
│  └───────────────┬──────────────────────────┘    │
│                  │                                 │
│  ┌───────────────▼──────────────────────────┐    │
│  │     DragonContextCompressor (上下文压缩)    │    │
│  │  语义检索历史 + 摘要压缩 → 减少 API token │    │
│  └───────────────┬──────────────────────────┘    │
│                  │                                 │
│  ┌───────────────▼──────────────────────────┐    │
│  │         DragonMemory (ChromaDB)            │    │
│  │  ├─ enterprise_knowledge                 │    │
│  │  └─ conversation_memories                │    │
│  │  embedding: bge-small-zh-v1.5            │    │
│  └──────────────────────────────────────────┘    │
│                                                   │
│  ┌──────────────────────────────────────────┐    │
│  │       DragonBackup (S3/OSS/MinIO)          │    │
│  │  └─ 定时备份 + 按需恢复                   │    │
│  └──────────────────────────────────────────┘    │
└───────────────────────────────────────────────────┘
```

---

## 5. 项目结构

```
dragon-agent/
├── pyproject.toml                    # Python 项目配置
├── README.md
├── config.yaml.example               # 配置模板
├── .env.example                      # API Key 模板
│
├── dragon/
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
│   ├── compressor/
│   │   ├── __init__.py
│   │   ├── compressor.py             # 上下文压缩器 (语义检索 + 摘要)
│   │   └── estimator.py              # Token 估算 & 压缩统计
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
├── dragon_data/                        # 持久化数据 (gitignore)
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
  dragon-agent:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./models:/app/models
      - ./dragon_data:/app/dragon_data
      - ./config.yaml:/app/config.yaml
    environment:
      - DRAGON_CONFIG=/app/config.yaml
    deploy:
      resources:
        limits:
          memory: 2G    # 0.8B模型 400MB + ChromaDB 300MB + Python
          cpus: '2'
```

### 7.2 扩展部署（多行业后端）

```
                    ┌──────────────┐
                    │  Dragon Agent │  (2C/2G)
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
| **P2: 上下文压缩** | 1 周 | 语义检索历史 + 摘要生成、token 统计、集成到 chat 流程 |
| **P3: 云端备份** | 1 周 | S3/OSS 备份、定时调度、一键恢复 |
| **P4: 行业知识** | 1 周 | 金融/医疗/法律/教育 SKILL.md |
| **P5: Web UI** | 2 周 | 管理面板、对话界面、知识库管理 |
| **P6: 生产加固** | 2 周 | 多平台接入、监控、压测、文档 |

---

## 9. 关键风险与对策

| 风险 | 对策 |
|------|------|
| 0.8B 模型分类不准 | 支持人工修正 → 微调（LoRA） |
| 上下文摘要偏差 | 保留原始摘要文本供审计 + 摘要置信度评分 |
| 向量库内存增长 | ChromaDB 持久化 + 定期清理过期记忆 |
| 云端备份失败 | 本地保留最近 7 个备份 + 重试机制 |
| 行业模型不可用 | 自动降级到 general 兜底模型 |
| 数据隐私合规 | 向量库本地存储，仅备份加密数据到云端 |

---

## 10. 与 Hermes Agent 的关系

```
Hermes Agent                      Dragon Agent
─────────────                     ─────────────
通用 AI Agent 平台                垂直行业智能调度 Agent
                                                                  
✅ Provider 插件体系       ──→    ✅ 行业模型注册 (复用模式)
✅ Memory Provider 抽象    ──→    ✅ ChromaDB 向量库 (替换后端)
✅ Session Search (FTS5)   ──→    ✅ 向量语义检索 (升级)
✅ Auxiliary Client        ──→    ✅ 小模型路由 + 大模型调度
✅ Skill System            ──→    ✅ 行业 SKILL.md (复用格式)
✅ config.yaml             ──→    ✅ 统一配置 (复用格式)
✅ Context Compaction      ──→    ✅ 上下文压缩 (复用思路)
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
