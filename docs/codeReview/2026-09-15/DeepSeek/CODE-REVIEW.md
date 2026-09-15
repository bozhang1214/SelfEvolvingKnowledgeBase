# 代码全量审查 · 2026-09-15

> 覆盖范围：`backend/app/**/*.py` + `frontend/src/**/*.{ts,tsx}` + `deploy/**`
> 审查方法：逐模块静态阅读，聚焦并发安全、异步 IO、异常处理、安全、架构

***

## Critical（🔴）— 6 条

| #  | 文件 / 行号                    | 问题                                                                                                    | 影响                                                                       | 修复建议                                                                                             |
| -- | -------------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| C1 | `chat.py` L55 + L491-498   | 普通 `set` 充当并发锁，检查+添加非原子                                                                               | 两个并发 SSE 请求可能**同时通过检查**，互相覆盖跑图结果，最终消息丢失或错乱                               | 用 `asyncio.Lock` 按 `(user_id, conv_id)` 粒度做键控锁（`dict[str, asyncio.Lock]`），或用 Redis 分布式锁          |
| C2 | `market.py` L57 + L83      | `_load_cache()` / `_save_cache()` 是**同步磁盘 IO**，在 async 函数链路上被直接调用                                     | 阻塞整个事件循环，所有正在跑的 SSE 响应、其他用户请求一起被冻                                        | 用 `asyncio.to_thread(self._load_cache)` / `to_thread(self._save_cache)` 包装，或整个缓存层改为 `aiofiles`   |
| C3 | `audit.py` L66 + L110-114  | 审计日志用 `threading.Lock` + 同步 `open().write()`，**在 async 路由里直接调用**                                      | 每次登录/注册/登出都会阻塞 event loop；线程锁在 async 里是多余的                               | 改用 `aiofiles` 或 `asyncio.Lock` + 队列异步落盘                                                          |
| C4 | `json_storage.py` L356-390 | `append_message` 在同一个全局锁内做了 **5 次 to\_thread**（读 index → 读 messages → 写 messages → 读 index → 写 index） | 单用户发消息时其他所有用户的存储操作被串行排队；在消息密集场景退化成"每次请求都要排队 4\~5 次线程切换"                  | 合并成一次 lock：全程 in-memory，最后一次 to\_thread 落盘                                                       |
| C5 | `chat.py` L247             | `_run_chat` 外层 try/finally 里 `clear_context()` 被执行，但 **LangGraph 内部的 task/thread 仍可能持有旧 contextvar**  | 在某些 LangGraph 节点内部用了 contextvars 时，finally 清空可能导致并发请求串 trace\_id / 日志上下文 | 把 `bind_context/clear_context` 移到**每个独立 async 路径**（Supervisor/Planner 等节点入口），而非 `_run_chat` 函数级别 |
| C6 | `chat.py` L86              | `ChatRequest.user_id` 有默认值 `"default"`，与 JWT 鉴权 `get_current_user` 返回的真实 user\_id 并存                  | 如果请求体没传 user\_id 或传了别人的 user\_id，两者不一致；**死代码 + 混淆**                      | 删除 `ChatRequest.user_id` 字段，只从 `get_current_user` 取值                                             |

***

## High（🟡）— 11 条

