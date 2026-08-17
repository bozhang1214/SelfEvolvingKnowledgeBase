# Phase 2 详细设计：记忆系统 + RAG + 知识库

> 文档版本：v1.0.0
> 最后更新：2026-08-12
> 状态：设计评审中
> 依赖文档：[01-architecture.md](./01-architecture.md)、[02-phase1-design.md](./02-phase1-design.md)

---

## 一、Phase 2 目标与范围

### 1.1 核心目标

Phase 2 的核心是**让 Agent 拥有真正的"大脑"**——从 L1 短期记忆扩展到 L2/L3 完整记忆体系，实现 RAG 检索增强，以及知识库的自迭代闭环。

### 1.2 必做（P0）

| 编号 | 范围 | 验收标准 |
|---|---|---|
| P0-1 | L2 中期记忆实现 | 跨会话偏好/历史话题持久化，Redis 存储 |
| P0-2 | L3 长期知识库实现 | ChromaDB 向量存储 + 结构化索引，支持检索/驱逐/合并 |
| P0-3 | RAG 检索增强 | 意图路由支持 `kb_strict`/`kb_prefer`，检索结果注入 Prompt |
| P0-4 | 知识库自迭代 | Scribe 摘要+重要性评分自动入库，冲突检测与版本管理 |
| P0-5 | PostgreSQL 存储切换 | 从 JSON 平滑迁移到 PostgreSQL，数据迁移脚本 |
| P0-6 | 文件上传处理 | PDF/Word/TXT 上传解析，自动入库 |
| P0-7 | 向量检索直连 | 绕过 MCP，直接 Python 调用向量库保证低延迟 |

### 1.3 不做

- 前端页面（Phase 3）
- 多用户鉴权（Phase 3）
- 生产部署（Phase 4）

---

## 二、L2 中期记忆（Session Memory）

### 2.1 数据模型

```python
class UserPreference(BaseModel):
    user_id: str
    key: str                              # 如 "language", "expertise_level"
    value: str                            # 如 "zh-CN", "beginner"
    confidence: float                     # 0.0~1.0，值的可信度
    updated_at: datetime
    source: str                           # "explicit"（用户明确设置）| "inferred"（LLM 推断）


class RecentTopic(BaseModel):
    user_id: str
    topic: str                            # 话题标识，如 "Python 装饰器"
    conv_id: str                          # 关联的会话 ID
    last_discussed_at: datetime
    importance: float                     # 0.0~1.0


class SessionMemoryBackend(ABC):
    @abstractmethod
    async def get_preferences(self, user_id: str) -> list[UserPreference]: ...

    @abstractmethod
    async def upsert_preference(self, pref: UserPreference) -> None: ...

    @abstractmethod
    async def get_recent_topics(self, user_id: str, limit: int = 10) -> list[RecentTopic]: ...

    @abstractmethod
    async def add_topic(self, topic: RecentTopic) -> None: ...

    @abstractmethod
    async def get_memory_context(self, user_id: str) -> dict:
        """返回完整的 L2 上下文，注入到 System Prompt"""
        ...
```

### 2.2 Redis 存储实现

```python
class RedisSessionMemory(SessionMemoryBackend):
    """基于 Redis 的 L2 记忆实现"""

    KEY_PREFIX = "l2:session:"
    PREFERENCE_KEY = "{user_id}:preferences"
    TOPIC_KEY = "{user_id}:topics"

    def __init__(self, redis_client: redis.asyncio.Redis):
        self.redis = redis_client

    async def get_preferences(self, user_id: str) -> list[UserPreference]:
        """从 Redis Hash 读取用户偏好"""
        key = f"{self.KEY_PREFIX}{self.PREFERENCE_KEY.format(user_id=user_id)}"
        raw = await self.redis.hgetall(key)
        return [UserPreference.parse_json(v) for v in raw.values()]

    async def get_recent_topics(self, user_id: str, limit: int = 10) -> list[RecentTopic]:
        """从 Redis Sorted Set 读取最近话题，按时间排序"""
        key = f"{self.KEY_PREFIX}{self.TOPIC_KEY.format(user_id=user_id)}"
        results = await self.redis.zrevrange(key, 0, limit - 1, withscores=True)
        return [RecentTopic.parse_json(json.loads(m)) for m, _ in results]
```

