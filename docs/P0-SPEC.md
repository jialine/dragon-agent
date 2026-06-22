# Dragon Agent — P0 模块技术规格

> 集成点：所有模块输入来自 `jury.JuryVerdict`，输出经 Consensus 统一格式化

---

## 1. FactChecker (事实核查引擎)

### 1.1 接口

```python
@dataclass
class FactClaim:
    """一条可验证的事实声明"""
    text: str                          # 原始声明文本
    claim_type: str                    # "factual" | "numerical" | "logical" | "subjective"
    source_model: str                  # 哪个模型提出的
    confidence: float                  # 模型自评置信度

@dataclass
class VerificationResult:
    """一条声明的事实验证结果"""
    claim: FactClaim
    verified: bool                     # 是否通过验证
    evidence: str                      # 验证依据
    evidence_source: str               # 依据来源 (URL / doc_id / "computed")
    confidence: float                  # 验证置信度 (0-1)
    contradicting_sources: List[str]   # 矛盾来源

class FactChecker:
    def __init__(self, memory: DragonMemory, web_search_enabled: bool = True):
        ...
    
    async def verify(
        self, 
        claims: List[FactClaim],
        context: str = ""              # 原始问题上下文
    ) -> List[VerificationResult]:
        """验证一组事实声明"""
        ...
    
    def extract_claims(
        self,
        text: str,
        source_model: str
    ) -> List[FactClaim]:
        """从文本中提取可验证的事实声明"""
        ...
```

### 1.2 验证策略

| 声明类型 | 验证方法 | 优先级 |
|----------|---------|:--:|
| factual | 知识库检索 + Web Search | 1 |
| numerical | 计算验证 (Python eval) | 1 |
| logical | 逻辑一致性检查 | 2 |
| subjective | 标记为"观点"，不验证 | 3 |

### 1.3 知识库检索流程

```
claim.text → bge-small-zh embedding → ChromaDB.query(top_k=5)
  → similarity > 0.7 → verified (evidence = matched doc snippet)
  → similarity < 0.3 → need_web_search = True
  → 0.3-0.7 → 模糊匹配 → 降低置信度
```

### 1.4 Web Search 验证

```
need_web_search → DuckDuckGo/Bing API → top 3 results
  → 提取摘要 → 与 claim 语义相似度
  → similarity > 0.6 → verified (evidence = URL + snippet)
  → otherwise → unverified
```

### 1.5 依赖

- `dragon.memory` — ChromaDB 检索 (已有)
- `dragon.router` — 声明类型分类 (已有)
- Web Search API (需新增 `dragon/web_search.py`)
- `sentence-transformers` — 语义相似度 (已有)

---

## 2. Consensus (共识输出引擎)

### 2.1 接口

```python
@dataclass
class ConsensusResult:
    """多模型共识输出"""
    answer: str                       # 最终答案 (Markdown)
    confidence: float                 # 整体置信度
    agreement_level: str              # "high" | "moderate" | "low" | "none"
    agreed_claims: List[str]          # 所有模型一致的声明
    disputed_claims: List[DisputedClaim]  # 有分歧的声明
    model_positions: Dict[str, str]   # 各模型立场摘要
    sources: List[SourceAttribution]  # 来源标注列表
    verdict: JuryVerdict              # 原始陪审团裁决

@dataclass
class DisputedClaim:
    claim: str
    positions: Dict[str, str]         # model → position
    verification: Optional[VerificationResult]

class ConsensusBuilder:
    def __init__(self, fact_checker: FactChecker):
        ...
    
    async def build(
        self,
        verdict: JuryVerdict,
        question: str
    ) -> ConsensusResult:
        """从陪审团裁决构建共识输出"""
        ...
```

### 2.2 共识算法

```
输入: JuryVerdict + FactCheck results

Step 1: 语义聚类
  - 用 bge-small-zh 计算所有模型答案的 embedding
  - DBSCAN 聚类 (eps=0.3)
  - 同簇 = 语义一致

Step 2: 决议级别
  - 所有模型在同一簇 → agreement=high, 输出胜出答案
  - 多数在同一簇 (>60%) → agreement=moderate, 输出多数 + 少数报告
  - 最大簇 <60% → agreement=low, 输出 "我不确定" + 各方观点
  - 全部不同簇 → agreement=none, 输出 "模型无法达成共识"

Step 3: 声明级对比
  - 逐声明比对验证结果
  - agreed_claims = 所有模型一致 + 通过验证的声明
  - disputed_claims = 有分歧的声明 (附各方立场 + 验证结果)
```

### 2.3 输出模板

```markdown
## 回答
{共识内容}

## 置信度
🟢 高置信度 (92%) — 3/3 模型一致

## 关键声明
- ✅ {声明1} [来源: xxx] [已验证]
- ✅ {声明2} [来源: xxx] [已验证]
- ⚠️ {声明3} — 模型存在分歧:
  - 模型A认为: ...
  - 模型B认为: ...
  - 验证结果: 倾向于A (知识库支持)

## 少数派报告
模型C持不同意见: ...

## 我不确定的部分
- {问题X}: 当前信息不足以给出确定答案
```

