# SEKB 变更日志（CHANGELOG）

> 记录所有功能迭代与问题修复。按时间倒序，最新在前。
> 维护约定：**每次功能开发或问题修复完成后，必须同步在本文件追加一条记录**，并更新文档头部「最后更新」日期。
> 最后更新：2026-09-09

---

## 2026-09-09

### 深度代码重构（WP2：upload 路由入库流水线下沉）
- **新增 `services/upload_service.py`**：从 `api/routes/upload.py`（1006→618 行）提取 MD5 去重索引、图片/文档原文件持久化、入库流水线（process_and_ingest/ingest_chunks/background_ingest/classify_document）、LLM 概览。
- **领域类型 IngestResult**：服务返回领域结果，路由转换为 UploadResponse，消除「服务→路由模型」反向依赖。
- 行为不变；test_image_processor 改从 upload_service 导入；新增 test_upload_service 6 例；全量 575 passed；ruff 全绿、mypy 301≤310。

### 深度代码重构（WP2：服务层下沉 · 首个子提取）
- **新增 `services/browser_client.py`**：从 `api/routes/job.py` 内联的 `_call_browser` + 硬编码 `_BROWSER_BASE` 提取为 `BrowserClient`（可配置 base_url、可注入/单测）。
- **job.py 路由瘦身**：BOSS 扫码两处调用点改走 `_browser.post(...)`，行为不变。
- 新增 test_browser_client.py 2 例；全量 569 passed；ruff 全绿、mypy 306≤310。

### 深度代码重构（WP1：公共工具收敛重复清零）
- **新增 `core/utils.py`**：收敛 `now_iso`（原 graph/state + storage/base 双份）、`fmt_dt`（原 share/knowledge/chat_share 三份）、`safe_float`（原 critic/scribe 双份）、`to_state_dict`（原 chat/cli/eval 三份）为单一实现。
- **新增 `tools/rag/format.py`**：三处 RAG 上下文格式化归一（retriever「知识库参考」/ executor「相关度」/ share「来源编号」三种风格集中维护）。
- **重复清零**：registry `except (MCPError, Exception)` 冗余简化为 `except Exception`。
- **测试**：新增 test_core_utils + test_rag_format 共 39 例；全量 567 passed（528+39）；ruff 全绿、mypy 307≤310。
- 覆盖跟踪项：P2-14 / Q-4.5 / Q-4.6 / Q-4.11 / P2-P2-03 / NEW-E。

### 文档工程重构（按 1-6 工程逆向分析提示词，完整版 12 篇）
- **备份**：原 `docs/` 历史内容清理：旧技术文档/问题记录/测试用例文档已删（git 历史可查），codeReview 迁移至 `docs/codeReview/`，运维手册至 `docs/ops/`，活文档至 docs 根。
- **保留**：运维/部署手册副本 → `docs/ops/`（10 篇，真实环境操作）；CHANGELOG/BACKLOG 活文档副本 → `docs/` 根继续维护。
- **新工程 `docs/tech/`**：完整版 12 篇证据驱动文档（00-README 地图 + 01-ARCHITECTURE C4/ADR + 02-RUNTIME-FLOWS + 03-MODULES + 04-DATA-MODEL + 05-API(65端点/SSE/CLI) + 06-CONFIG + 07-DESIGN-PATTERNS + 08-GLOSSARY + 09-OBSERVABILITY + 10-TESTING + 11-EVOLUTION），全部带 `file:line`、Mermaid、编号/术语一致。
- **事实表 SSOT**：`docs/tech/.facts/T1-T8`（路由 65/配置 178/LLM 23 调用/AgentState/工具/存储/异步/可观测），多子代理并行抽取。
- **发现与记录**：49 死配置、10 处绕过 LLM 统一入口、4 死指标、7 无消费 State 字段、`${VAR:-default}` 语法缺陷、多处注释-实现漂移（→ `docs/tech/漂移清单.md` + `待确认项清单.md`）。
- **代码-文档联动**：`.validation/check-doc-sync.sh`（pre-commit hook：改路由→提示 05、改 config→06、改 graph/agents→02/03、改存储→04、改前端→03、改测试→10）+ `check-freshness.sh`（last-updated/based-on-commit 过期标记）+ `check-links.sh`（链接/孤儿，本地与服务器均全绿）。
- 根 `README.md` 文档导航更新为新工程结构。

