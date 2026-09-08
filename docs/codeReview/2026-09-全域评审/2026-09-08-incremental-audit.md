# SelfEvolvingKnowledgeBase · 增量审计报告

> **审计基线**：上次审计时的 HEAD（当前审计目标为 HEAD\~20 之后的 20 次提交）\
> **涉及文件**：31 个（见 `git diff --name-only` 输出）\
> **审计范围**：新增功能质量、架构一致性、安全隐患、性能瓶颈、潜在 bug、与存量代码的耦合点

***

## 新增功能概览

| # | 功能                             | 核心模块                                                                            | 复杂度 |
| - | ------------------------------ | ------------------------------------------------------------------------------- | --- |
| 1 | **用户画像地基**                     | models/profile.py + storage + /profile 路由 + job/profile.py                      | M   |
| 2 | **技能模式 + 反馈闭环**                | chat.py（skill 参数 + SystemMessage 注入 + `<PREF>` 标签 + 后台记录员 LLM）                  | L   |
| 3 | **对话排队 + 后端并发锁**               | chat.ts store（pendingQueue）+ chat.py 路由（\_conv\_inflight set）                   | M   |
| 4 | **ChromaDB 加固**                | knowledge\_base.py（retrieve + list\_entries 重构）+ rebuild script + backup script | M   |
| 5 | **知识库分享按分类限定**                 | models/share.py + share\_storage.py（category\_l1/l2/l3）                         | M   |
| 6 | **list\_entries 原生 offset 分页** | knowledge\_base.py（limit+offset 替代 skip）+ include 字段优化                          | S   |
| 7 | **账号隔离修复**                     | 原始文件存储 + MD5 索引按 user\_id 隔离                                                    | S   |
| 8 | **右侧历史对话 Drawer + Skill 选择器**  | Chat.tsx（HistoryOutlined + Drawer + SKILLS 数组）                                  | S   |

***

## 一、模块级发现

### 1. 用户画像地基 ✅ 设计良好

**核心文件**：[profile.py（模型）](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/models/profile.py) / [profile\_storage.py](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/storage/profile_storage.py) / [profile.py（路由）](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/api/routes/profile.py) / [profile.py（job 模块）](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/agents/job/profile.py)

**亮点**：

- Pydantic 模型定义清晰，`ProfileUpdate` 用 `| None = None` + `model_dump(exclude_unset=True)` 实现"只合并传入字段"

- ProfileStorage 写入用临时文件 + `os.replace` 原子重命名，避免并发写损坏 ✅

- `upsert_update` 嵌套字段深合并（`{**data[key], **value}`），对 `job_preferences` 等嵌套对象友好 ✅

- job/profile.py 同时支持 README.yaml 加载 + 按 user\_id 实时画像补充，**双向画像**（README 是静态基准 + 用户在对话中表达的偏好动态覆盖）

- 画像注入链路完整：`load_user_profile(user_id)` → 读取 README → 叠加 ProfileStorage → 职位分析 prompt

**发现的问题**：

| #  | 严重   | 位置                                                  | 问题                                                                                                 | 建议                                                                        |
| -- | ---- | --------------------------------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| P1 | 🟡 中 | profile\_storage.py L29                             | `user_id` 用 `replace("/","_")` 做路径安全，但没做 `..` 过滤                                                   | 加 `user_id = re.sub(r'[^\w-]', '_', user_id)` 白名单过滤                       |
| P2 | 🟡 中 | profile\_storage.py L32-40                          | `get_sync` 捕获 `except Exception` 太宽，把所有异常吞了只返回 None                                                | 区分 JSONDecodeError / OSError / Pydantic ValidationError 分别处理              |
| P3 | 🟢 低 | profile\_storage.py L58-75                          | `upsert_update` 是 async 但内部自己 import datetime                                                      | 顶格 import，代码风格统一                                                          |
| P4 | 🟢 低 | profile.py（job）L112-121                             | `ProfileStorage("data/profile").get_sync(user_id)` 每次调用都 new 一个实例，不走 AppContext 的 profile\_storage | 注入 AppContext 统一管理（可通过 `AppContext` 新增 profile\_storage 字段，bootstrap 时装配） |
| P5 | 🟡 中 | profile.py（job）L79-98 `_profile_to_supplement_text` | 这个函数和 chat.py 里 `_build_skill_context` 中拼装画像的逻辑重复了                                                 | 抽一个 `profile_to_context_text(profile)` 到公共 utils，两处复用                     |