### 2.3 记忆提取时机

L2 记忆在以下时机由 Scribe 提取和更新：

1. **对话结束时**：Scribe 生成摘要后，同时提取用户偏好和话题
2. **偏好明确设置时**：用户说"请记住我喜欢用 Python"等
3. **周期性更新**：每日凌晨整理一次 L2 记忆（过期话题清理）

### 2.4 L2 记忆注入

L2 记忆在每轮对话开始时注入到 System Prompt：

```
System Prompt:
你是一个知识助手。以下是关于用户的已知信息：

【用户偏好】
- 语言：中文（置信度：1.0）
- 技术水平：初学者（置信度：0.7）

【最近讨论话题】
- Python 装饰器（重要性：0.8，最后讨论：2026-08-12）
- React Hooks（重要性：0.6，最后讨论：2026-08-10）

请基于以上用户信息提供更个性化的回答。
```

---

## 三、L3 长期知识库（Knowledge Base）

### 3.1 数据模型

```python
class KnowledgeEntry(BaseModel):
    entry_id: str                         # 唯一 ID
    content: str                          # 知识内容（摘要/事实）
    source: str                           # 来源："conversation" | "document" | "manual"
    source_id: str                        # 来源 ID（conv_id / doc_id）
    embedding: list[float]                # 向量表示
    metadata: dict                        # 元数据：{user_id, topic, importance_score, ...}
    created_at: datetime
    updated_at: datetime
    last_accessed_at: datetime
    access_count: int
    importance_score: float               # 0.0~1.0
    version: int                          # 版本号，用于冲突解决
    supersedes: Optional[str]             # 被此条目替代的旧条目 ID


class KnowledgeBaseBackend(ABC):
    @abstractmethod
    async def add(self, entry: KnowledgeEntry) -> str: ...

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        min_score: float = 0.3
    ) -> list[KnowledgeEntry]: ...

    @abstractmethod
    async def update(self, entry_id: str, content: str) -> KnowledgeEntry: ...

    @abstractmethod
    async def delete(self, entry_id: str) -> None: ...

    @abstractmethod
    async def evict(self, policy: EvictionPolicy) -> EvictionResult: ...

    @abstractmethod
    async def merge_duplicates(self, threshold: float = 0.95) -> int: ...
```

### 3.2 ChromaDB 实现

```python
class ChromaKnowledgeBase(KnowledgeBaseBackend):
    """基于 ChromaDB 的 L3 知识库实现"""

    def __init__(self, persist_path: str, embedding_fn: EmbeddingFunction):
        self.client = chromadb.PersistentClient(path=persist_path)
        self.collection = self.client.get_or_create_collection(
            name="knowledge",
            embedding_function=embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )
        self._embedding_fn = embedding_fn

    async def add(self, entry: KnowledgeEntry) -> str:
        """添加知识条目"""
        embedding = self._embedding_fn([entry.content])[0]
        self.collection.add(
            ids=[entry.entry_id],
            embeddings=[embedding],
            documents=[entry.content],
            metadatas=[{
                "user_id": entry.metadata["user_id"],
                "topic": entry.metadata.get("topic", ""),
                "importance_score": entry.importance_score,
                "source": entry.source,
                "created_at": entry.created_at.isoformat(),
                # ... 其他元数据
            }]
        )
        return entry.entry_id

    async def retrieve(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        min_score: float = 0.3
    ) -> list[KnowledgeEntry]:
        """检索知识，仅返回当前用户的条目"""
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where={"user_id": user_id},
            include=["documents", "metadatas", "distances"]
        )
        # 过滤低于 min_score 的结果
        entries = []
        for i, doc_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i]
            score = 1 - distance  # cosine distance → similarity
            if score >= min_score:
                entries.append(...)
        return entries
```

### 3.3 向量检索直连

**关键设计决策**：向量检索**不经过 MCP**，直接 Python 调用 ChromaDB：