### AI 对话体验优化（技能按钮移除 + 消息操作 + 滚动跟随 + 真流式确认）
- **移除技能按钮（任务1）**：删除 AI 对话输入区「通用/应聘/科技资讯助手」技能按钮及 `skill` 参数链路（前端 service/store/Chat + 后端 ChatRequest/`_build_skill_context`/`_schedule_preference_extraction` 调用）。技能模式后续有更具体需求再开发。
- **消息操作按钮（任务2）**：AI 答复下新增 复制/重生成/转发；用户输入下新增 复制/编辑。store 新增 `regenerateAssistant`（截断到该用户消息后重发）+ `editUserMessage`（替换内容+删除其后消息重发），两者复用 `runStream` 走真流式。
- **滚动跟随（任务3）**：用户上滚（距底 >80px）即停止自动跟随，右下角出现悬浮「回到底部」按钮；点击恢复跟随。
- **真流式确认与思考进度修复（任务4）**：作答 token 已确认是**真流式**（`astream_with_stats` → token sink）。首字延迟主因是顺序多智能体链（supervisor → RAG → **deepseek-reasoner planner 15~35s** → executor 逐次 LLM 调用），非推送端问题。修复「思考过程停在旧文案」：`astream`（仅节点结束推送）→ `astream_events`（节点**开始**即推送「正在…」进度），长耗时 LLM 节点期间进度条实时可见。实测节点进度随执行实时推进（0s 理解意图→1s 检索/规划→3s 执行→6s 反思→7s 生成），简单问题首字 5.8s，复杂问题首字延迟取决于 reasoner 规划时长。


## 2026-09-09

### 测试工具链整合（代码审查批次 1+2，含精简去重）
- **约定确立**：每次提交前跑改动范围**增量测试**（`scripts/incremental_test.sh`，支持 `--staged` 提交前对比）；每两周/每月跑一次**全量测试**（`scripts/full_test.sh`）。
- **增量/全量脚本**：`scripts/incremental_test.sh`（改动映射：后端 ruff+mypy 回归门禁+相关 pytest，前端 tsc+eslint+vitest）+ `scripts/full_test.sh`（后端全量单测/集成/门禁 + 前端全量/构建）；后端测试跑在自动构建的 `sekb-toolbox` 镜像（backend 镜像 + ruff/mypy/pytest/feedparser）。
- **后端门禁**：ruff 覆盖面扩到 `tests/` 并清零存量 139 处违规（`86443f9`，行宽 100→120，适配存量中文/SSE 长行）；mypy 改从 backend 目录执行使 `[tool.mypy] strict` 真正生效，存量 310 条类型债转为**回归门禁**（`scripts/mypy_gate.sh` + `backend/mypy-baseline.txt`，超基线才失败，防新增不阻塞迭代）。
- **测试补强**：修复 2 个 API 聊天测试的 mock（`_run_chat` 已改 `astream` 流式执行，`ba79b75`），后端 521→528 passed；新增 TestClient 路由级 API 测试 7 例（知识列表/搜索参数透传、kb 未启用降级、401/403 门禁）；前端接入 @testing-library/react + jest-dom（组件测试 3 例，58 passed）。
- **前端门禁**：ESLint 9 flat config（typescript-eslint + react-hooks）接入 CI 并清零存量 error（`c431ada`），`no-explicit-any` 存量 114 条降 warning 逐轮收窄；tsc --noEmit 保留。
- **pre-commit**：`.pre-commit-config.yaml`（ruff + mypy 回归门禁走 docker，tsc/eslint 走本机 node）。
- **安全扫描**：CI 新增 security-scan job——gitleaks（密钥扫描）+ pip-audit（Python 依赖漏洞）；frontend-build 增加 `npm audit --omit=dev --audit-level=high` 阻断门禁（生产依赖当前仅 5 条 moderate，react-router 链）+ 全量 audit 上报（devDeps 漏洞非阻断）。
- **覆盖率门禁**：CI `--cov-fail-under=40`（当前 43%，逐轮上调）。
- **工具清单精简去重**：safety 并入 pip-audit、hadolint 并入 Trivy；文档守卫类（lychee/OpenAPI/清单）推迟到文档轮次（D15）；node_exporter/cAdvisor 属监控运维轮次。详见 `docs/BACKLOG.md`「五、测试工具链清单与状态」。