***

### 2. 技能模式 + 反馈闭环 🔴 有设计缺陷

**核心文件**：[chat.py](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/api/routes/chat.py) 新增 \~500 行

**亮点**：

- `request.skill` 参数在 ChatRequest 模型中声明，三档技能模式（通用助手 / 应聘助手 / 科技资讯助手）

- **反馈闭环方案 A**：应聘助手在回复末尾输出 `<PREF>...</PREF>` 标签，系统提取后写入画像

- **反馈闭环方案 B**：后台 `asyncio.create_task` 调度独立 LLM 调用（用 `chat_simple` 角色），**不阻塞主回复** 做偏好抽取 ✅

- `_conv_inflight: set[str] = set()` 做会话并发锁，防止同一会话多标签页并发打断

**严重问题**：

| #  | 严重   | 位置                                                                | 问题                                                                                              | 影响                                                                                                                           | 建议                                                                                                                                     |
| -- | ---- | ----------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Q1 | 🔴 高 | chat.py L417-422 `graph_input` 拼接                                 | `skill_ctx + '\n\n===== 用户问题 =====\n' + user_input` 直接拼到 user\_input 里注入 LangGraph              | **污染对话历史**：`graph_input` 作为 user\_input 存入 JSON Storage + L1 ShortTermMemory，下次恢复历史时会看到用户说过"你是求职偏好记录员..." 这种鬼话               | 不要改 user\_input。**单独注入 SystemMessage** 到 GraphState 的 conversation\_history 里，Supervisor → Planner 链路在构建 messages 时 prepend 一个系统级的技能指令 |
| Q2 | 🔴 高 | chat.py `_build_job_analysis_context` L131-215                    | 同步读 archive + market 模块（archive.list\_reports 是文件 IO），没有异步包装                                    | **阻塞事件循环**：`archive.list_reports(user_id)` 是同步 Python 字典操作，虽然不是网络 IO，但也不优雅；而且在高并发 chat\_stream 下，所有请求都在 event loop 里串行做磁盘 IO | 用 `asyncio.to_thread` 包装，或把 job\_agent 注入 AppContext 后异步调用                                                                             |
| Q3 | 🟡 中 | chat.py `_PREF_EXTRACT_PROMPT` + `_record_preferences_task`       | 后台记录员 LLM 每轮对话（应聘助手模式）都额外调一次 LLM                                                                | **成本翻倍**：应聘助手一次对话 = 主 LLM（reasoner 复杂任务）+ 后台 chat\_simple 抽取 = 2 次 LLM 调用                                                    | 只在检测到 `request.skill == "应聘助手"` 且答案长度 > 50 字时才触发；或每隔 N 轮抽取一次                                                                           |
| Q4 | 🟡 中 | chat.py `_PREF_RE = re.compile(r"<PREF>(.*?)</PREF>", re.DOTALL)` | 用正则匹配自设计的标签，脆弱                                                                                  | LLM 可能输出 `<pref>` 小写、`<PREF >` 带空格、或被 markdown code fence 包裹                                                                 | 用更健壮的解析：先 strip 掉 \`\`\` 围栏、大小写不敏感正则、标签内 JSON 用 `_extract_json_from_llm` 兜底                                                            |
| Q5 | 🟡 中 | chat.py `_background_extract_tasks: set[Any]`                     | 全局 set 持有后台 task，done 时 discard                                                                 | 进程不退出就没问题；但如果用户快速发多轮应聘助手消息，每轮都 `create_task`，极端情况下可能有 1000+ task 同时 pending                                                  | 加个 semaphore 做并发限制，或排队串行执行                                                                                                             |
| Q6 | 🟡 中 | chat.py L672 `_conv_inflight: set[str]` 并发锁                       | 用普通 `set` 做并发保护，读写都在 async 函数里                                                                  | Python asyncio 默认单线程，当前没问题。但如果未来加多 worker（gunicorn / uvicorn workers），每个 worker 都有独立 set，锁失效                                 | 注释里说明"单 worker 有效"，或用 Redis 做分布式锁                                                                                                      |
| Q7 | 🟢 低 | chat.py `_extract_and_update_profile` + `_apply_pref_to_profile`  | 两个函数做几乎一样的事（提取 pref → 写画像），只是一个带标签、一个纯 JSON                                                     | 代码重复                                                                                                                         | 抽 `_write_pref_to_profile(user_id, pref_dict)` 共用                                                                                      |
| Q8 | 🟢 低 | chat.py `_extract_json_from_llm`                                  | 和 BaseAgent.\_parse\_json\_response + KnowledgeIngester.\_parse\_facts\_response 做几乎一样的 JSON 解析 | 三处重复                                                                                                                         | 抽到公共 `utils/json_parser.py`                                                                                                            |

**Q1 详细分析**（最关键的设计问题）：

```python
# 当前实现（chat.py L417-422）
skill_ctx = await _build_skill_context(ctx, user_id, skill)
if skill_ctx:
    graph_input = f"{skill_ctx}\n\n===== 用户问题 =====\n{user_input}"