```python
# tools/direct/vector_store.py
class DirectVectorStore:
    """本地向量检索直连，绕过 MCP 协议"""

    def __init__(self, kb: KnowledgeBaseBackend):
        self.kb = kb

    async def search(
        self,
        query: str,
        user_id: str,
        top_k: int = 5
    ) -> list[dict]:
        """
        直接调用知识库检索，延迟 < 10ms
        相比 MCP 调用（~50ms），快 5 倍
        """
        entries = await self.kb.retrieve(query, user_id, top_k)
        return [
            {
                "content": e.content,
                "score": 1 - distance,
                "source": e.source,
                "importance": e.importance_score
            }
            for e in entries
        ]
```

**为什么绕过 MCP？**
- 向量检索是**延迟敏感型**操作，每次对话都要调用
- MCP 协议涉及序列化/反序列化/进程通信，增加 ~40ms 开销
- 本地调用直接操作 ChromaDB 内存索引，延迟 < 10ms

### 3.4 驱逐与合并策略

```python
class EvictionPolicy(BaseModel):
    max_items: int = 10000
    stale_days: int = 7
    min_importance: float = 0.3
    similarity_merge_threshold: float = 0.95


class KnowledgeEvictor:
    """知识库守护清理机制"""

    async def run(self, policy: EvictionPolicy) -> EvictionResult:
        """
        驱逐流程：
        1. 若条目数 < max_items，跳过
        2. 标记 importance < min_importance 且 stale_days 未访问的条目
        3. 语义相似度 > merge_threshold 的条目合并
        4. 删除已标记条目
        """
        current_count = await self.kb.count()
        if current_count < policy.max_items:
            return EvictionResult(evicted=0, merged=0, skipped=True)

        # Step 1: 标记陈旧低重要性条目
        to_delete = await self._find_stale_entries(policy)

        # Step 2: 合并重复条目
        merged_count = await self._merge_similar_entries(policy)

        # Step 3: 删除
        if to_delete:
            await self.kb.batch_delete(to_delete)

        return EvictionResult(
            evicted=len(to_delete),
            merged=merged_count,
            skipped=False
        )
```

**驱逐触发**：
- 每次添加新条目时检查（增量驱逐）
- 每日凌晨定时全量驱逐（cron）

---

## 四、RAG 检索增强

### 4.1 RAG 在工作流中的位置

```
                    ┌──────────────────────┐
       User Input → │   Supervisor        │
                    │  意图识别 + 预检索    │  ← Phase 2 启用真正的预检索
                    └──────────┬───────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
    [kb_strict / kb_prefer]            [web_default / task_plan]
    强制 RAG 检索                      联网 + RAG 辅助
               │                               │
               ▼                               ▼
    ┌──────────────────────┐          ┌──────────────────────┐
    │  RAG Retriever      │          │  RAG Retriever      │
    │  Top-K 检索          │          │  Top-K 检索（辅助）  │
    │  注入 Prompt         │          │  注入 Prompt（可选） │
    └──────────┬───────────┘          └──────────┬───────────┘
               │                               │
               └───────────────┬───────────────┘
                               ▼
                    ┌──────────────────────┐
                    │      Planner +       │
                    │      Executor        │
                    └──────────────────────┘
```

### 4.2 RAG 检索流程

```python
class RAGRetriever:
    """RAG 检索器"""

    def __init__(self, vector_store: DirectVectorStore, config: RAGConfig):
        self.vector_store = vector_store
        self.config = config

    async def retrieve_for_query(
        self,
        query: str,
        user_id: str,
        intent: IntentType
    ) -> RAGResult:
        """
        根据意图执行 RAG 检索：

        kb_strict:  仅返回知识库结果，无结果时提示"知识库中暂无相关信息"
        kb_prefer:  返回知识库结果作为主答案
        web_default: 返回知识库结果作为辅助参考
        task_plan:  返回知识库结果 + 联网结果
        """
        if intent == IntentType.KB_STRICT:
            return await self._strict_rag(query, user_id)
        elif intent == IntentType.KB_PREFER:
            return await self._prefer_rag(query, user_id)
        else:
            return await self._auxiliary_rag(query, user_id)

    async def _strict_rag(self, query: str, user_id: str) -> RAGResult:
        results = await self.vector_store.search(
            query=query, user_id=user_id, top_k=5
        )
        if not results or results[0]["score"] < 0.5:
            return RAGResult(
                mode="strict",
                context=None,
                fallback_message="抱歉，知识库中暂未找到相关信息。您可以上传相关文档来补充。"
            )
        return RAGResult(mode="strict", context=results)

    async def _auxiliary_rag(self, query: str, user_id: str) -> RAGResult:
        results = await self.vector_store.search(
            query=query, user_id=user_id, top_k=3
        )
        high_score_results = [r for r in results if r["score"] > 0.85]
        return RAGResult(mode="auxiliary", context=high_score_results)
```