## 2026-09-08

### P0 止血（合并 Qoder 深度审查后）
- **修复（CON-02 并发丢数据，已动态复现）**：JSON 存储层引入 `asyncio.Lock` 保护 index 读-改-写临界区；uvicorn `--workers 2` → `--workers 1`（消除多进程共享 ChromaDB/SQLite 并发写、Prometheus 指标失真、scheduler 重复执行）。
- **修复（SEC-01 JWT 弱密钥）**：`get_jwt_secret` 改为 fail-closed（未配置/过短/含占位词即拒绝启动），移除硬编码回退；启动期校验；**已轮换生产密钥**（旧 token 全部失效，需重新登录）。
- **修复（SEC-02 端口暴露）**：backend `8000:8000` → `127.0.0.1:8000:8000`，仅经 nginx 反代对外。

### R2-06 答案 token 真流式
- **新增**：`llm_factory.astream_with_stats` 流式接口 + `core/token_sink.py`（contextvar token 回传）+ Executor 流式生成草稿答案逐 token 回传。答案在生成阶段即流式输出（实测 27.5s 开始出 token，618 token），替代「全量生成后逐字推」。

### 文档工程（第一刀）
- **导航收敛（C2）**：`docs/README.md` 补 8 篇孤儿（ISSUES-FIXES/boss-jd-cookie-manual/job-sources/INCREMENTAL-TEST-CASES/codeReview 等）+ 归档标注 + 新增「Phase 5 能力速览」与阶段路线图 Phase 5。

### 安全止血 + 访问控制 + 体验增强（批次 A0 + 白名单 + B1/B2）
- **安全修复（批次 A0）**：chat 路由补会话归属校验（SEC-01 越权 IDOR）；全局异常脱敏返回 `error_id`（SEC-04）；资讯只读接口补登录鉴权（SEC-05）；分享 `is_scoped` 改任意级非空 + 层级完整性校验（R2-01）+ 默认 30 天过期（R2-02）。
- **可靠性修复**：`backup_kb.sh` 加 `trap` 兜底启动 + 新增 `restore_kb.sh`（R2-03）；前端队列卡死补 `flushQueue` + 非流式清空队列入口（R2-05）；前端单测修复（streamChat 7 参 + user init 会话恢复）（R2-18）；RAG 检索失败打标记（RAG-09）。
- **新增（用户白名单）**：`ALLOWED_EMAILS` 环境变量（逗号分隔邮箱）——白名单内=完整功能，非白名单=预览（仅功能说明 + 科技资讯只读，不能重新生成日报）。`/me` 返回 `access_level`，前端按级别收窄菜单/路由，后端 router 级 `require_full_access` 依赖拦截。
- **新增（B1 思考过程流式）**：聊天由固定「正在思考…」改为按图节点流式推送真实进度（理解意图→检索知识库→规划→执行→反思→生成回答），用 `astream` + `asyncio.Queue` 并发消费。
- **新增（B2 发送快捷键）**：设置页增加「发送快捷键」选项（Enter 发送 / Cmd+Ctrl+Enter 发送），聊天输入框按设置生效。

