---
title: T6 存储与数据文件清单
status: fact-sheet
scope: backend 全部持久化（ChromaDB/JSON/记忆/缓存/上传）+ 数据目录与备份
---

# T6 存储与数据文件清单

> 只描述现状（as-is）。证据 `backend/文件:行号` 或 `docker-compose.prod.yml:行号`/`scripts/backup_kb.sh:行号`；推断标【推断·待验证】；密钥 `<REDACTED>`。

## 0. 数据根目录与生产挂载

- 生产：backend 容器 WORKDIR `/app`（backend/Dockerfile:34），全部相对 `data/...` 路径解析到容器 `/app/data`；Docker 卷 `sekb_data:/app/data`（docker-compose.prod.yml:75）。本地开发相对 backend/ 目录解析（config.yaml 与代码同目录）。
- 卷内另含 HF 模型缓存 `/app/data/hf_cache`（docker-compose.prod.yml:69，HF_HOME）。浏览器服务 Cookie/登录态在独立卷 `sekb_browser_data`（docker-compose.prod.yml:262）。
- 备份：`scripts/backup_kb.sh` 停止 backend → 打包整个 `sekb_data` 卷（含 chroma_db、uploads/documents、uploaded_md5.json、shares.json、conversations、news 等，backup_kb.sh:8-9,36-43）→ 重启（trap EXIT 兜底确保启动，backup_kb.sh:32-34）→ 保留 `RETENTION_DAYS`(默认14) 天（backup_kb.sh:24,48-49）。
- 并发前提：生产 uvicorn `--workers 1` 单写进程，避免多进程共享 ChromaDB/SQLite 目录并发写损坏、指标失真、scheduler 重复执行（backend/Dockerfile:129-131 注释，CON-01/OPS-01/CON-03）。

## 1. L3 长期知识库（ChromaDB）

| 项 | 现状（证据） |
|---|---|
| 位置/集合 | 目录 `data/chroma_db`（config.tools.vector_store.persist_path，config.yaml:218）；`chromadb.PersistentClient` 本地持久化（knowledge_base.py:147）；集合名 `knowledge`（默认 knowledge_base.py:116），`get_or_create_collection(name, metadata={"hnsw:space":"cosine"})`（knowledge_base.py:148-151） |
| 向量维度 | 默认 embedding 模型 `BAAI/bge-small-zh-v1.5`，**512 维**、normalize（embedding.py:47,53,103）；哈希降级 256 维，仅 `allow_hash_fallback=true`（embedding.py:105-118）；生产配置 false（config.yaml:117） |
| 条目 schema | `KnowledgeEntry`（knowledge_entry.py:35-82）：entry_id(uuid4 str)、content、source(conversation/document/manual)、source_id、user_id、topic、importance_score(默认0.5)、version(默认1)、supersedes、metadata、created_at/updated_at/last_accessed_at、access_count、category_l1/l2/l3(默认 其他/待分类/未分类)、category_confidence、category_source(auto/manual)、series。落库 metadata = `to_chroma_metadata()`（knowledge_entry.py:84-109，16 个标量字段，仅支持 str/int/float/bool）；相似度=1-cosine distance（knowledge_base.py:317） |
| 检索参数 | `retrieve(query,user_id,top_k=5,min_score=0.3,category_l1/2/3)`（knowledge_base.py:250-335）；where 过滤 user_id+分类多字段 `$and`（knowledge_base.py:72-101）；RAG 节点实际传 top_k=5（config.yaml:115）；同维度另提供 find_similar/update/update_metadata(+batch)/delete(_batch)/get/count/list_entries/count_entries（knowledge_base.py:337-609） |
| 写入方 | KnowledgeIngester 对话入库 `knowledge_base.add()`：knowledge_ingestor.py:323,398,431（由 chat 路由 chat.py:591 / CLI cli/chat.py:276 触发）；文件/图片上传 `vector_store.add()`（upload.py:258）；批量删除 upload.py:421、分类重打标 update_metadata upload.py:862（knowledge.py 路由删除 knowledge.py:211、人工重分类 knowledge.py:231-290） |
| 读取方 | RAG 节点（builder.py:194-241 → DirectVectorStore.search → kb.retrieve）；知识库列表/统计/检索/删除路由 knowledge.py:133-144,162-186；分享问答/浏览经 kb.retrieve（share 链路）【推断·待验证：具体调用行在 share 路由，本次未展开】 |
| 隔离维度 | 单集合内按 metadata `user_id` where 过滤（knowledge_base.py:86-87,284-289）；`user_id=None` 不过滤（跨用户读，仅知识库管理用途） |
| 并发/原子性 | 无应用层锁；全部同步 ChromaDB 调用经 `asyncio.to_thread`（knowledge_base.py:199-207,293-299）；靠生产单 worker 规避并发写（Dockerfile:129-131）；ChromaDB PersistentClient 自带落盘，无显式 close（bootstrap.py:296-299） |
| 增长/清理 | **无自动清理代码**。config 存在 eviction 参数 `max_items:10000 / stale_days:7 / similarity_merge_threshold:0.95`（config.yaml:118-121、config.py:102-104）但 app 代码零引用 → 【推断·待验证】死配置/未实现；`rebuild_chroma_vectors.py` 脚本在 scripts/（重建向量用途，非自动） |
| 备份 | 随 sekb_data 卷整卷备份（backup_kb.sh:43）；备份注释称 uploads/documents 为“L1 兜底：ChromaDB 全毁时用原始文件重灌”（upload.py:68-69） |