# 然后把 graph_input 当 user_input 传给 LangGraph
state = create_initial_state(user_input=graph_input, ...)
```

**问题链**：

1. `graph_input` → 存入 GraphState.user\_input → LangGraph 工作流跑完全程
2. `_run_chat` L233-234 持久化：`append_message(conv_id, {"role": "user", "content": user_input})`
3. 下一次 `get_messages` 恢复 L1 memory → 用户消息变成了"你是求职顾问..."
4. Supervisor 看到的对话历史里用户莫名其妙地说"我是求职顾问" → 意图识别会混乱

**正确做法**：

```python
# 正确方案：graph_input 不变，skill 指令作为 SystemMessage 注入
skill_system_msg = SystemMessage(content=skill_ctx)
# 加到 conversation_history 的最前面（LangGraph 各节点都能看到）
history = [skill_system_msg, *history] if skill else history
state = create_initial_state(user_input=user_input, history=history, ...)
```

这样 user\_input 保持干净，技能指令作为系统级上下文，不会被用户消息污染。

***

### 3. 对话排队 + 后端并发锁 ✅ 基本可行，但有边界

**核心文件**：[chat.ts（store）](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/frontend/src/stores/chat.ts) / [chat.py（路由）](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/api/routes/chat.py)

**亮点**：

- 前端 `pendingQueue` + `queueSending` 状态，`isStreaming` 时新输入自动入队

- `flushQueue` 在 onDone / onError 后自动触发，逐条发

- `cancelStream` 会同时清空 pendingQueue，语义一致

- 后端 `_conv_inflight` set，key = `f"{user_id}|{conv_id}"`，并发时返回友好错误

**问题**：

| #   | 严重   | 位置                                                              | 问题                                                                                                                         | 建议                                 |
| --- | ---- | --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| Q9  | 🟡 中 | chat.ts `pendingQueue` 没有大小限制                                   | 用户疯狂输入可以无限堆积，极端情况内存爆                                                                                                       | 加 `MAX_QUEUE_SIZE = 10`，超过提示"队列已满" |
| Q10 | 🟢 低 | chat.ts store `clearQueue` + `set({ isStreaming: false, ... })` | cancelStream 会同时 abort 流和清空队列，但 onError 也会 flushQueue。如果 abort 后 error callback 先触发，flushQueue 会在 isStreaming 仍为 true 时被跳过 | 加个 abort\_pending 标志位协同            |
| Q11 | 🟢 低 | chat.py `_conv_inflight` 没 TTL                                  | 如果 event\_generator 因网络异常退出但未走到 finally，锁永远不释放                                                                             | 加一个 60s TTL 的清理协程定时扫               |

***

### 4. ChromaDB 加固 ✅ 质量很高

**核心文件**：[knowledge\_base.py](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/memory/knowledge_base.py)（list\_entries 重构 + retrieve 加分类过滤）/ [backup\_kb.sh](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/scripts/backup_kb.sh) / [rebuild\_chroma\_vectors.py](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/scripts/rebuild_chroma_vectors.py)

**亮点**：

- list\_entries 从 `limit=limit+offset + 手动 skip offset` 改为 ChromaDB 原生 `offset=offset`，大库 20s → 秒级 ✅

- `include` 参数可选，分类统计只传 `["metadatas"]` 不加载全文 ✅

- `or []` 兜底修复 ChromaDB 未请求字段时返回 None 而非空列表 ✅

- retrieve 复用 `_build_where_filter` 加 category\_l1/l2/l3 过滤 ✅

- rebuild\_chroma\_vectors.py 流程完整：读→备份→重嵌入→删旧→重建→校验 count + 探针检索 ✅

- backup\_kb.sh 停 backend → tar 数据卷 → 重启 → 14 天轮转 ✅

**小问题**：

| #   | 严重   | 位置                                  | 问题                                                     | 建议                                                                                               |
| --- | ---- | ----------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| Q12 | 🟡 中 | backup\_kb.sh                       | `stop backend` 再打包数据卷——停服时间取决于 chroma\_db 大小，可能需要 30s+ | 用 chromadb 的 checkpoint/snapshot API 做在线备份（Chroma 0.5+ 支持 `client.persist()` + 直接 tar sqlite 文件） |
| Q13 | 🟢 低 | rebuild\_chroma\_vectors.py L38     | `sys.path.insert(0, "/app")` 硬编码路径                     | 改为 `sys.path.insert(0, Path(__file__).resolve().parent.parent.as_posix())` 相对定位                  |
| Q14 | 🟢 低 | rebuild\_chroma\_vectors.py dry-run | dry-run 导出备份但没导出完整 metadatas（documents 全空），恢复时丢失内容     | dry-run 也导出完整 payload                                                                            |

***

### 5. 知识库分享按分类限定 ✅ 设计合理

**核心文件**：[models/share.py](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/models/share.py) / [share\_storage.py](file:///Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend/app/storage/share_storage.py)

**亮点**：

- `category_l1/l2/l3` 三级分类限定分享范围，空字符串 = 不限 ✅

- `is_valid()` + `is_scoped()` + `category_label()` 三个辅助方法覆盖常见判断场景 ✅

- ShareStorage 读写文件原子操作 ✅

- 分享会话按 `(share_id, viewer_user_id)` 隔离，每个 viewer 一个独立会话 ✅

**问题**：

| <br /> | #    | 严重                                               | 位置                                                                                                                                        | 问题                                                                                                | 建议                               |
| :----- | ---- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------- |
| Q15    | 🟡 中 | share\_storage.py L47-48                         | `self._save_sync` 是同步函数但在 delete\_share 里通过 `await asyncio.to_thread(self._save_sync)` 调用；但函数内部用 `json.dump(self._shares, ...)` 直接序列化整个字典 | `self._shares` 在高并发写时可能 race condition。虽然 Python GIL 保护单 dict 写，但多个 thread 同时调 `_save_sync` 会串行，慢 | 用 asyncio.Lock 保护                |
| Q16    | 🟡 中 | share\_storage.py L118                           | `_conv_file` 每次 append\_message 都读完整 messages → 加 → 写回                                                                                    | 大会话时 O(n) 每次追加。应该用 append 模式（jsonl 或 JSON Streaming）或 Redis List                                  | 改 jsonl：每行一条完整 message 对象，追加只写一行 |
| Q17    | 🟢 低 | share\_storage.py L23 `_shares: dict[str, dict]` | `_ensure_initialized` 会把 shares.json 全部 load 进内存                                                                                          | 当前条目不多（KB 级），OK；但要考虑条目多了后需要分页加载                                                                   | 大库时分页懒加载                         |

***

## 二、新增功能 vs 存量代码的耦合点

### 耦合 1：多个 JSON 存储并行存在

| 存储                               | 实例化方式                      | 路径                  |
| -------------------------------- | -------------------------- | ------------------- |
| AppContext.storage (JSONStorage) | bootstrap 注入               | data/conversations/ |
| ProfileStorage                   | **chat.py 里临时 new**        | data/profile/       |
| ShareStorage                     | bootstrap 注入 AppContext    | data/shares.json    |
| ChatShareStorage                 | bootstrap 注入 AppContext    | data/chat\_shares/  |
| UserStorage                      | bootstrap 注入 AppContext    | data/users/         |
| Job archive (内置)                 | **job profile.py 里临时 new** | data/job/           |

**问题**：chat.py 里 `ProfileStorage("data/profile").upsert_update(user_id, patch)` 直接 new，绕过了 AppContext 的生命周期管理。如果未来要做存储层切换（JSON→Postgres），chat.py 需要同步改。

**建议**：统一走 AppContext.profile\_storage（已在 bootstrap.py L201-202 装配），chat.py 的 `ctx` 参数里已经有 AppContext，直接用 `ctx.profile_storage`。

### 耦合 2：chat.py 持续膨胀

chat.py 当前 **\~500 行**，新增技能模式后膨胀到 **\~950 行**，承担了：

- 聊天主流程（\_run\_chat + \_stream\_tokens）

- 技能上下文构建（\_build\_skill\_context + \_build\_job\_analysis\_context）

- 偏好提取（\_extract\_and\_update\_profile + \_record\_preferences\_task + \_schedule\_preference\_extraction）

- 并发锁

**建议**：拆出 `chat_skill.py` 和 `chat_skill_feedback.py` 两个模块，主 chat.py 只做路由和 SSE 编排。

### 耦合 3：JSON 解析逻辑三处重复

| 位置                     | 函数                       | 行数       |
| ---------------------- | ------------------------ | -------- |
| base.py                | `_parse_json_response`   | L77-143  |
| knowledge\_ingestor.py | `_parse_facts_response`  | L587-661 |
| chat.py（新增）            | `_extract_json_from_llm` | L346-363 |

**建议**：抽到 `utils/json_parser.py` 做统一函数。

***

## 三、安全审查

### 🔴 S1：用户路径安全

**位置**：profile\_storage.py L29 / share\_storage.py L115

```python
# profile_storage.py
safe = user_id.replace("/", "_").replace("\\", "_")
return self._dir / f"{safe}.json"
```

**风险**：`..` 没过滤，攻击者构造 `user_id = "../../etc"` 会路径穿越。虽然当前 user\_id 是 JWT 里的服务端生成值，但防御性编程应该白名单过滤。

**建议**：

```python
import re
safe = re.sub(r'[^\w-]', '_', user_id)  # 只保留字母数字下划线
```

### 🟡 S2：后台 LLM 抽取任务无重试上限

**位置**：chat.py `_record_preferences_task` — 直接 try except 吞掉

如果 LLM 持续超时，每次应聘助手对话都会创建一个失败的后台 task。虽然有 done callback discard，但 pending 的 task 会积累。

### 🟡 S3：Skill 模式绕过了 Supervisor 意图识别

**位置**：chat.py L417-422

```python
if skill and skill != "通用助手":
    skill_ctx = await _build_skill_context(ctx, user_id, skill)
    graph_input = f"{skill_ctx}\n\n===== 用户问题 =====\n{user_input}"