### 知识库（关键故障修复 + 加固）
- **故障**：ChromaDB HNSW 段损坏导致 `/upload/status` 超时、`/upload/files` 500、上传 502（`chromadb.errors.InternalError: Failed to apply logs to the hnsw segment writer`，为 1.5.9 已知 HNSW bloat-guard bug，官方暂无修复版）。
- **纠正**：此前「删除 VECTOR 段从 WAL 重建」的修复**误删了向量**（WAL 早已合并进段，删除后重建出空段，导致检索返回 0）。真正修复为**重嵌入重建**：`scripts/rebuild_chroma_vectors.py` 从 SQLite 取出 10603 条文档 → 用同一 bge-small-zh 模型重嵌入 → 重建 collection，检索恢复（探针命中、RAG 查询分数 0.74）。
- **性能修复**：`count`/`list_entries` 不再加载 embedding（`include=[]` / `include=["documents","metadatas"]`），避免大库慢查询导致接口超时。

### 知识库加固（L0/L1/L2）
- **L0 锁版本**：`chromadb>=0.5.0` → `chromadb==1.5.9`（当前最新，含 bug 但无修复版，锁死防漂移；待官方修复版再升级）。
- **L1 原始文件落盘**：文档（md/pdf/docx/txt）上传时按相对路径保存到 `data/uploads/documents/`（此前只有图片存原图），作为 ChromaDB 全毁时的最终重灌源。
- **L2 每日备份**：`scripts/backup_kb.sh`（停 backend → 打包整个 `sekb_data` 卷 → 重启 → 保留 14 天），已装 crontab 每天 3:30 执行；已手动跑通首份备份（124M）。
- **L6 模型缓存持久化（D10）**：`HF_HOME=/app/data/hf_cache` 指向数据卷，embedding 模型缓存随 `sekb_data` 卷持久化——重建后端容器不再从 hf-mirror 重下 ~100MB 模型（已把旧缓存迁移进卷，冷启动后检索 0.08s，且每日备份一并覆盖模型缓存，恢复完全自足）。

### 账号隔离（审查 + 修复）
- **审查结论**：会话、职位缓存（3 个）、知识库 ChromaDB、分享均已按 user_id 隔离；**资讯 news 定性为「系统级公共资源」**（登录即可看），维持全局。
- **修复**：文件原始存储（L1）路径与 `uploaded_md5.json` 索引补上 user_id 隔离（`{user_id}/相对路径`、`{user_id}|文件名`），并迁移存量 133 条 MD5。

### 聊天增强
- **新增（对话排队自动发）**：回复进行中时新输入进入队列，当前回复结束后自动发送下一条，避免误打断。前端队列（`pendingQueue`+`flushQueue`）+ 后端按会话加并发锁（同一会话重复流式请求返回错误事件）。
- **新增（历史会话右侧快速导航）**：对话区顶栏加「历史会话」按钮，悬停弹出历史会话浮层（置顶/切换/删除），参考 deepseek 的深色浮层，方便快速查看与切换历史会话。

### 多功能联动地基（第 4 条前置）
- **新增（用户画像）**：`UserProfile` 模型 + `ProfileStorage`（按 user_id 隔离，`data/profile/{user_id}.json`）+ `GET/PUT /api/v1/profile`。含求职偏好（目标岗位/城市/薪资/公司类型/关键词）、技能、职业目标、资讯关注大类——供「应聘助手 / 科技资讯助手」skill 与招聘分析、资讯模块联动使用；聊天中的偏好回流将存入画像。