## 2. JSON 存储（会话/消息）— JSONStorage

| 项 | 现状（证据） |
|---|---|
| 布局 | `data/index.json`（全部会话元信息单文件）；`data/conversations/{conv_id}.json`（每会话消息数组）（json_storage.py:5-10,43-49；config.yaml:243-244） |
| index schema | `ConversationMeta`：conv_id、user_id、title、status(active/archived/deleted)、pinned、created_at、updated_at、message_count、total_input_tokens、total_output_tokens、total_cost_usd（base.py:38-55） |
| 消息 schema | `MessageRecord`：msg_id、conv_id、role(user/assistant/system)、content、intent?、tokens_input、tokens_output、latency_ms、trace_id?、created_at（base.py:58-74）；追加消息时由 storage 补全 msg_id/conv_id/created_at 并累加 index 统计（json_storage.py:343-398） |
| 写入方 | chat 路由 create/append（chat.py:445,560,576）；conversations 路由 create/反馈 append（conversations.py:122,299）；会话管理(update/delete/status)同路由【推断·待验证：update/delete 调用行未逐一核对】 |
| 读取方 | list/get_messages（conversations.py:103,153,182,280）；健康检查 health.py:63（`__health_check__` 用户）；chat 路由历史读取 chat.py:460 |
| 隔离维度 | list 按 user_id 过滤 + status!=deleted（json_storage.py:283-287）；Phase 1 注释“user_id 固定 default”（base.py:46）→ Phase 3 后按真实 user_id |
| 锁/原子性 | 进程内 `asyncio.Lock` 保护 index.json 读-改-写（json_storage.py:77,237-242,301-312,356-386,424-435）；文件写一律 **临时文件+os.replace 原子重命名**（json_storage.py:122-141,172-191）；IO 全部 asyncio.to_thread。局限：锁是单进程内协程锁，多进程/多实例下无效【推断·待验证】 |
| 删除 | 软删除（index status=deleted）+ 物理删消息文件（json_storage.py:318-341）；索引中 deleted 记录永不 purge |
| 增长/清理 | 无自动清理/压缩；删除只标状态不删记录 |

## 3. L1 短期记忆（内存，非持久化）

| 项 | 现状（证据） |
|---|---|
| 实现 | 进程内存 dict：`_messages: conv_id→list[BaseMessage]`、`_summaries: conv_id→list[str]`、`_conv_users: conv_id→user_id`（short_term.py:78-82） |
| 行为 | 滑动窗口保留最近 max_turns×2 条（short_term.py:110-112）；超 max_tokens(3000) 触发 LLM 压缩，保留 max_compressed_summaries=3 条摘要，超限合并最早两条（short_term.py:168-234）；get_context 拼“摘要+近期消息”至 max_tokens（short_term.py:130-166）；压缩用 LLM 角色默认 scribe（short_term.py:57,306-309）；无 LLM 时降级文本截断（short_term.py:295-298） |
| 持久化 | **无**——进程重启即清空（内存 dict）；生产者：graph 各节点 add_message/compress_if_needed 调用方（chat 路由等）【推断·待验证：逐调用行未全列】 |
| 配置注记 | hard_token_limit=4000 仅用于构造日志与校验 max_tokens<hard（short_term.py:94-95、config.py:134-137），压缩/截断判断未引用它【推断·待验证：可能为死配置】；l2_session(Redis) enabled:false（config.yaml:107-111），Docker 中 redis 属 with-db profile 未启用（docker-compose.prod.yml:199-241） |
| 并发 | 单事件循环内共享 dict，无锁；无跨进程一致性（单 worker 设计下可接受） |

## 4. News 存储（日报/周报/月报）