```

Skill 模式下，**Supervisor 的意图识别完全基于拼接过的 graph\_input 做判断**。Supervisor 会看到"你是求职顾问..."然后判断意图。但如果用 SystemMessage 注入（Q1 修复方案），Supervisor 会正确地只看 user\_input 部分做意图判断。

### 🟢 S4：ShareStorage 原子写入 ✅

`os.replace(tmp, path)` 跨平台原子替换，写入失败时 tmp 不影响主文件。好实践。

### 🟢 S5：分享令牌用 secrets ✅

`generate_share_id()` 用 `secrets.token_urlsafe(16)`，256-bit 熵，不可预测。好实践。

***

## 四、性能审查

| #  | 严重   | 位置                                    | 问题                                                                         | 影响                                   | 建议                                                             |
| -- | ---- | ------------------------------------- | -------------------------------------------------------------------------- | ------------------------------------ | -------------------------------------------------------------- |
| P6 | 🟡 中 | chat.py `_build_job_analysis_context` | 每次 chat\_stream 应聘助手请求都同步读 archive.list\_reports + archive.get\_report × 2 | 应聘助手对话延迟额外增加 \~100-300ms（磁盘 IO）      | 用 `asyncio.to_thread` 包装 + 结果缓存（比如 Redis 或 in-memory LRU 1min） |
| P7 | 🟢 低 | profile\_storage.py                   | get/save 每次 to\_thread，但 ChatRequest 高频时会频繁切换线程                            | 可接受；极端高 QPS 时加 LRU 内存缓存              | 加 1s TTL 的内存缓存                                                 |
| P8 | 🟢 低 | rebuild\_chroma\_vectors.py           | 重嵌入阶段用同步 `embed_fn([docs])`                                                | 大库（5000+ 条）时 embedding 会是纯 Python 串行 | 用 `asyncio.to_thread` + 并发批次（比如 `asyncio.gather` 多个 batch）     |

***

## 五、前端增量变更发现

### Chat.tsx（新增 \~130 行）

**亮点**：

- Drawer 右侧历史导航、HistoryOutlined 图标

- SKILLS 数组硬编码在 Chat.tsx 顶部，三档切换（通用/应聘/资讯）

- pendingQueue.length 显示在输入框下方 ✅

- 停止按钮会同时 clearQueue ✅

- isStreaming 时输入框 placeholder 动态变 ✅

**问题**：

| #  | 严重   | 位置            | 问题                                                                                                                         | 建议                                                                                                                                        | <br />                 |
| -- | ---- | ------------- | -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | :--------------------- |
| F1 | 🟡 中 | Chat.tsx      | SKILLS 硬编码在前端，没有从后端 config 读取                                                                                              | 加一个 `GET /api/v1/config/skills` 动态获取                                                                                                      | <br />                 |
| F2 | 🟢 低 | Chat.tsx      | Drawer 用 `historyOpen` state + `onClick` 触发条，但关闭时没清空列表                                                                     | 关闭 Drawer 不影响数据，OK。但每次重新打开要重新渲染                                                                                                           | <br />                 |
| F3 | 🟢 低 | chat.ts store | `flushQueue` 用 `void get().sendMessage(next.content, next.skill)` 触发，set 同步更新后立即调 sendMessage                              | OK，但逻辑较绕。用 `flushQueue: () => Promise<void>` 明确返回类型                                                                                       | <br />                 |
| F4 | 🟡 中 | chat.ts store | `cancelStream` 时调 `(window as any).__stream_controller.abort()` + `clearQueue()` + abort 后 error callback 也可能触发 flushQueue | cancel 后 error 会再 flushQueue，但队列已清空，第二次 flushQueue 是空操作 OK。但 error callback 会再 set isStreaming=false（已 cancel 时本来就是 false），多次 setState 空转 | 加 `_cancelled` flag 保护 |

***

## 六、改进后的架构图（增量）

```
                    ┌──────────────┐
  GET/PUT /profile →│ ProfileStorage│ ← chat.py 反馈闭环写入
                    └──────┬───────┘
                           ↓
                    用户画像（JSON 持久化）
                    ╱          ╲
                   ╱            ╲
    job/profile.py            chat.py（技能上下文注入）
    （职位分析注入画像）        （应聘助手 / 资讯助手读取画像）
                                 ↓
                    ┌────────────────────────┐
                    │   chat.py _run_chat    │
                    │  ┌───────────────────┐ │
                    │  │ skill 参数 →      │ │
                    │  │  SystemMessage    │ │ ← Q1 修复后
                    │  │  (不污染 user_input)│
                    │  └───────────────────┘ │
                    │       ↓                │
                    │  LangGraph Worker      │
                    │       ↓                │
                    │  主 LLM（reasoner）    │
                    │       ↓                │
                    │  done → 反馈闭环 A/B   │
                    │   A: <PREF> 标签提取    │
                    │   B: 后台记录员 LLM    │ ← asyncio.create_task
                    └────────────────────────┘

  ChromaDB 加固（独立链路）：
  backup_kb.sh（每日 3:30）→ tar 数据卷（停 backend）→ 14 天轮转
  rebuild_chroma_vectors.py（恢复脚本）→ 读全部文档 → 备份 JSON → 重嵌入 → 删旧 → 重建