| #   | 文件 / 行号                               | 问题                                                                                                | 影响                                                                                            | 修复建议                                                                                                          |
| --- | ------------------------------------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| H1  | `middleware.py` L79 + L119 + L146     | `_windows: defaultdict(list)` 无锁，清理过期 + append 分两步                                                | 高并发下两个协程可能读到同一份 list，各自 append 后互相覆盖，导致限流计数丢失或翻倍                                              | 用 `asyncio.Lock` 保护整个滑动窗口的读-改-写                                                                               |
| H2  | `llm_factory.py` L270-325             | tenacity `@retry` 装饰器嵌套在**每次调用都会重定义的内部函数** `_call` 上                                              | 每次 `ainvoke_with_stats` 都走一遍装饰器工厂开销；retry\_count 追踪完全不准                                       | 把 retry 逻辑提到模块级类方法或 tenacity 的 `before_sleep` 回调里精确计数                                                         |
| H3  | `llm_factory.py` L349                 | `retried = retry_count > 0 or error_msg is not None and "rate limit" in ...` 运算符优先级隐患             | `or` 优先级低于 `and`，虽然逻辑碰巧正确，但任何维护者读起来都要停一下                                                      | 加括号 `retried = retry_count > 0 or (error_msg is not None and "rate limit" in (error_msg or "").lower())`      |
| H4  | `embedding.py` L125 + L142            | `_embedding_cache` 是普通 dict，检查+添加非原子；缓存 key 只看 model\_name，忽略 allow\_hash\_fallback               | 两个不同 `allow_hash_fallback` 传入相同 model\_name 会**共享同一个缓存实例**，第二个调用的 allow\_hash\_fallback 被静默丢弃 | cache key 改为 `(model_name, allow_hash_fallback)` 元组                                                           |
| H5  | `auth.py` L44                         | `get_jwt_secret()` 直接读 `os.environ.get("JWT_SECRET")`，**不走 config 加载链路**                          | 如果用了 `load_config()` 展开的 `${JWT_SECRET}` 或 `.env` 文件，auth.py 根本感知不到                           | 统一从 `config.api.auth` 读取，或在 config 加载前显式 `load_dotenv()`                                                      |
| H6  | `bootstrap.py` L354 + `server.py` L52 | **两个独立的** **`_app_context`** **/** **`_context`** **全局变量 + 两个独立的** **`get_app_context()`** **函数** | chat.py 用 `server.get_app_context`，job.py 用 `bootstrap.get_app_context`——两者读的是**不同变量**！       | 删掉 bootstrap.py 里的 `get_app_context`，全局只有 server.py 的那个                                                       |
| H7  | `json_storage.py` 全篇                  | 全局单锁 `self._lock` 保护**所有会话**的所有存储操作                                                               | 任何一个慢并发请求（如大消息 append）都会阻塞**所有**其他用户的存储                                                       | 按 `conv_id` 分锁：`dict[str, asyncio.Lock]`，加锁时创建，释放时清理                                                          |
| H8  | `chat.py` L279 + L212                 | `_stream_tokens` 逐字符 yield，`await asyncio.sleep(0)` 每个字符切一次事件循环                                   | 5000 字回复 = 5000 次 event loop 切换，严重浪费调度开销                                                      | 按块 yield（如每 30\~50 字或每 100ms flush 一次）                                                                        |
| H9  | `llm_factory.py` L385-408             | `astream_with_stats` 流式路径**永远记录 input\_tokens=0, output\_tokens=0**                               | 流式答案的 token/成本统计全部丢失，用户看到的"用量"完全不准                                                            | 在 chunks 收集完后用 `len("".join(chunks))` 估算 output\_tokens，或让 LLM provider 在流结束后返回 usage\_metadata               |
| H10 | `chat.py` L199-224                    | `astream_events` 里的 `acc.update(to_state_dict(out))` 会被**最后一个节点的 state** 完全覆盖前序节点的字段              | 如果 Supervisor 在 state 里写了 A 字段，Planner 没写 A，LangGraph 增量合并应该保留 A，但 `acc.update` 可能出问题         | 把 `acc.update` 改成深合并，或用 LangGraph 原生的 `final_state = await graph.ainvoke(state)` 替代手动 astream\_events 拼 state |
| H11 | `audit.py` L110-117                   | async 路由里直接调用同步 `open().write()` + `threading.Lock`                                               | 阻塞 event loop；线程锁在 async 里多余                                                                  | 改用 `asyncio.Lock` + 异步队列 + 后台消费者线程/进程落盘                                                                       |

***

## Medium（🟢）— 10 条

| #   | 文件 / 行号                                    | 问题                                                                              | 影响                                                         | 修复建议                                                        | <br /> | <br /> |
| --- | ------------------------------------------ | ------------------------------------------------------------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------- | :----- | :----- |
| M1  | `chat.ts` L205 / L219 / L258 / L272 / L289 | 前端 5 处**静默 catch** 错误处理                                                         | 出错时用户和开发者都看不到任何日志，排查问题时完全盲区                                | 至少在 catch 里调 `console.error()` 或 `logger.error()`           | <br /> | <br /> |
| M2  | `chat.ts` L107                             | SSE 解析失败静默忽略 `catch { // 忽略解析错误 }`                                              | 后端返回 malformed JSON 时前端直接吞掉，极难排查                           | 加 `logger.warn('SSE parse error', raw=dataStr)`             | <br /> | <br /> |
| M3  | `market.py` L29-30 + L16                   | 缓存文件路径、TTL、版本号全是硬编码常量                                                           | 换部署环境/数据目录时必须改源码；多 worker 部署时各进程独立文件                       | 把缓存路径放进 config，TTL 通过 config 或环境变量控制                        | <br /> | <br /> |
| M4  | `alerts.py` L36                            | `DEFAULT_ALERT_WEBHOOK_URL = "http://sekb-feishu-webhook:5001/webhook"` 硬编码内网地址 | 换 K8s 命名空间/端口时必须改源码                                        | 从环境变量读取：`os.environ.get("ALERT_WEBHOOK_URL", "http://...")` | <br /> | <br /> |
| M5  | `config.py` L58                            | `retry_backoff_seconds: [1, 2]` 与 llm\_factory.py L279 的 `max=10` 硬编码           | 想调重试间隔上限（如改成 30s）必须改源码                                     | 把 max\_backoff 放进 config.llm                                | <br /> | <br /> |
| M6  | `guard.py` L62-65                          | `parse_llm_guard` 在没匹配到 JSON 时，只要原文含 `"true"` 就判定为注入                            | **极容易误判**：LLM 返回 `"I think this is true because..."` 会直接封  | 严格匹配 JSON 字段 `{"injection": true/false}`，匹配不到时走正则规则层        | <br /> | <br /> |
| M7  | `chat.py` L219                             | `await on_progress(name)` 被**每个 LangGraph 节点的 start 事件触发**，包括 START、END 等内部节点   | 某些节点名不在 `_NODE_PROGRESS` 里时不推送 progress，没问题但浪费事件循环切换       | 改成检查 `name in _NODE_PROGRESS` 再推送                           | <br /> | <br /> |
| M8  | `auth.py` L26                              | `_jti_blacklist: set[str] = set()` 进程内存黑名单                                      | 多 worker 部署时 token 吊销跨进程不生效；**worker 重启后所有已吊销 token 重新有效** | 迁移到 Redis，key = `jti:blacklist:{jti}`，TTL = token 剩余有效期     | <br /> | <br /> |
| M9  | `embedding.py` L62                         | `_loaded: bool = False` 标志位 + `_ensure_loaded()` 非线程安全的懒加载                      | async 并发下第一次调用可能同时有多个协程触发 `_ensure_loaded`，重复加载模型          | 用 `asyncio.Lock` 保护模型加载，或在启动期 await 预加载                     | <br /> | <br /> |
| M10 | `Chat.tsx` L540                            | 列表渲染用 `key={idx}` 而非稳定的消息 ID                                                    | 中间插入/删除消息时 React 复用错位，可能导致编辑态或选中状态挂到错误消息上                  | 改成 \`key={msg.message\_id                                   | <br /> | idx}\` |

***

## Low（🔵）— 6 条

| #  | 文件 / 行号                              | 问题                                                         | 影响                                                       | 修复建议                                                                                            |
| -- | ------------------------------------ | ---------------------------------------------------------- | -------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| L1 | `deploy.sh` L112                     | `eval "$@"` 执行命令                                           | 虽来自受控变量，但仍是反模式                                           | 改成参数数组：`run() { ... }` 接受 `"$@"` + `exec`                                                       |
| L2 | `chat.py` L55                        | `_conv_inflight` set 无清理机制                                 | 如果进程 crash 或 SSE 异常断连，被占的 key 永远残留（直到进程重启）               | 加 TTL 清理后台任务，或在 run\_task 超时后自动丢弃                                                               |
| L3 | `json_storage.py` L156 + L203 + L210 | `conv_id` 直接拼进文件路径 `f"{conv_id}.json"`                     | 虽然 UUID 不会有路径穿越，但如果有人伪造 conv\_id（如 `../etc/passwd`）还是有风险 | 用 `Path(conversations_dir, f"{conv_id}.json").resolve()` 并校验 parent                             |
| L4 | `embedding.py` L125                  | `_embedding_cache` 模块级全局                                   | 测试间状态泄漏                                                  | 提供 `clear_embedding_cache()` 函数，conftest.py 里 fixture tear down 调                               |
| L5 | `alerts.py` L43-46                   | `_LAST_SENT` dict 无 TTL 清理（只有 >200 条时整体清一半）                | 长跑进程里 key 累积                                             | 改成按时间戳定时清理：`_LAST_SENT = {k: v for k, v in _LAST_SENT.items() if now - v < DEDUP_WINDOW_S * 2}` |
| L6 | `chat.ts` L43-44                     | `streamControllers: Map<string, AbortController>` 模块级全局无清理 | 长期间如果 controller 没被正确 abort/delete，Map 会缓慢增长             | 在 cancelStream/deleteConversation 里确保清理；加 `streamControllers.clear()` 兜底                        |

***

## 统计

| 严重度         | 数量     | 典型影响                       |
| ----------- | ------ | -------------------------- |
| 🔴 Critical | 6      | 并发消息丢失、全 event loop 阻塞、死代码 |
| 🟡 High     | 11     | 限流失效、token 统计不准、全局锁瓶颈      |
| 🟢 Medium   | 10     | 静默错误吞掉、硬编码路径、弱正则           |
| 🔵 Low      | 6      | eval 反模式、key 无 TTL、测试隔离    |
| **合计**      | **33** | <br />                     |