### 4.3 RAG 上下文注入

检索结果以结构化格式注入到 Executor 的 Prompt：

```
【知识库参考】
以下是从您的知识库中检索到的相关信息：

[参考1]（重要性：0.85，来源：对话）
Python 的装饰器是一种特殊的语法，用于在不修改函数代码的情况下扩展函数功能。

[参考2]（重要性：0.72，来源：文档）
装饰器的实现原理是函数作为一等公民，通过闭包包装原始函数。

请基于以上参考信息回答问题。如果参考信息不足，可以联网搜索补充。
```

---

## 五、知识库自迭代机制

### 5.1 写入流程

```
对话结束
    │
    ▼
Scribe 异步处理
    │
    ├── 1. 生成摘要（< 200 字）
    ├── 2. 计算重要性评分（0.0~1.0）
    ├── 3. 抽取事实三元组
    │
    ▼
重要性评分判断
    │
    ├── score < 0.3 → 跳过，不入库
    ├── 0.3 ≤ score < 0.7 → 入库为"一般知识"
    └── score ≥ 0.7 → 入库为"重要知识"，同时更新 L2 记忆
    │
    ▼
冲突检测
    │
    ├── 语义相似度 > 0.95 → 合并（保留新版本，标记 supersedes）
    ├── 语义相似度 0.80~0.95 → 并存，标记为"相关"
    └── 语义相似度 < 0.80 → 新增
    │
    ▼
写入 ChromaDB
```

### 5.2 重要性评分算法

```python
def calculate_importance_score(
    content_type: str,      # "fact" | "preference" | "conclusion" | "chitchat"
    has_factual_info: bool,  # 是否包含事实性信息
    involves_preference: bool,  # 是否涉及用户偏好
    is_complex_conclusion: bool,  # 是否是复杂任务结论
    timeliness: float,       # 信息时效性（0.0~1.0）
    user_citation_count: int  # 用户后续引用次数
) -> float:
    """
    重要性评分计算公式：
    score = 0.25 * factual_info_weight
          + 0.20 * preference_weight
          + 0.20 * complex_conclusion_weight
          + 0.15 * timeliness
          + 0.20 * citation_weight
    """
    score = 0.0

    # 事实性信息权重（25%）
    if has_factual_info:
        score += 0.25

    # 用户偏好权重（20%）
    if involves_preference:
        score += 0.20

    # 复杂结论权重（20%）
    if is_complex_conclusion:
        score += 0.20

    # 时效性权重（15%）
    score += 0.15 * timeliness

    # 引用次数权重（20%，归一化）
    score += 0.20 * min(user_citation_count / 5.0, 1.0)

    return min(score, 1.0)
```

### 5.3 冲突解决与版本管理

```python
class ConflictResolver:
    """知识库冲突解决器"""

    def __init__(self, kb: KnowledgeBaseBackend, embedding_fn: EmbeddingFunction):
        self.kb = kb
        self.embedding_fn = embedding_fn

    async def resolve_conflict(self, new_entry: KnowledgeEntry) -> ConflictResult:
        """
        冲突解决流程：
        1. 语义相似度 > 0.95：新旧条目合并，保留两者内容，版本号递增
        2. 语义相似度 0.80~0.95：并存，标记为相关，不合并
        3. 语义相似度 < 0.80：直接新增
        """
        similar_entries = await self.kb.find_similar(
            embedding=new_entry.embedding,
            user_id=new_entry.metadata["user_id"],
            threshold=0.80
        )

        if not similar_entries:
            return ConflictResult(action="insert", entry=new_entry)

        # 检查是否有被替代的旧条目
        for old in similar_entries:
            if old.importance_score < new_entry.importance_score:
                new_entry.supersedes = old.entry_id
                return ConflictResult(action="replace", entry=new_entry, old_entry=old)

        return ConflictResult(action="coexist", entry=new_entry, related_entries=similar_entries)
```

---

## 六、文件上传处理

### 6.1 支持的文件类型