### 聊天增强 vol.2
- **性能修复**：知识库分类统计 `/knowledge/categories/stats` 由「载入全部文档全文」改为「仅载元数据」，耗时从 ~18.9s 降到秒级——修复分类目录下每个分类无条目数（前端拿不到 counts）。
- **修复（分类目录数量为 0）**：`list_entries` 用 `get(key, [])` 兜底，但当 ChromaDB `include` 不请求某字段（如 `documents`）时，该键存在但值为 `None`，导致 `len(None)` 抛错、统计返回空 `{"stats":[]}`。改用 `get(key) or []` 兜底，统计恢复正常（全部/各分类有真实条目数）。
- **改进（系列文章树对齐）**：系列/子目录/文件改用 antd `showIcon` + 统一图标（📖 读、📁 文件夹、📄 文件），替代 emoji 混排，避免 `[+]` 开关与标题图标错位。
- **改进（应聘助手主动提问 + 画像回流）**：应聘助手**不再强依赖用户画像**——画像不全时主动向用户提问（目标岗位/城市/薪资/技能/职业目标）；用户给出后，回复末尾输出 `<PREF>{...}</PREF>` 结构化标签，后端解析并写入用户画像（反馈闭环），并把标签从展示内容中去除，避免用户看到。
- **新增（应聘助手与职位分析共享数据）**：应聘助手注入近期的职位分析结果（最近 2 份存档报告的「关键词/城市/职位数 + 热门方向 Top3 + 建议补强 Top3 + 代表职位 Top4」+ 职位市场概况）——实现「先分析职位、再据此沟通应聘事宜」的工作流。
- **改进（方案 B：记录员 LLM 抽取偏好）**：不再只依赖主 LLM 输出 `<PREF>` 标签（方案 A 保留为轻量兜底）。新增后台「记录员 LLM」独立分析对话，提取求职偏好写入画像——**异步、不阻塞主回复**，更可靠。
- **新增（D14-职位：画像反向影响职位分析）**：批量分析「一键市场」与单职位分析均按 `user_id` 读取用户实时画像（聊天积累的求职偏好/技能/职业目标），作为「用户实时更新偏好」补充注入分析提示词——分析与用户真实诉求更贴合。

### D14-资讯（定案：保持公共流，不个性化）
- **决策**：资讯为「系统级公共资源」，日报**不做按用户个性化注入**（方案①）。用户画像中的 `news_interests` 不参与日报生成；保持公共流干净自洽。若将来需要「为你推荐」类个人化，放到前端做，不改公共日报生成。
- **已知现象（非本次引入）**：聊天工作流首 token 延迟较高（supervisor→RAG→planner→executor 多步 LLM 调用），通用助手与应聘助手一致；SSE 渐进流出不影响接收。
- **改进（右侧导航改为「对话内历史提问」）**：右侧边缘触发条 + 右侧 Drawer 改为**列出当前对话内的用户历史提问**（用户侧输入，非会话列表），点击某条提问即**滚动定位到消息流中该提问位置并短暂高亮**——方便回看当时上下文。
- **新增（skill 技能按钮 + 调度）**：聊天框下技能按钮（通用助手 / 应聘助手 / 科技资讯助手，参考豆包）。切换技能 → 聊天请求带 `skill` 参数 → 后端构建技能上下文（应聘助手注入求职画像 + 职位市场概况；资讯助手注入关注大类）注入本次 LLM 输入，且**不写入会话历史**。用户画像地基 + skill 注入为「深入沟通」核心；「聊天偏好→画像→反向影响招聘/资讯」的反馈闭环记入 D14。

### 系列识别
- **新增**：`detect_series` 支持「dN 第N天」编号（如 `2-Agent全栈开发学习实践/2-s1-w1/d1-xxx.md`），系列名取**顶级目录名**；`N-` 数字前缀的根目录文件（总纲/学习计划/补充资料）保持独立、不误入系列。
- **性能修复**：`/upload/re-series` 由逐条 `update_metadata` 改为**批量元数据更新**（1 次 get + 1 次 update），并把单次 `limit=5000` 改为分页拉全量，避免大库下超时/漏文件。耗时从 >280s 降到约 15s。
- **修复**：系列树**默认折叠**（移除 `defaultExpandedKeys`），刷新页面即自动加载系列列表（此前因 ChromaDB 500 导致加载为空）。