```

***

## 七、修复优先级建议

| 优先级       | 编号         | 修复项                                                        | 工作量      | 理由                         |
| --------- | ---------- | ---------------------------------------------------------- | -------- | -------------------------- |
| **🔴 P0** | Q1         | Skill 上下文改用 SystemMessage 注入                               | S（1h）    | 会污染对话历史、Supervisor 意图识别会混乱 |
| **🔴 P0** | S1         | ProfileStorage / ShareStorage 路径安全白名单                      | S（30min） | 路径穿越安全风险                   |
| **🟡 P1** | P6         | `_build_job_analysis_context` 用 asyncio.to\_thread 包装 + 缓存 | S        | 应聘助手对话额外 \~200ms 磁盘 IO     |
| **🟡 P1** | Q3         | 后台记录员 LLM 加触发条件                                            | S        | 每轮额外一次 LLM 调用，成本翻倍         |
| **🟡 P1** | Q4         | `<PREF>` 标签解析健壮化                                           | S        | LLM 输出格式不稳定                |
| **🟡 P1** | F4         | cancelStream / flushQueue 协同加 flag                         | S        | stop 后 error callback 空转   |
| **🟢 P2** | P1/P2      | ProfileStorage 异常处理细化                                      | XS       | 可观测性                       |
| **🟢 P2** | P4/P5      | ProfileStorage 走 AppContext + 消除重复函数                       | S        | 代码复用                       |
| **🟢 P2** | Q7/Q8      | JSON 解析函数三处重复 → 抽公共模块                                      | S        | 维护性                        |
| **🟢 P2** | Q9         | pendingQueue 加 MAX\_QUEUE\_SIZE                            | XS       | 边界保护                       |
| **🟢 P2** | chat.py 拆分 | 技能逻辑拆出独立文件                                                 | M（2h）    | chat.py 已 950 行，继续膨胀不可维护   |

***

## 八、存量架构建议延续（来自上次审计）

上次审计的核心架构问题在本次增量中**仍未解决**：

| 上次审计编号 | 问题                             | 本次增量是否缓解                          |
| ------ | ------------------------------ | --------------------------------- |
| **A1** | Executor 顺序执行忽略 depends\_on    | ❌ 未缓解                             |
| **A2** | SSE 是伪流式（ainvoke 跑完才逐字符推）      | ❌ 未缓解；本次新增的对话排队**加剧了** SSE 结束延迟感知 |
| **A3** | L1 记忆进程内 dict，多 worker 丢会话     | ❌ 未缓解                             |
| **A4** | 反思策略 Adaptive/Sampling 未实现     | ❌ 未缓解                             |
| **C1** | Executor 拓扑排序 + asyncio.gather | ❌ 未缓解                             |

**备注**：对话排队的 pendingQueue 在 SSE 模式下有个微妙问题——用户看到"回复进行中...已排队 2 条"，但实际上 SSE 本身还要等 LangGraph 完整跑完才推送 token。排队期间的"回复进行中"提示可能是上一轮的状态。这个 UX 细节值得关注。

***

*增量审计完成。本次审计只读，未引入任何环境变更或修改源码。*
