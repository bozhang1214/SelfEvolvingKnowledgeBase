---
title: T5 工具与集成清单
status: fact-sheet
scope: backend/app/tools + agents/executor + config.yaml tools 段 + news/job 进程内集成
---

# T5 工具与集成清单

> 只描述现状（as-is）。证据格式 `backend/文件:行号`；非代码直接读出者标【推断·待验证】；密钥一律 `<REDACTED>`。
> 读取范围：`tools/registry.py`、`tools/direct/vector_store.py`、`tools/rag/retriever.py`、`tools/mcp/*`、`tools/file_processor.py`、`tools/image_processor.py`、`agents/executor.py`、`graph/builder.py`(RAG 节点)、`config.yaml`。

## 1. 总览与数量

| 类别 | 名称 | 数量 |
|---|---|---|
| ToolRegistry 托管工具（MCP Server 工具，转为 LangChain Tool） | web_search | 1 |
| Executor 可派发工具（按 `step.tool` 分发） | web_search / rag_retrieve / llm_generate（未知工具兜底 llm_generate） | 3 |
| 直连/本地处理组件（非 registry 工具，被 Graph/上传链路直接调用） | DirectVectorStore、RAGRetriever、FileProcessor、ImageProcessor | 4 |
| 外部 HTTP/进程依赖 | 博查搜索 API、自建 RSSHub + 外站 RSS/网页源、猎聘/大厂招聘/Mokahr API、sekb-browser 服务(BOSS)、视觉多模态 LLM(OpenAI 兼容)、HF embedding 模型下载 | 见各节 |

ToolRegistry 仅管理 1 个工具（web_search），`tools/registry.py:1-14` 注释明确说明文件系统/向量库/文档解析“后续阶段接入”，当前未注册为 MCP 工具（registry.py:7）；向量检索走进程内直连（`tools/direct/vector_store.py:1-18` 设计说明：MCP 序列化 ~40ms 开销 vs 本地 <10ms）。

## 2. 工具明细

### 2.1 web_search（博查搜索）

| 字段 | 现状（证据） |
|---|---|
| 类型 | MCP 工具（stdio 子进程）→ 内部 HTTP POST 博查 API；MCP 不可用时降级为进程内直接封装（仍调同一 HTTP API） |
| 工具名 | `web_search`，常量 registry.py:53；与 MCP Server 注册名一致（bocha_server.py:301-307） |
| 注册/生命周期 | `ToolRegistry.initialize()` registry.py:99-143：provider==bocha → `_init_bocha_mcp()`（registry.py:124-133）；子进程命令 `python -m app.tools.mcp.bocha_server`（registry.py:150），env 传 BOCHA_API_KEY/ENDPOINT/MAX_RESULTS/TIMEOUT_SECONDS/MAX_RETRIES（registry.py:153-159）；`shutdown()` 断开并复位（registry.py:197-214） |
| 入参 | `{query: str(必填), count: int 默认5, min1, max20}`（bocha_server.py:44-60）；HTTP payload `{query, count, summary: true}`（bocha_server.py:134-138） |
| 出参 | `[{title, url, snippet, source:"bocha"}]`（bocha_server.py:19,243-248）；非标准结构兼容解析 data.webPages.value / webPages.value / data / value（bocha_server.py:219-231）；MCP 返回 TextContent(JSON 字符串)（bocha_server.py:342），client 单条文本自动 json.loads（client.py:305-308） |
| 超时 | `timeout_seconds` 配置默认 10s（config.yaml:208、bocha_server.py:97/416），httpx.AsyncClient(timeout=...)（bocha_server.py:144） |
| 重试 | `max_retries` 默认 2（config.yaml:209），指数退避 `asyncio.sleep(2**attempt)` 1s/2s（bocha_server.py:140-188）；HTTP ≥400 视为 ToolError **不重试直接抛**（bocha_server.py:148-152,174-176）；重试耗尽抛 ToolError（bocha_server.py:191-194） |
| 失败降级 | ① MCP 连接失败 → `_init_direct_fallback()` 直接封装博查 API（registry.py:127-133,185-195）；② provider≠bocha → 直接封装（registry.py:134-137）；③ langchain_core 未装且需兜底 → ToolError（registry.py:187-191）；④ 调用侧：Executor 捕获任何异常转 ToolError、步骤记 FAILED 继续执行后续步骤（executor.py:106-134,227-231） |
| 外部依赖 | 博查 AI 搜索 API `https://api.bochaai.com/v1/web-search`（config.yaml:206），key `${BOCHA_API_KEY}`（config.yaml:205，脱敏 `<REDACTED>`）；依赖 mcp / langchain-mcp-adapters / langchain_core / httpx 库 |
| 配额/限流 | 代码内无配额统计；单次 count 上限 20（schema bocha_server.py:55-56）；API 层限流已启用（`api.rate_limit.enabled: true`，ASGI 分组 + nginx 双闸） |
| 健康检查 | MCP 模式 `session.send_ping()`（registry.py:277、client.py:320-334）；直接封装模式仅判 tool 非空（registry.py:272-274） |