### 文件上传
- **修复（去重不生效）**：同名去重/跳过此前用 `file.name`（basename）对比后端入库的 `file_name`（完整相对路径），导致文件夹重传时永远匹配不上、不弹「同名同内容跳过」框。现统一改用相对路径标识 `fileKey = webkitRelativePath || name`。
- **改进（续传弹窗）**：检测到未完成上传时，弹窗改为**每个文件前加复选框**，并**只列出尚未入库的文件**（过滤掉已成功入库的残留项）；确认后按勾选的文件续传（文件夹场景自动用文件夹选择器以保留相对路径）。

### 知识库分享（按分类限定范围）
- **新增**：分享不再只支持整个知识库，现在可在创建分享时用**分类级联选择**（大类 → 子类 → 细类）限定范围，选到哪一级就只分享到哪一级；不选则分享整个知识库。
- **后端**：`SharedKnowledge` 增加 `category_l1/l2/l3`；创建/列出/详情均返回 `category_label`；`list_shared_entries` 与分享问答的 RAG 检索都**按分享范围强制过滤**（防止越权看其他分类）；`retrieve`/`search` 增加三级分类过滤参数。
- **前端**：分享弹窗加 `Cascader` 分类选择、分享列表与结果页显示分享范围；分享访问页头部显示分类范围标签。

---

## 2026-09-07

### 文件上传
- **改进**：上传循环迁移到全局 store（zustand），切换左侧导航（组件卸载/重挂）不中断上传，重挂后从 store 读回进度。
- **新增（轻量刷新续传）**：上传时把未完成文件名写入 localStorage，刷新后检测到未完成列表时弹窗列出，让用户确认后重新选择这些文件继续上传（不存文件内容，File 对象跨刷新无法序列化）。

### 资讯日报（分类升级 + 信息源扩充）
- **新增**：分类从「关键词匹配」升级为「批量 LLM 语义分类」（单归属），能按内容区分「Agent(应用层) vs 大模型(算法层)」，失败回退关键词。效果：大模型 7→20 条、安全 0→20 条。
- **新增**：`WebFetcher` 网页采集器（CSDN 热榜 + 魔搭模型库，公开 JSON 接口无需 Token），接入资讯采集流水线。

### 文件上传 / 系列识别
- **新增**：系列文章改用 Tree 多级展示（系列 → 子目录 → 文件），默认展开第一级、子级折叠。
- **修复**：系列文章识别不准确——`detect_series` 支持「第N天/课/讲/期」等常见单位，文件名本身无系列名时用**文件夹名**兜底（利用「同一文件夹下多为同一系列」规则）。
- **新增**：`POST /upload/re-series` 重新识别存量文件系列（不重新分类，轻量）；前端「系列文章」卡片加「重新识别系列」按钮。

### 文件上传
- **修复**：上传失败 502/503 给出明确提示（后端重启/初始化中），并支持「一键重试失败的文件」。
- **新增**：同名文件按内容 SHA-256 对比——同名且内容一致时可「跳过」（含复选框批量跳过），同名但内容不同才询问覆盖。
  - 后端上传时计算并记录文件哈希（`data/uploaded_md5.json`），`/upload/files` 返回 `md5`。

### 资讯日报
- **回退**：取消「有限多归属」，恢复**严格单归属**（一条只归一个类）；不足条目的类通过「扩大信息源」解决，而非分类层凑数。
- **新增**：信息源扩充掘金分类/标签（前端/后端/Android/iOS/LLM/Agent/Flutter），filtered 从 95 → 162 条。
- **改进**：归类改用「标题 + 摘要 + 正文前 500 字符」匹配，提高与大类的相关性。
- **改进**：单类输出加 20 条硬上限，防止某类过载。