| 项 | 现状（证据） |
|---|---|
| 目录 | `data/news`（config.yaml:299） |
| 文件 | 日报：`daily_{YYYY-MM-DD}.md` + `daily_{YYYY-MM-DD}.json`（结构化副本，供周/月报聚合）（storage.py:29-39）；周报 `weekly_{期}.md`、月报 `monthly_{期}.md`（仅 md，storage.py:71-78）；索引 `index.json` |
| index schema | [{date, total_count, headline(截100字), path, created_at}]（storage.py:101-115） |
| 报告 schema | report={headline:{title,source,importance,abstract,analysis,attention,link}, sections:[{category,summary,keywords,items:[{title,source,importance,abstract/one_liner,attention/why_matters,link}]}], comprehensive:{correlation,forecast}, total_count}（storage.py:166-236 渲染字段、generator.py:132-179 产出） |
| 写入方 | NewsService `save_daily`（service.py:142）/ `save_periodic`（service.py:146）；由 API/news 路由手动刷新或 scheduler cron（config daily 08:00 等，config.yaml:301-304）触发 service.refresh（service.py:52-76） |
| 读取方 | api/routes/news.py:54,65,69-72,108（list_reports/read_report/read_periodic）；结构化 JSON 供周/月报聚合 storage.py:59-67 |
| 保留策略 | `retention_days: 70`（config.yaml:300、storage.py:131-144）——`_cleanup()` 仅删超期 `daily_*.md`（storage.py:136-144） |
| 隔离 | 全局单用户共享，无 user_id 维度 |
| 锁/原子性 | 生成侧跨进程文件锁 `.lock_daily`（O_CREAT|O_EXCL + 陈旧锁超时 600s 兜底，service.py:78-97），幂等跳过：daily_exists 双重检查（service.py:60-74）；但 **storage 写文件非原子**（直接 write_text，无临时文件替换）：save_daily storage.py:32-35、index storage.py:126-129 |
| 发现的问题点 | ① `_cleanup()` 只清 `daily_*.md`，同日期 `daily_*.json` 与 index.json 过期项不被清理 → json 残留【推断·待验证：长期累积】；② index.json 无锁非原子，并发 save 可能丢项/半写；③ 周报/月报无保留期清理 |

## 5. Profile 用户画像存储

| 项 | 现状（证据） |
|---|---|
| 路径 | `data/profile/{user_id}.json`，一用户一文件（profile_storage.py:1-6）；路径对 / \\ 转义兜底（profile_storage.py:28-30） |
| schema | `UserProfile`：user_id、bio、skills[]、career_goal、job_preferences{target_roles,target_cities,min_salary_k,company_types,keywords}、news_interests[]、updated_at（models/profile.py:35-44） |
| 写入方 | chat 偏好回流 upsert_update（chat.py:297,347）；profile 路由 upsert_update（profile.py:51）；job/profile.py:114 读取（应聘助手） |
| 读取方 | chat.py:226 读画像；profile 路由；job/profile.py:114 |
| 原子性 | 写 = 临时文件 + os.replace（profile_storage.py:42-50）；**无锁**——upsert_update 是“读-改-写”（profile_storage.py:58-75），并发会丢更新；嵌套 job_preferences 浅合并（profile_storage.py:64-71） |
| 隔离 | 按 user_id 文件隔离 |

## 6. Job 分析存档与缓存

| 项 | 现状（证据） |
|---|---|
| 报告目录 | `data/job_reports/`（archive.py:15）：`{rid}.md` + `{rid}.json`（结构化，archive.py:133-151），rid=uuid4 hex 12（archive.py:136）；索引 `index.json`（archive.py:16） |
| index schema | [{id, user_id, type(single/batch), title, created_at}]（archive.py:143-149），**上限 200 条**截断（_MAX_REPORTS，archive.py:17,150） |
| 写入方 | routes/job.py:78(single)、228(batch) 调 save_report；删除/列表 archive.py:154-207 |
| 隔离 | 按 user_id 过滤读写（list/get/delete 均校验 user_id，archive.py:154-206） |
| 原子性/锁 | **无锁、非原子**：`_save_index` 直接 write_text（archive.py:128-130）；索引截断到 200 但**磁盘 .md/.json 不删** → 物理文件无限累积【推断·待验证】；读 index 失败返回空（archive.py:119-125） |
| 缓存1 | `data/job_analysis_cache.json`：key=`user_id:md5(jd前3000字)`，value={ts, result}，TTL 14 天（analysis_cache.py:16-22,39-53） |
| 缓存2 | `data/job_cache.json`：key=`user_id|keyword|city|min_salary_k`，value={ts, jobs≤1000 条}，TTL 14 天（job_cache.py:15-21,38-52）；get_latest_cached 供前端默认回填（job_cache.py:55-92） |
| 缓存写入方 | routes/job.py:71(save_analysis)、129/350(save_cached_jobs) |
| 缓存原子性 | **无锁、非原子 write_text 全文件覆盖**（analysis_cache.py:34-36、job_cache.py:33-35）；整文件 RMW，并发写互相覆盖（单 worker 下缓解） |