| 类型 | 扩展名 | 解析方式 |
|---|---|---|
| PDF | `.pdf` | `pypdf` 或 `pdfplumber` 提取文本 |
| Word | `.docx` | `python-docx` 提取文本 |
| 纯文本 | `.txt`, `.md` | 直接读取 |
| Markdown | `.md` | 直接读取，保留格式 |

### 6.2 文件处理流程

```python
class FileProcessor:
    """文件处理器"""

    def __init__(self, kb: KnowledgeBaseBackend, config: FileConfig):
        self.kb = kb
        self.config = config

    async def process_file(
        self,
        file_path: str,
        user_id: str,
        metadata: dict
    ) -> ProcessResult:
        """
        文件处理流程：
        1. 解析文件内容
        2. 分块（chunk_size=500 tokens, overlap=50 tokens）
        3. 为每个块生成摘要 + 重要性评分
        4. 入库到 ChromaDB
        """
        content = await self._parse_file(file_path)
        chunks = self._chunk_content(content)

        inserted_count = 0
        for chunk in chunks:
            summary = await self._summarize_chunk(chunk)
            importance = self._calculate_importance(chunk, metadata)

            entry = KnowledgeEntry(
                entry_id=str(uuid.uuid4()),
                content=summary,
                source="document",
                source_id=file_path,
                embedding=self.embedding_fn([chunk])[0],
                metadata={
                    "user_id": user_id,
                    "file_name": metadata.get("file_name", ""),
                    "chunk_index": chunks.index(chunk),
                    **metadata
                },
                importance_score=importance,
                version=1
            )
            await self.kb.add(entry)
            inserted_count += 1

        return ProcessResult(
            file_path=file_path,
            chunks_created=len(chunks),
            entries_inserted=inserted_count,
            status="success"
        )
```

### 6.3 分块策略

```python
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """
    智能分块策略：
    - 优先按段落分割
    - 段落超过 chunk_size 时按句子分割
    - 相邻块保留 overlap 个 token 的重叠
    """
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) <= chunk_size:
            current_chunk += para + "\n\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            # 段落本身超过 chunk_size，按句子分割
            if len(para) > chunk_size:
                sentences = para.split(". ")
                for sent in sentences:
                    if len(current_chunk) + len(sent) <= chunk_size:
                        current_chunk += sent + ". "
                    else:
                        chunks.append(current_chunk.strip())
                        current_chunk = sent + ". "
            else:
                current_chunk = para + "\n\n"

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks
```

---

## 七、PostgreSQL 存储切换

### 7.1 数据库 Schema

```sql
-- 会话表
CREATE TABLE conversations (
    conv_id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    title VARCHAR(255),
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    message_count INT DEFAULT 0,
    total_input_tokens INT DEFAULT 0,
    total_output_tokens INT DEFAULT 0,
    total_cost_usd DECIMAL(10, 6) DEFAULT 0.000001
);

-- 消息表
CREATE TABLE messages (
    msg_id VARCHAR(36) PRIMARY KEY,
    conv_id VARCHAR(36) NOT NULL REFERENCES conversations(conv_id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,  -- user | assistant | system
    content TEXT NOT NULL,
    intent VARCHAR(50),
    tokens_input INT,
    tokens_output INT,
    latency_ms INT,
    trace_id VARCHAR(36),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_messages_conv_id ON messages(conv_id);
CREATE INDEX idx_conversations_user_id ON conversations(user_id);
CREATE INDEX idx_conversations_updated_at ON conversations(updated_at DESC);

-- L2 偏好表
CREATE TABLE user_preferences (
    user_id VARCHAR(36) NOT NULL,
    key VARCHAR(100) NOT NULL,
    value TEXT,
    confidence DECIMAL(3, 2) DEFAULT 1.0,
    source VARCHAR(20) DEFAULT 'inferred',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, key)
);

-- L2 话题表
CREATE TABLE recent_topics (
    user_id VARCHAR(36) NOT NULL,
    topic VARCHAR(200) NOT NULL,
    conv_id VARCHAR(36) NOT NULL,
    importance DECIMAL(3, 2) DEFAULT 0.5,
    last_discussed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, topic, conv_id)
);

-- L3 知识表（元数据，向量在 ChromaDB）
CREATE TABLE knowledge_entries (
    entry_id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    source VARCHAR(20) NOT NULL,  -- conversation | document | manual
    source_id VARCHAR(36),
    importance_score DECIMAL(3, 2) DEFAULT 0.5,
    version INT DEFAULT 1,
    supersedes VARCHAR(36),
    embedding_ref VARCHAR(100),  -- ChromaDB 中的引用
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ DEFAULT NOW(),
    access_count INT DEFAULT 0
);

CREATE INDEX idx_knowledge_user_id ON knowledge_entries(user_id);
CREATE INDEX idx_knowledge_importance ON knowledge_entries(importance_score DESC);
CREATE INDEX idx_knowledge_created_at ON knowledge_entries(created_at DESC);
```