### 批量分析
- **回退**：恢复「职位列表严格一致才默认展示最后一次报告」，避免展示过时报告引起误解。

---

## 2026-09-07（更早）

### 资讯日报
- **新增**：`_generate_category` 对 LLM 输出 items 按 title 去重（消除重复条目）。
- **调整**：大类顺序让热门具体类（Agent/RAG/鸿蒙/跨端）优先于宽泛类，避免被「大模型」等抢光。
- **新增**：历史报告存档额外落结构化 JSON，前端用与批量/单职位一致的语义化布局渲染。
- **新增**：批量分析 tab 默认展示最后一次缓存报告（后按用户要求回退为严格一致）。

### 文件上传
- **新增**：支持文件 + 文件夹混合拖入上传，文件夹递归到最深层（`webkitGetAsEntry` 递归 + `webkitdirectory` 选择文件夹按钮）。

### 职位分析
- **新增**：缓存职位默认展示 + 筛选选项回填（`/job/cache/latest`）。
- **新增**：历史报告分「批量分析 / 单职位分析」两个子 tab。
- **新增**：批量分析默认展示最后一次缓存报告（`/job/batch-analyze/cached`）。
- **新增**：资讯日报单归属（一条只归一个类）+ 摘要关键词加粗高亮。

### 资讯日报（归类修复）
- **修复**：去掉「大模型发布/更新」的裸「模型」关键词、「Agent」类的裸「框架」关键词，避免误匹配数据模型/车型/前端框架等。
- **修复**：exclude「早报」，排除 IT早报 类每日汇总栏目。

---

## 2026-09-04 ~ 09-05

### 职位分析（重构）
- **新增**：批量分析接入求职者视角提示词（`批量职位分析.md` + `职位知识迭代.md`），并行两次 LLM 产出「市场行情」+「知识迭代」，喂 JD 摘要。
- **新增**：单职位分析融入「JD 潜台词翻译」，简历建议强化「diff 微调」（不重写全文），移除 per-JD 学习计划。
- **新增**：批量/单职位报告改为中文语义化布局（替代生硬 JSON 表格）。

### 职位收集
- **新增**：跨页全选（「全选全部 N 条」按钮 + `preserveSelectedRowKeys`），解决分页 100 条上限无法全选全部职位。

### 监控与告警
- **新增**：飞书告警卡片按钮拆分为「查看告警(Prometheus) / 查看看板(Grafana) / 查看对话记录」，与告警源一致。
- **修复**：前端健康检查误报 unhealthy（改用 HTTPS + `--no-check-certificate`）。
- **新增**：监控页公网暴露需求记录到 BACKLOG D5（暂缓）。

### 职位筛选
- **修复**：城市筛选从「前缀匹配」改为「包含匹配」，识别「浙江 / 北京市」这类多地职位。
- **修复**：mokahr 城市归一化优先取 cityName（省名兜底），避免「浙江」顶替「杭州」。

### 飞书告警深链
- **新增**：飞书告警「查看对话记录」深链定位到具体会话：
  - 后端 `answer_groundedness` 增加 `conversation_id` 标签；
  - `alerts.yml` 透传 `conversation_id`；
  - feishu_gateway 按钮跳 `FRONTEND_URL/chat?conversation_id=xxx`；
  - 前端 Chat 支持 `?conversation_id=` 深链，未登录时保留目标登录后回跳。

---

## 附：文档维护约定

1. 每次功能迭代或问题修复完成后，**必须**在本文件追加记录，并更新头部日期。
2. 涉及 `config.yaml` 参数变更的，同步更新 [04-config-reference.md](./04-config-reference.md)。
3. 新增架构级决策的，在 [01-architecture.md](./01-architecture.md) 追加 ADR。
4. 需求暂缓/推进的，同步更新 [BACKLOG.md](./BACKLOG.md)。