## 7. 用户与分享存储（Phase 3 鉴权/分享）

| 项 | 现状（证据） |
|---|---|
| 用户 | `data/users.json`：dict{user_id → User{user_id,email,password_hash,name,avatar_url,created_at,updated_at,is_active,settings}}（user_storage.py:14-34、models/user.py:8-29）；写 = **整文件直接写，非原子无锁**（user_storage.py:32-34）；内存缓存整表 `_users` 启动加载（user_storage.py:20-30）；读写方 auth 路由 + chat_share/share 路由查 owner（chat_share.py:76、share.py:112） |
| 知识库分享 | `data/shares.json`：share_id→SharedKnowledge{share_id,owner_user_id,title,permission=read_chat,category_l1/2/3(空=全库),created_at,expires_at,is_active}（share_storage.py:29-49、models/share.py:24-49）；分享会话 `data/shares/{share_id}/{viewer}.json`：{share_id,viewer_user_id,messages[],updated_at}（share_storage.py:144-185） |
| 分享读写方 | 创建/列表/撤销 routes/share.py:221,252,337；浏览+问答读写会话 share.py:423,476,511,515；delete 时 rmtree 会话目录（share_storage.py:121-128）；过期默认 30 天（share_storage.py:74-79），**无过期清扫任务**【推断·待验证】 |
| 聊天分享 | `data/chat_shares.json`：share_id→SharedConversation{...,conv_id,permission=read_only,messages 快照[]}（chat_share_storage.py:26-52、models/chat_share.py:22-40）；消息为创建时快照只读（chat_share.py:104-122）；读写方 routes/chat_share.py:63-178 |
| 原子性 | shares/chat_shares 均临时文件+os.replace（share_storage.py:50-54、chat_share_storage.py:48-52）；但 RMW 无锁；append_message 读-改-写（share_storage.py:168-185）并发追加会丢消息【推断·待验证】 |
| 隔离 | owner/viewer 均 user_id 维度；token 为 share_id（secrets.token_urlsafe 16B，models/share.py:16-21） |

## 8. 上传文件与杂项持久化

| 项 | 现状（证据） |
|---|---|
| 文档原文件 | `data/uploads/documents/{user_id}/{安全路径}`（upload.py:150-152，copy2 落盘），L1 兜底可重灌（upload.py:68-69） |
| 图片原图 | `data/uploads/images/{user_id}/`（upload.py:127,134）；OCR txt 由 ImageProcessor 存 `data/uploads/images/{stem}_ocr.txt`（默认 image_save_dir，file_processor.py:213） |
| MD5 索引 | `data/uploaded_md5.json`：key=`user_id|file_name`→sha256（upload.py:73-82,104-111；`_compute_md5` 实际 SHA-256，upload.py:104-105）；写=write_text 非原子无锁（upload.py:87-89） |
| 日志/评估/trace | `data/logs`（logging.log_dir，config.yaml:284，50MB×7 轮转）；`data/eval_logs`（config.yaml:138）；`data/traces`（tracing.local_json，config.yaml:197，单文件≤10MB）——均非业务数据，随卷备份 |
| eval 报告 | `data/eval_reports/report.md`（eval/reporter.py:15,156，CLI 产物） |
| HF 缓存 | `/app/data/hf_cache`（docker-compose.prod.yml:69，容器内 HF_HOME，持久化避免重建重下模型） |

## 9. 锁/原子性问题点汇总（只列现状）

1. 进程内协程锁仅存在于 JSONStorage（index.json 读改写，json_storage.py:77）；全项目无跨进程锁（除 news `.lock_daily` O_EXCL 文件锁，service.py:78-97）。
2. 原子写（tmp+os.replace）仅：JSONStorage 文件、ProfileStorage、ShareStorage、ChatShareStorage。**非原子**：news storage（md/json/index）、job archive index、job 两个缓存、users.json、uploaded_md5.json、eval_reports。
3. 无锁 RMW 读-改-写：profile upsert_update、share append_message、job 缓存、users 表。
4. 生产以单 worker（uvicorn --workers 1，Dockerfile:136-139）作为并发损坏的默认防线，而非应用层锁。
5. 清理缺口：news `daily_*.json` 残留与 index 过期项、job_reports 物理文件超 200 截断后不删、ChromaDB eviction 配置无代码实现、分享过期无清扫。