### 7.2 数据迁移脚本

```python
async def migrate_json_to_postgres(
    json_dir: str,
    postgres_url: str
) -> MigrationResult:
    """
    从本地 JSON 迁移到 PostgreSQL：
    1. 读取所有 JSON 会话文件
    2. 逐个插入到 PostgreSQL
    3. 导入 ChromaDB 向量
    4. 验证数据完整性
    """
    storage = PostgresStorage(postgres_url)

    # 读取 JSON 索引
    index = load_index(json_dir)

    for conv_meta in index["conversations"]:
        conv_id = conv_meta["conv_id"]
        conv_data = load_conversation(json_dir, conv_id)

        # 插入会话
        await storage.create_conversation(conv_data["meta"])

        # 插入消息
        for msg in conv_data["messages"]:
            await storage.append_message(conv_id, msg)

    # 验证
    pg_count = await storage.count_conversations()
    json_count = len(index["conversations"])

    return MigrationResult(
        json_conversations=json_count,
        postgres_conversations=pg_count,
        messages_migrated=pg_count == json_count,
        status="success" if pg_count == json_count else "mismatch"
    )
```

---

## 八、新工具：文件解析 MCP Server

### 8.1 设计

```python
class FileParserMCPServer:
    """文件解析 MCP Server"""

    @server.list_tools()
    async def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="parse_file",
                description="解析上传的文档文件（PDF/Word/TXT）",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "文件路径"},
                        "chunk_size": {"type": "integer", "default": 500}
                    },
                    "required": ["file_path"]
                }
            ),
            Tool(
                name="list_uploaded_files",
                description="列出已上传的文件",
                inputSchema={"type": "object", "properties": {}}
            )
        ]

    @server.call_tool()
    async def call_tool(self, name: str, arguments: dict) -> list[TextContent]:
        if name == "parse_file":
            content = await self._parse(arguments["file_path"])
            chunks = self._chunk(content, arguments.get("chunk_size", 500))
            return [TextContent(type="text", text=json.dumps({"chunks": chunks}))]
        ...
```

---

## 九、Phase 2 验收标准

### 9.1 功能验收

| 验收项 | 通过标准 |
|---|---|
| L2 偏好提取 | 5 条偏好测试用例，准确率 ≥ 80% |
| L2 话题追踪 | 跨会话话题关联正确，召回率 ≥ 90% |
| L3 知识入库 | 10 条测试知识正确入库，可检索 |
| RAG 检索 | kb_strict 场景正确只返回知识库结果 |
| RAG 质量 | 检索相关度（Recall@5）≥ 0.7 |
| 冲突解决 | 相似知识正确合并，版本号递增 |
| 文件上传 | PDF/Word/TXT 正确解析入库 |
| 驱逐机制 | 超过 max_items 时自动驱逐低质量条目 |
| 数据迁移 | JSON → PostgreSQL 迁移完整，数据一致 |

### 9.2 性能验收

| 指标 | 目标 |
|---|---|
| 向量检索延迟（本地） | < 10ms |
| RAG 端到端延迟 | < 500ms |
| 知识入库吞吐 | ≥ 100 entries/s |
| 文件解析速度 | ≥ 1MB/s |

### 9.3 质量验收

| 指标 | 目标 |
|---|---|
| RAG Recall@5 | ≥ 0.7 |
| RAG 精确率（Top1） | ≥ 0.85 |
| 知识污染率（错误知识占比） | < 5% |
| 驱逐准确率（驱逐的确实是低质量知识） | ≥ 90% |