### 2.2 rag_retrieve（知识库检索）

| 字段 | 现状（证据） |
|---|---|
| 类型 | “Executor 名义工具”，实际检索发生在 Graph `rag_retrieval` 节点（Supervisor→Planner 之间），Executor 只引用预检索结果（builder.py:194-241,306-334） |
| 检索入口 | `RAGRetriever.retrieve_for_query(query, user_id, intent)`（retriever.py:119-211），由 `rag_retrieval_node` 用 vector_store + config 构造（builder.py:214-221） |
| 入参 | query、user_id（隔离维度）、intent（字符串，与 GraphState.IntentType 同值，retriever.py:37-45） |
| 检索参数 | top_k=config.memory.l3_knowledge.retrieval_top_k(=5)（builder.py:217、config.yaml:115）；min_score 默认 0.3（未显式传入，走 retriever 默认 retriever.py:107）；importance_threshold=config(0.3)（builder.py:218、config.yaml:116），仅当 >0 且检索后有结果时按 importance 过滤（retriever.py:187-191） |
| 意图路由 | kb_strict/kb_prefer：top_k=5、min_score=0.3（retriever.py:159-166）；web_default/task_plan（辅助模式）：top_k=max(2, top_k//2)、min_score=min(0.9, 0.3+0.15)（retriever.py:167-170,106-117）；chitchat/chat_simple/未识别意图：跳过 RAG（retriever.py:137-143,171-174） |
| 出参 | `RAGResult{mode: strict/prefer/auxiliary/disabled, context: list[dict]|None, fallback_message, retrieval_count, latency_ms}`（retriever.py:58-74）；context 每项来自 DirectVectorStore.search：{content,score,source,source_id,importance,entry_id,topic}（vector_store.py:86-94） |
| 降级 | vector_store=None（L3 未启用）→ mode=disabled + “知识库未启用”（retriever.py:146-156）；节点异常 → 空结果 + rag_fallback_message 标记（builder.py:235-241）；strict/prefer 无结果时生成 fallback 文案注入 Prompt（retriever.py:213-223）；Executor 中 rag_retrieve 步骤仅回显“知识库已检索到 N 条/暂无相关信息”，知识文本由 RAG 节点直接注入 EXECUTOR_PROMPT {rag_context} 段，避免重复出现（executor.py:233-244,155-158） |
| 超时/重试 | 无显式超时/重试（ChromaDB 同步 IO 经 asyncio.to_thread，knowledge_base.py:293-299）；每步耗时 latency_ms 记日志（retriever.py:177-184） |
| 外部依赖 | ChromaDB PersistentClient + 本地 embedding 模型（见 T6 知识库节） |

### 2.3 llm_generate（LLM 直答，Executor 内建）

| 字段 | 现状（证据） |
|---|---|
| 类型 | LLM（非外部工具；LangChain ChatModel 调用） |
| 调用点 | executor.py:246-248 → `_llm_generate()` executor.py:259-283：`llm_factory.ainvoke_with_stats("executor", [HumanMessage])`（executor.py:272-275，走统一统计入口）；未知工具名兜底为 llm_generate（executor.py:250-257） |
| 入/出参 | 入参 query 字符串（tool_input.query 或 user_input 兜底 executor.py:247/256）；出参响应文本（response.content 或 str） |
| 失败降级 | 抛 ToolError → 步骤 FAILED 记录并继续后续步骤（executor.py:121-134）；草稿生成失败时以工具结果或 user_input 兜底（executor.py:363-367,375-381） |
| 模型/超时 | 角色 executor 对应 deepseek-chat、temperature 0.3、max_tokens 2000（config.yaml:37-40）；LLM 全局 timeout_seconds 60 / max_retries 2 / 退避 [1,2]（config.yaml:19-24）；详见 T3 表 |

### 2.4 DirectVectorStore（进程内直连，非 registry 工具）

| 字段 | 现状（证据） |
|---|---|
| 类型 | 直连（Python 直接调 ChromaKnowledgeBase，绕过 MCP；vector_store.py:1-18） |
| 入/出参 | `search(query,user_id="default",top_k=5,min_score=0.3,category_l1/2/3=None)` → 字典列表（vector_store.py:45-105）；`add(content,user_id,source="conversation",source_id,importance_score=0.5,topic,category_l1="其他",category_l2="待分类",category_l3="未分类",category_confidence,0,category_source="auto",series)` → entry_id（vector_store.py:107-158）；`count(user_id=None)`（vector_store.py:160-162） |
| 降级/超时 | 无网络与超时（本地）；embedding 同步调用以 to_thread 包裹（knowledge_base.py:170-172）；Chromadb 依赖缺失时抛 ImportError（knowledge_base.py:161-164） |
| 外部依赖 | ChromaDB、embedding 函数（默认本地 sentence-transformers `BAAI/bge-small-zh-v1.5` 512 维，embedding.py:47,53；哈希降级 256 维仅 allow_hash_fallback=true 时 embedding.py:105-118；生产配置 false config.yaml:117） |

### 2.5 RAGRetriever（策略层，见 2.2）；2.6 见 FileProcessor/ImageProcessor

### 2.7 FileProcessor + chunk_text（文档解析/分块，上传链路工具）

| 字段 | 现状（证据） |
|---|---|
| 类型 | 进程内处理组件（非 registry 工具） |
| 支持格式 | .txt/.md/.markdown/.pdf/.docx + 图片(.jpg/.jpeg/.png/.webp/.bmp/.gif/.tiff)（file_processor.py:52-54,38-39） |
| 入/出参 | `parse_file(path)` → 纯文本（file_processor.py:229-284）；`process_file(path,user_id,metadata)` → `ProcessResult{file_path,file_name,file_size,content_length,chunks,chunks_count,status,error}`（file_processor.py:62-73,286-356）；`chunk_text(text,chunk_size=500,overlap=50)`（file_processor.py:81-138） |
| 分块策略 | 段落(\n\n)→句子(中英文句末标点)→按 chunk_size 硬切，相邻块保留 overlap 字符重叠（file_processor.py:85-106,141-173）；非法参数抛 ValueError |
| 错误处理 | 任何阶段异常封装为 status="error" 的 ProcessResult，不外抛（file_processor.py:339-356）；图片走 `_process_image_file` → ImageProcessor（file_processor.py:415-497）；parse_file 对图片抛“请走 process_file”提示（file_processor.py:267-271） |
| 外部依赖 | pypdf（.pdf）、python-docx（.docx）懒导入，未装抛友好 ImportError（file_processor.py:379-405）；同步 IO 经 asyncio.to_thread（file_processor.py:277） |
| 配额/限制 | 无；上传侧单文件上限 50MB（upload.py:58），图片上限 20MB（image_processor.py:42） |

### 2.8 ImageProcessor（OCR + 多模态打标签，上传链路工具）

| 字段 | 现状（证据） |
|---|---|
| 类型 | 进程内处理组件（OCR 本地 + 多模态 LLM HTTP） |
| 入/出参 | `process_image(path,user_id,save_dir=None)` → `ImageProcessResult{file_path,file_name,file_size,ocr_text,tags,description,ocr_text_path,status,error}`（image_processor.py:45-57,86-174） |
| OCR | PaddleOCR 懒加载（image_processor.py:235-264），同步引擎在线程执行（to_thread，image_processor.py:196/199），兼容 3.x predict / 2.x ocr；lang 默认 ch（config.yaml:234）；OCR 未装/失败 → 空串降级不报错（image_processor.py:188-190,228-233） |
| 视觉 LLM | ChatOpenAI(base_url, api_key, model 默认 qwen-vl-plus, max_tokens=500, temperature=0.3, **timeout=30**)，base64 data-URL 多模态消息（image_processor.py:306-337）；`vision_llm.base_url/api_key/model` 由 config.yaml:227-230（env 可覆盖，默认 dashscope 通义）提供；未配 api_key 或模型调用失败 → 按 OCR 关键词规则打简单标签（image_processor.py:270-286,345-383） |
| 出参规约 | JSON 响应 {tags(3-5 个≤4字), description}，容忍 markdown fence 包裹（image_processor.py:318-325,399-419） |
| 保存 | OCR 文本+标签落盘 `save_dir/{原图stem}_ocr.txt`（image_processor.py:421-454），默认 save_dir="data/uploads/images"（file_processor.py:213）；上传路由将原图存 `data/uploads/images/{user_id}/`（upload.py:127,134） |
| 失败降级 | 文件不存在/超 20MB → status=error；处理链路异常 → status=error（image_processor.py:103-125,160-174）；视觉失败不阻断（回退文本标签） |
| 外部依赖 | PaddleOCR（paddleocr 库，本地模型）、OpenAI 兼容视觉 LLM（默认 dashscope qwen-vl-plus，key `<REDACTED>` 环境变量注入） |
| 配额/限流 | 无显式限流；单请求 timeout=30s 无重试【推断】 |

## 3. 非注册集成（news / job 采集链路，进程内 HTTP 集成）

| 集成 | 说明（证据） | 超时/重试/降级 | 外部依赖 |
|---|---|---|---|
| RSS 源采集 RSSFetcher | config 15+ RSS 源，含 6 个掘金路由走**自建 RSSHub** `http://rsshub:1200`（config.yaml:306-325）；feedparser 解析（rss_fetcher.py:86），单源最多 100 条（rss_fetcher.py:23,89），UA 头伪装（rss_fetcher.py:25-28） | timeout=15s（rss_fetcher.py:47,77）；并发 gather return_exceptions，单源失败记日志跳过（rss_fetcher.py:57-67） | feedparser/httpx；RSSHub 容器（docker-compose.prod.yml:147-166，内部网络 http://rsshub:1200） |
| 网页源采集 WebFetcher | CSDN 热榜 GET + 魔搭 ModelScope PUT，公开 JSON 接口免 token（web_fetcher.py:1-15）；每源 ≤25 条 | timeout=15s；单源失败跳过（web_fetcher.py:47-58） | httpx；外站接口（无 token） |
| 原文正文抽取 fetch_article_text | httpx 抓 HTML → trafilatura 抽取正文（content_extractor.py:1-20）；并发 10 信号量，正文截 1200 字符（service.py:25-27,214-231） | 每 URL timeout=8s（content_extractor.py:23）；任何失败返回空串、调用方用 RSS 摘要兜底（content_extractor.py:10-12,31-34） | httpx、trafilatura |
| 猎聘免登录采集 LiepinJobFetcher | 两步：GET 首页拿 Cookie(XSRF-TOKEN/acw_tc/__gc_id) → 带 Cookie+X-Xsrf-Token+X-Fscp-* 头 POST 搜索接口（fetcher.py:4-9,48-66）；requests.Session 线程池（fetcher.py:9,119-120,160-166） | 见 fetcher.py 实现（同步 requests） | 猎聘 liepin.com 接口（fetcher.py:31-32） |
| 大厂招聘源 + Mokahr | 字节/腾讯/百度/小米/阿里/小红书官方招聘 API 各一个源类 + Mokahr 通用源（sources.py:92/157/200/248/309/374,486-487），AES 解密 Mokahr（sources.py:446-468） | 每源独立 try，失败源降级为空列表（collector.py:10-14） | 各外站接口 |
| BOSS 直聘（BrowserSource） | 调 sekb-browser 浏览器服务 `http://browser:1300`（collector.py:20-33），需要先在浏览器服务导入 Cookie/扫码登录，未登录返回空 | 容器健康检查 :1300/health（docker-compose.prod.yml:189-194） | sekb-browser 服务（Playwright+Chromium，数据卷 sekb_browser_data） |
| 日报/周报/月报生成 | LLM 角色 news_report（config.yaml:60-64）逐大类 JSON 生成（news/generator.py:132-179）；非本表工具，存储细节见 T6 | — | DeepSeek LLM（prompt 文件在仓库根 prompt/，generator.py:80-126） |

## 4. Executor 工具调度行为汇总

- 逐步骤顺序执行 task_steps；依赖仅记录不排序（executor.py:87-88）；每步维护 ToolCallRecord{tool_name,input,output,success,latency_ms,error}（executor.py:97-137）；步骤失败不中断后续步骤（executor.py:121-134）。
- 三个可派发工具 + 未知兜底 llm_generate（executor.py:219-257）。
- 草稿生成走 EXECUTOR_PROMPT；若全局 token sink 存在则 astream_with_stats 流式逐 token 回传（executor.py:350-367）。
- 工具结果拼接 execution_context 全部进 Prompt（executor.py:140）。

## 5. 配额/限流/异常发现（现状事实）

- 工具层无任何代码内配额/限流/熔断计数；只有单次 count 上限（web_search 20）与重试/超时参数。
- API 限流已启用（`enabled: true`，2026-09-10 起；ASGI 分组 + nginx 双闸）。
- 发现：ImageProcessor 直接 `langchain_openai.ChatOpenAI`（image_processor.py:293,306）不走 llm_factory 统一统计/降级入口；news/job 采集各链路也不经过 ToolRegistry。
- 发现：config.yaml 仅出现 1 个 MCP 化工具（web_search），filesystem 工具 enabled:false（config.yaml:211-213），registry 注释声称后续接入，代码中无对应实现。