---

## 3. Source Attribution (来源标注)

### 3.1 接口

```python
@dataclass
class SourceAttribution:
    claim: str
    source_type: str        # "knowledge_base" | "web" | "model_inference" | "computed"
    source_detail: str      # URL / doc_id / model_name
    retrieval_score: float  # 检索匹配度 (仅 kb/web)
    access_time: float      # 访问时间戳

class SourceTracker:
    def __init__(self, memory: DragonMemory):
        ...
    
    def attribute(
        self,
        claim: str,
        verification: VerificationResult
    ) -> SourceAttribution:
        ...
    
    def format_citation(
        self,
        attribution: SourceAttribution
    ) -> str:
        """格式化为可读引用"""
        ...
```

### 3.2 来源级别

```
✅ [已验证 · 知识库] — 与企业知识库匹配
✅ [已验证 · 网页] — 与实时搜索匹配  
⚠️ [模型推理 · 未验证] — 模型基于训练知识推断
❌ [存疑 · 来源矛盾] — 不同来源给出矛盾信息
💭 [观点 · 不适用] — 主观判断，无需验证
```

### 3.3 依赖

- `FactChecker.VerificationResult` — 验证结果
- `dragon.memory` — 知识库检索

---

## 4. Hallucination Metrics (幻觉率追踪)

### 4.1 接口

```python
@dataclass 
class HallucinationReport:
    session_id: str
    total_claims: int
    verified_claims: int
    unverified_claims: int
    contradicted_claims: int
    hallucination_rate: float      # 未被验证的声明比例
    confidence_calibration: float  # 模型自评 vs 实际准确率差距
    timestamp: float

class HallucinationTracker:
    def __init__(self, db_path: str = "~/.dragon/metrics.db"):
        ...
    
    def record(
        self,
        verdict: JuryVerdict,
        consensus: ConsensusResult
    ) -> HallucinationReport:
        ...
    
    def benchmark(
        self,
        test_suite: str = "truthfulqa"
    ) -> Dict[str, float]:
        """运行标准基准测试"""
        ...
    
    def dashboard(self) -> Dict:
        """幻觉率趋势数据"""
        ...
```

### 4.2 基准测试集

| 基准 | 题数 | 类型 |
|------|------|------|
| TruthfulQA | 817 | 英文事实准确性 |
| HaluEval | 5,000 | 幻觉检测 |
| **Dragon-Bench** | **200** | **中文自建基准** |

### 4.3 Dragon-Bench 设计

```
200 题，覆盖:
  - 事实知识 (40%): 历史、地理、科学事实
  - 逻辑推理 (20%): 三段论、数学证明
  - 时效信息 (20%): 现任领导人、最新事件
  - 陷阱题 (20%): 已知的模型常见幻觉

每题标准答案 + 关键事实清单
自动评分: 关键事实命中率
```

### 4.4 仪表板指标

```
- 幻觉率趋势 (日/周)
- 按声明类型分布
- 按模型分布
- 置信度校准曲线
- "I don't know" 频率
- 用户反馈覆盖
```

---

## 5. 集成架构

```
User Question
    │
    ▼
┌──────────────────────────────────────────────────────┐
│                    Dragon Pipeline                     │
│                                                       │
│  Router → Dispatch → [Model A, Model B, Model C]     │
│                              │                        │
│                              ▼                        │
│                      ┌──────────────┐                │
│                      │  Jury.debate │ (已有)          │
│                      └──────┬───────┘                │
│                             │ JuryVerdict              │
│                             ▼                        │
│                      ┌──────────────┐                │
│                      │ FactChecker  │ (P0.1 新建)     │
│                      │ .extract()   │                │
│                      │ .verify()    │                │
│                      └──────┬───────┘                │
│                             │ List[VerificationResult] │
│                             ▼                        │
│                      ┌──────────────┐                │
│                      │ Consensus    │ (P0.2 新建)     │
│                      │ .build()     │                │
│                      └──────┬───────┘                │
│                             │ ConsensusResult         │
│                             ▼                        │
│                      ┌──────────────┐                │
│                      │ Hallucination│ (P0.4 新建)     │
│                      │ Tracker      │                │
│                      │ .record()    │                │
│                      └──────────────┘                │
│                             │                        │
│                             ▼                        │
│                      User-Facing Output              │
│                      (with Source Attribution)       │
└──────────────────────────────────────────────────────┘
```

---

## 6. 开发顺序

| 顺序 | 模块 | 依赖 | 预估 |
|:--:|------|------|:--:|
| 1 | Web Search 工具 | 无 | 300 LOC |
| 2 | **FactChecker** | Web Search + Memory | 1,500 LOC |
| 3 | **Source Attribution** | FactChecker | 600 LOC |
| 4 | **Consensus** | FactChecker + Jury | 1,200 LOC |
| 5 | **Hallucination Metrics** | Consensus | 800 LOC |
| 6 | Dragon-Bench 数据集 | 无 | 200 题 |

**总计: ~4,400 LOC / 4 周**
