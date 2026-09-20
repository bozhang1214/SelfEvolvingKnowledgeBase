# 端云协同 · 实施记录（M0–M3）

> **这是什么**：`RFC-端云协同与端侧Agent.md` 的**实施记录部分**（原 §14–§18）单独归档到这里。
> 设计、决策、里程碑、风险与跟踪项仍在设计文档里；这里放的是**每一轮到底做了什么、
> 实测数字是多少、踩了哪些坑**——它们是证据，篇幅大、更新频繁，放在设计文档里会把主线淹没。
>
> 做法同 SEKB 既有的约定：**活文档保持精简，历史留档可追溯**。
> 分支：设计动机 → [RFC-端云协同与端侧Agent.md](./RFC-端云协同与端侧Agent.md)；
> 端侧宿主代码 → [`apps/`](../apps/README.md)；接口契约 → [ops/16-端云协同协议.md](./ops/16-端云协同协议.md)。

---

## 0. 阶段总结与恢复指引（2026-09-20 暂停点）

真机未到，端侧线在此暂停。**已完成并验证**的部分、**待办**与**怎么接着干**如下。

### 已完成（都有实测证据）

| 阶段 | 交付 | 关键实测数字 |
|---|---|---|
| **M0** Mac 端侧跑通 | Ollama 三档模型 + 全本地/端云双档配置（生成物，`--check` 进 CI） | 2B decode 86–116 tok/s、923 token TTFT 427ms；关思考 **25×** |
| **M1** 端云一致性内核 | `plane_router.py` + 路由日志 + 交接摘要 + 版本戳；**一处挂载、全链路生效** | 端侧 119ms（未关思考 1898ms、冷启动 6547ms）；前端缀守卫改道 0 字符外泄 |
| **M2** Android 端侧宿主 | `apps/android/`（Kotlin+Compose）：设备凭证、SSE、工具+权限审计、自检 | 自检 **14/14**；越权拦截率 50%；UI 人工路径走通 |
| **M3①** 端侧 RAG | ONNX `bge-small-zh-v1.5`（与云端同空间）+ SQLite 索引 + `kb_search` 工具 | **Hit@1 87%、Hit@3 100%、MRR 0.928**；嵌入 19–38ms、检索 <1ms |
| **M3②** 可用化 | SAF 导入（纯文本/PDF）、文档列表与删除、回答显示检索来源 | 自检 **26/26**；导入→检索→删除全链路 |
| **M3③** 压缩 | int8 量化（体积 1/4） | 排序不变、嵌入 **19ms vs 33–38ms**、阈值重标到 0.5 |

### 待办（按优先级）

| 待办 | 为什么现在做不了 / 怎么做 |
|---|---|
| **真机性能数字**（decode tok/s、TTFT、内存峰值、发热） | **唯一的外部依赖：等真机**。模拟器跑在 Mac CPU 上，数字不可用（RFC §9.1） |
| 扫描件 **OCR** | PDF 文本层已支持，扫描件目前如实拒绝；端侧 OCR 会让包再大一圈，值得单独评估 |
| Word/Excel 解析 | 后端有 python-docx/pypdf，端侧没有；等真机出现真实用例再排 |
| 混合检索（端侧 + 云端检索融合） | 一旦要做，**int8 的空间不兼容**会成为约束（要么回退 fp32，要么保持两套索引） |
| 文档管理界面（搜索/批量删除） | 现在只列最近 3 份；等真机真实用量再设计，避免凭空设计 |
| UI 导入的"最后一公里"自动化 | 自检覆盖了导入逻辑；SAF 选择器里的选中动作没脚本化（人工点两下即可，见 BACKLOG） |
| 鸿蒙/iOS 端 | 见设计文档 §17 的工具链实测与三条路线（KMP/CMP 鸿蒙 Beta、CMP for iOS、llvmpipe） |

### 怎么接着干（恢复步骤，都在仓库里）

```bash
# 1) 起模拟器 + 推模型与样本（全部状态在仓库内，无需仓库外写权限）
bash scripts/emulator.sh --background
bash scripts/android.sh push-model            # fp32 + int8 一起推（int8 优先生效）
bash scripts/android.sh push-sample           # 本机文档 E2E 用的 md + pdf 样本

# 2) 三重验证（各约 1 分钟）
bash scripts/android.sh install
adb shell am start -n com.sekb.ondevice/.MainActivity --ez selftest true   # 端云+权限+RAG 自检
adb shell am start -n com.sekb.ondevice/.MainActivity --ez evalrag true    # 检索评测 + 阈值标定
bash scripts/android.sh test                                               # JVM 单测（无需设备）

# 3) 模型工具链（离线导出 + 量化 + 验证）
bash scripts/fetch_embedding_model.sh --verify          # 与 sentence-transformers 比对 + 区分度检查
bash scripts/fetch_embedding_model.sh --int8            # 产出 int8（空间戳不同，切换时自动重算索引）
python3 scripts/eval_embedding_quantization.py          # fp32 vs int8 对比（体积/延迟/质量/标定曲线）
```

### 恢复时必须守住的三条不变量

1. **向量空间戳**：只有与云端同空间的模型（fp32 `bge-small-zh-v1.5@512`）允许与云端向量融合；
   int8 与桩都不行。换模型 → **原地重算**索引（`SqliteVectorStore.reembed`），绝不混用。
2. **阈值是标定出来的**：0.5 是对 fp32 与 int8 都成立的取值；换模型/换语料必须重跑标定曲线。
3. **设备专属数据永不出端**：`kb_search` 的 `deviceOnly` 是构造期注入；非本机嵌入一律拒绝。

---

## 14. M0 实施记录（2026-09-17）码**

`backend/app/api/server.py:129` 已经在读 `SEKB_CONFIG_PATH` 环境变量（`backend/.env.example:65` 也有登记），
所以"全本地 profile"只是**换一个配置文件**：

```bash
SEKB_CONFIG_PATH=config.local.yaml <启动命令>
```

### 14.2 避免"配置档漂移"：本地档用**生成**而非手抄

SEKB 是单文件配置（无 overlay），本地档必然是主配置的副本 → 手抄必然漂移。
所以新增 `scripts/make_local_profile.py`：**主配置是唯一权威，本地档是派生产物**，
并提供 `--check` 供 CI 校验两者同步（与文档防漂移同一原则：单一事实源 + 机器校验）。

派生的改动只有 llm 段 3 处 + 逐角色 model（按三档映射）：

| 档 | 模型 | 承担角色 |
|---|---|---|
| short | `qwen3.5:2b-mlx` | supervisor / critic / chat_simple / rerank / ragas |
| default | `qwen3.5:4b-mlx` | planner / executor / critic_complex / scribe / job_analysis |
| quality | `qwen3.5:9b-mlx` | news_report |

> 顺带确认一个架构事实：`LLMConfig.base_url` 是**全局单点**，但 `LLMRoleConfig.model` 是**逐角色**的
> —— 所以"端侧按角色分档"在同一端点内**现在就能用**，不需要等 S5。

> **网络环境事实（2026-09-17 实测，别再踩）**：本机直连国际源基本不可用 ——
> `registry.ollama.ai` 的 blob 请求 **307 重定向后 0 字节**（`-4`/`-6` 都是 0 B/s）、
> HuggingFace 官方 0 B/s、GitHub raw 仅 **27 KB/s**；而同机国内链路 6.3 MB/s、
> **ModelScope 16.8–20 MB/s**。因此模型改从 **ModelScope 的 `unsloth/Qwen3.5-*-GGUF`** 取
> （Q4_K_M 三档合计 9.2GB），再用 `ollama create` 导入。**附带好处**：GGUF 正是后面 Android
> (llama.cpp) 要用的格式。可复现命令见 `scripts/edge_m0_setup.sh`。

### 14.3 测量脚本：`scripts/edge_bench.py`

按 **prefill（算力受限）/ decode（带宽受限）分别计时**，端云同题对照。
两种端点取数方式不同：Ollama 走原生 `/api/chat`（回报 `prompt_eval_duration` / `eval_duration`，
是**精确值**）；云端走 OpenAI 兼容流式（取首 token 时间，decode 用"首片→末片"窗口折算）。

**量得准比量得多重要——本轮实测踩到并修掉两个陷阱**（都写进了脚本注释）：

| 陷阱 | 现象 | 修法 |
|---|---|---|
| **Ollama 前缀缓存** | 同一 prompt 复跑时 `prompt_eval_duration` 只统计未命中部分 → 1025 token 算出 **42844 tok/s** 的荒谬值 | 每次给 system 加一次性 nonce 破坏前缀复用（`--no-nonce` 可关） |
| **thinking 模型的 `reasoning_content`** | 云端「输出 64 token 但 TTFT=0」自相矛盾 | 计时同时覆盖 `delta.content` 与 `delta.reasoning_content` |

### 14.4 集成冒烟：SEKB → 本地 Ollama（用已装的 7B 代跑）

qwen3.5 尚在下载，先用已就绪的 `qwen2.5:7b` 验证**通路**（`SEKB_CONFIG_PATH=config.local.yaml` +
真实 `LLMFactory`，把角色模型临时指向该模型）：

| 角色 | 类型 | 结果 | 延迟 | 返回 |
|---|---|---|---|---|
| `executor` | 普通 | ✅ | 901ms | `{"intent":"news"}` |
| `supervisor` | **`response_format: json`** | ✅ | **160ms** | `{"intent":"news"}` |

用量统计正常：`calls=2 in=116 out=12 cost=$0.0`。
→ **结论：端侧通路（base_url / api_key / JSON 约束 / 用量记账）全部验证通过；
`supervisor` 级意图分类在端侧 160ms 完成，完全支撑"短任务端侧"的定位。**

### 14.5 端云对照实测（M5 Pro，最终数据）

> 端侧已按要求关闭思考模式（见 §2.4）；取数时无其他负载。**云端仍为其默认配置**（未关思考），
> 所以下表对端侧略偏严。

| 平面 | 模型 | 任务 | 输入tok | 输出tok | TTFT(ms) | prefill(tok/s) | decode(tok/s) | 总时长(ms) | 生成500tok预计(s) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 本地 | `qwen3.5-2b` | **intent（短进短出）** | 78 | 7 | **111** | 705 | **115.9** | **222** | 4.3 |
| 本地 | `qwen3.5-2b` | short_qa | 57 | 59 | 76 | 750 | 108.1 | 640 | 4.6 |
| 本地 | `qwen3.5-2b` | structured | 65 | 277 | 78 | 829 | 85.8 | 3327 | 5.8 |
| 本地 | `qwen3.5-2b` | long_context（923 tok 输入） | 923 | 117 | 427 | 2160 | 87.5 | 1790 | 5.7 |
| 本地 | `qwen3.5-4b` | intent | 78 | 6 | 232 | 336 | 54.3 | 426 | 9.2 |
| 本地 | `qwen3.5-4b` | long_context | 923 | 126 | 912 | 1012 | 46.3 | 3677 | 10.8 |
| 本地 | `qwen3.5-9b` | intent | 78 | 6 | 373 | 209 | 34.5 | 632 | 14.5 |
| 本地 | `qwen3.5-9b` | long_context | 923 | 105 | 1444 | 639 | 28.0 | 5239 | 17.9 |
| 云端 | `deepseek-flash` | intent | 80 | 64 | 845 | — | 163.7 | 1230 | 3.1 |
| 云端 | `deepseek-flash` | short_qa | 57 | 256 | 1262 | — | 173.3 | 2769 | 2.9 |
| 云端 | `deepseek-flash` | structured | 65 | 512 | 1098 | — | 157.9 | 4341 | 3.2 |
| 云端 | `deepseek-flash` | long_context | 910 | 256 | 1306 | — | 218.1 | 2475 | 2.3 |

**结论（这就是路由矩阵的实测依据）**：

1. **短任务端侧完胜**：intent 任务端侧 **222ms vs 云端 1230ms（快 5.5×）**，
   TTFT **111ms vs 845ms（快 7.6×）** —— 省掉的是网络往返 + 云端排队。
2. **decode 差距已收窄到 1.4×**：2B 档 **115.9 tok/s** vs 云端 163.7 tok/s。
   作为对照，7B 档只有 43–45 tok/s（差 3.5–5×）→ **"选小一档"比"端侧优先"这个口号更关键**。
3. **长输出云端仍略优但已接近平价**：等长折算 500 token，端侧 2B **4.3s** vs 云端 **3.1s**（1.4×）。
4. **长输入端侧可接受**：923 token 输入时端侧 TTFT 427ms（prefill 2160 tok/s），并未崩。
5. 4B/9B 档定位为"质量优先、输出不长"：intent 426ms / 632ms 仍远快于云端 1230ms。

> **对路由阈值的影响**：原方案把"长输出（>500 token）一律判给云端"过严。
> 实测后改为：**≤300 token 输出走端侧 2B；>500 token 且要求质量才上云**；
> 4B/9B 用于"需要更好质量但输出中等"的端侧任务。

### 14.6 M0 完成情况

- [x] 三档模型就绪（`qwen3.5-2b` 1.3GB / `4b` 2.7GB / `9b` 5.7GB，GGUF Q4_K_M，经 `ollama create` 导入）
- [x] SEKB → 本地 Ollama 通路验证（**用真实本地档、不改模型名**）：`supervisor` 角色
      返回合法 JSON、记账正常、cost=$0
- [x] 实测数字回填 §4.1 路由阈值
- [x] SEKB 端侧通路（真实本地档、真实 LLMFactory）—— M0 完成
- [ ] SEKB 全链路（HTTP/SSE 端到端）延迟 —— 属 M1（见 §15.5）
- [ ] `news_report`（9B 档）实际生成质量抽查 —— 需要长输出，属 M1

---

## 15. M1 实施记录（2026-09-17，已交付待部署）

目标：把 §4.5 的一致性机制落成**可运行、可观测**的代码。

### 15.1 落点选择：为什么做在 SEKB 内，而独立 repo 推迟到 M2

M1 的机制（路由决策 / 升级 / 交接 / 版本戳 / 路由日志）**两端都要理解**：升级载荷由服务端接收、
交接摘要要进服务端 prompt、版本戳必须两边一致。所以 M1 的代码落在 SEKB 内最自然；
**独立 repo（D2）在 M2 开始写 Android 宿主时创建**——那时才有真正的客户端代码。
（D2 结论不变，只是起点从 M1 挪到 M2。）

### 15.2 已交付

| 组件 | 位置 | 说明 |
|---|---|---|
| 平面路由 + 升级 + 交接 | `backend/app/core/plane_router.py` | `PlaneRouter.decide/evaluate/should_escalate/versions` + `RoutedLLM` 门面 |
| 平面端点配置 | `backend/app/core/config.py` 的 `PlaneEndpointConfig`/`RoutingConfig`/`PlanesConfig` | `llm.planes.edge` + `llm.planes.routing`；不配 = 单平面（行为与改造前完全一致） |
| 按平面取实例 | `backend/app/core/llm_factory.py` | 支持 `get(role, plane)` 与 `ainvoke_with_stats(..., plane=...)`；记账按平面分开，避免两平面互相覆盖 |
| **主链路接入** | `LLMFactory.attach_router()` | **一处挂载、全链路生效**：agents/graph/tools/memory 那十余处 `ainvoke_with_stats(role, msgs)` 调用点**零改动**即具备端云路由与自动升级（挂载点在 `bootstrap.py` 第 4.5 步） |
| **端侧预热** | `LLMFactory.warmup_edge()` | 走 Ollama `/api/generate` 空调起 + `keep_alive=30m`，消除 ~6.5s 冷启动；失败不阻塞启动 |
| **端侧关思考** | `PlaneEndpointConfig.disable_thinking`（默认 true） | 注入 `reasoning_effort="none"`：同一意图分类 **2283ms/236token → 89ms/7token（25 倍）**。注意 `think=false` 会被 OpenAPI 客户端拒掉，必须用这个字段 |
| 路由事件流水 | `backend/app/storage/edge_route_storage.py` | append-only JSONL + 幂等键 + 行数裁剪（原子替换）；产出**两个北极星指标** |
| 可观测接口 | `backend/app/api/routes/edge.py` | `GET /api/v1/edge/routes/stats`、`GET /routes`、`POST /route-events`（供 M2 的 Android 上报） |
| 端云协同配置档 | `scripts/make_local_profile.py` → `backend/config.edge-cloud.yaml` | 与「全本地档」并列，都是**生成物**（`--check` 进 CI，防漂移） |
| 端到端冒烟 | `scripts/edge_m1_smoke.py` | 真跑 Ollama + DeepSeek，验证「短任务落端侧、长输出落云端、指标齐全」 |
| **6 类升级信号** | `plane_router.evaluate()` | `json_invalid` / `empty` / `degenerate` / `timeout` / `low_confidence` / `tool_hallucination` 五类**事后**判定 + `context_overflow` 由输入预算**事前**改判（§4.2 全齐） |
| **自动交接摘要** | `HandoffFacts` / `HandoffBuilder.from_facts`·`from_event` / `inject_handoff` | 升级时**自动**生成并注入云端请求（紧跟 system 的前缀位置）；调用方无需知道 handoff 的存在 |
| **流式前缀守卫** | `LLMFactory._astream_guarded` + `PlaneRouter.stream_guard` | 端侧流式先攒 `stream_guard_chars`（默认 60）再判定，命中信号则**丢弃该前缀**改走云端——用户此时还没看到任何字符，等于免费改道 |
| **隐私硬边界** | `Decision.device_only` + `PlaneRouter.escalation_allowed` | DEVICE_ONLY 数据**任何情况下不上云**，包括"端侧答得很烂"时：宁可承认失败并留痕 `escalation_blocked:device_only` |
| 测试 | `backend/tests/unit/test_plane_router.py` | 69 条：决策矩阵 / 6 类信号 / 前缀守卫与 0 字符外泄 / 隐私边界矩阵 / 指标口径 / API / 门面升级路径 / 接线回归 |

### 15.3 首轮跑出来的两个真问题（都被测试/冒烟当场抓住）

1. **用「允许上限」当「预期输出」判预算 → 把最该端侧的任务赶去云端。**
   `supervisor.max_tokens = 500 > 端侧预算 300`，于是意图分类被判云端。
   修正：引入 `DEFAULT_EXPECTED_OUTPUT`（supervisor 32 / planner 600 / news_report 6000…）
   + 支持 `routing.expected_output` 按角色覆盖 + 调用方显式传 `max_output_tokens`。
2. **端侧平面没接「档位→本地模型」映射**，把 `deepseek-flash` 发给了 Ollama → `model not found`。
   单元测试用假工厂盖不到，**端到端冒烟一跑就现形**。修正：`_edge_model_for()`
   （角色 → 档位 → `planes.edge.models`），并补了两条接线回归测试。

3. **M0 的「关思考」经验没接进 SEKB 路径**：主链路首次跑出来 1898ms，而基准是 222ms——
   一查是端侧平面没关思考（236 token 全花在推理上）。修正后 **128ms**。
   **教训**：bench 脚本里验证过的优化，不等于产品路径上生效；必须两端都验。
4. **`_create_llm(plane="edge")` 能绕过档位映射**（直接调用又变回"把云端模型名发给 Ollama"）：
   把解析下沉进 `_create_llm`，让"绕过"在结构上不可能发生。

5. **隐私硬边界原本是漏的**（第二轮自查抓到）：`device_only_roles` / `device_data=True`
   只影响**平面选择**，不影响**升级判定**——端侧答成非法 JSON 时，`RoutedLLM.ainvoke`
   照样会把"永不出端"的数据发给云端。这正是 D-系列里唯一的硬约束（只允许设备数据留在
   端侧），却是最容易被"顺手升级"绕过去的地方。修正：`Decision.device_only` +
   `escalation_allowed()`，升级前必须过这道闸；不达标时记 `escalation_blocked:device_only`。
   **教训**：隐私约束必须写在**决策点**上，写在"入口参数"上就等于没写。

> 这几条正好说明为什么「单元测试 + 真实端点冒烟」两层都要有：前者验逻辑，后者验接线；
> 而"bench 里验证过"与"产品路径生效"是**两件事**，必须分别验。

### 15.3.1 顺带核实的代码事实

`_create_llm` 里原本写着「优先 langchain-deepseek，失败回退 langchain-openai」，但
`from langchain_deepseek import ChatDeepseek` 的**类名拼错**（实际是 `ChatDeepSeek`），
该分支从未生效过——生产一直跑的是 `ChatOpenAI`（行为正确，因为 DeepSeek/Ollama 都是
OpenAI 兼容端点）。已删掉这个假分支与误导性注释，改为显式使用 `ChatOpenAI`。

### 15.4 M1 冒烟结果（真实双平面）

```
role=supervisor  平面=edge   原因=edge_preferred                     模型=qwen3.5-2b  返回 {"intent":"news"}
role=planner     平面=cloud  原因=output_over_edge_budget(600>300)   模型=deepseek-flash
路由统计：端侧决策=1 端侧完成=1 升级=0 → 端侧完成率 100% / 升级率 0%
版本戳：embedding_space=bge-small-zh-v1.5@512 / edge_model / cloud_model / tier / tool_schema
交接摘要：<handoff from="edge" reason="...">…（以上是已建立背景，请直接续接，不要重复已完成的工作）

主链路（factory.attach_router 后，用**普通** ainvoke_with_stats 调用，不带 plane 参数）：
   路由已挂载: True      预热 qwen3.5-2b: 7–13ms
   调用结果: 平面=edge  模型=qwen3.5-2b  延迟=119ms（未关思考时 1898ms；冷启动 6547ms）

流式路径（第三轮新增，走**真实 SSE** 流经前缀守卫）：
   守卫=60字符  平面=edge  原因=edge_preferred|stream_guard
   首字=224ms（含守卫缓冲）  字符数=37
   输出='端侧推理通过降低计算复杂度、减少数据吞吐量和优化硬件架构，显著降低了能耗。'
```

**守卫的延迟代价（诚实记录）**：本次答案只有 37 字符 < 60 的守卫阈值，属**最坏情况**——
攒不满就得等流结束，首字延迟 = 整段生成时间（224ms）。长回答下首字延迟 ≈ 生成 60 字符的
时间（2B 上约 0.3–0.6s），总时长不变。之所以取 60 而不是更大：退化检测的下限是 40 字符
（`_DEGEN_MIN_LEN`），再往上攒只是把用户看得到的延迟换成更长的白等。

**哪里验的**：可自动改道的路径（退化前缀 → 改走云端 → 端侧 0 字符外泄）用**受控假模型**
在单测里验（真模型无法稳定复现退化输出）；真机冒烟验的是"接线没坏 + 延迟代价可测"。
两者分工而不是互相替代。

### 15.5 M1 待补

- [x] ~~把 `RoutedLLM` 接进 chat 主链路~~ → 已通过 `attach_router()` 在工厂层挂载（零调用点改动）
- [x] ~~端侧预热~~ → `warmup_edge()` 已实现并在 bootstrap 调用（实测 13ms 完成预热）
- [x] ~~路由日志纳入数据卷~~ → bootstrap 用 `config.storage.data_dir/edge/routes.jsonl`
- [x] ~~流式路径的**升级**~~ → 用**前缀守卫**实现（`_astream_guarded`）：
      改道发生在用户看到任何字符之前，因此**不需要**"已输出多少字符"的续写协议。
      代价是首字延迟 += 攒 60 字符的时间（实测最坏 224ms）。
      SSE 续写协议只有在"端侧已吐了一部分才失败"时才需要 → 属 M2 客户端侧议题。
- [x] ~~交接摘要的**自动生成**~~ → `HandoffFacts` / `HandoffBuilder.from_facts|from_event`，
      升级时自动注入云端请求（单测 `test_reroute_injects_handoff_into_cloud_stream` 验到）
- [x] ~~路由日志的**备份**~~ → 日志在 `data_dir/edge/routes.jsonl`，本就在 `sekb_data`
      数据卷内，`backup_kb.sh` 按卷打包即覆盖；已在脚本头注释里写明**为什么**必须备份
- [ ] 交接摘要的事实来源可以更丰富（当前从路由事件 + 端侧输出抽取；接入 chat 回合上下文后更准）
- [ ] M2：客户端侧 SSE 续写协议（`already_streamed_chars`）、Android 宿主、独立 repo

---

## 16. M2 实施记录（2026-09-17，进行中）

目标：把端侧宿主真正接起来——S1 设备身份、S3 执行位置、协议规范、Android 模拟器功能验证。

**M2 的三个已定选择（本轮开工前确认）**：

| # | 问题 | 结论 |
|---|---|---|
| 1 | 独立 repo `sekb-ondevice-agent` 放哪 | **Gitea 主 + GitHub 辅**，复用已有 push-mirror 机制（`scripts/gitea_mirror.py`，`REPO_MAP` 需加一行） |
| 2 | 端侧 LLM 怎么落 | 本期**不打 llama.cpp NDK**：做成**可插拔适配层**，模拟器默认连宿主机 Ollama（`10.0.2.2:11434`）；真·端侧推理与真机性能一起推到 M3 |
| 3 | 验证到什么程度 | 模拟器只验**功能与协议**（路由/升级/交接/工具调用/权限审计/事件上报），**不产出性能数字** |

第 2 条是对 §9 表格里 M2 描述的一处**收窄**，理由：模拟器跑在 Mac 的 CPU 上，
性能数字（decode tok/s、TTFT）无意义（§9.1 已列明），而"能不能装下 1.3GB GGUF + NDK 编译"
属于真机问题；先把协议与功能做对，性能等真机一次做准。§9 未改，作为历史承诺保留。

### 16.1 已交付（本轮）

| 组件 | 位置 | 说明 |
|---|---|---|
| **S1 设备身份** | `backend/app/storage/device_storage.py` + `backend/app/api/routes/device.py` | enroll / refresh / heartbeat / list / revoke 五件套：长效设备 token（默认 30 天）+ **可轮换**（旧 jti 立即失效）+ **可单独吊销**（手机丢了唯一止损手段） |
| 设备 token 域 | `backend/app/core/auth.py` | payload 加 `typ=device` / `device_id` / `scope=edge`；`sub` 仍是 user_id → 设备**继承用户的访问级别**，但多一层身份 |
| **生命周期咽喉点** | `auth._claims_checked()` | 所有鉴权依赖（`get_current_user` / `get_current_claims`）唯一入口，所以"设备已吊销/已轮换"在**任何**受保护路由上自动成立——不靠各路由自觉 |
| **最小权限边界** | `require_user_account`（挂在 `upload` / `knowledge` / `share`） | 设备 token **不能读写知识资产**：长效凭证一旦泄漏，能做的应当尽可能少；设备的读路径是 chat 里的 RAG |
| **S3 执行位置** | `plane_router.summarize_route_events` + `chat.ExecutionInfo` | 聊天响应（非流式与 SSE `done.meta`）带 `execution`：`primary_plane/model/reason/escalated/by_plane/versions/roles` —— 用户有权知道自己的数据出没出端 |
| 请求级事件收集 | `plane_router.collect_route_events`（contextvar） | 用 contextvar 而不是工厂上的 `last_route_event` 字典：后者是全局的，**并发请求会串台**（A 请求的"执行位置"里混进 B 的角色） |
| 协议规范 | `docs/ops/16-端云协同协议.md` | 端侧宿主↔SEKB 的完整契约（含权限边界表、SSE 事件、错误码、与代码的对应关系） |
| 测试 | `test_device_auth.py`（28 条）+ `test_plane_router.py` 新增 10 条 | 生命周期 / 最小权限 / 咽喉点回归 / 汇总口径 / **并发不串台** / 多模态 chunk 归一 |

### 16.2 本轮抓到的真问题

1. **吊销了也照样能聊天**（自己第一版就踩了）：校验最初只挂在 `get_current_claims` 上，
   而聊天路由走的是 `require_full_access → get_current_user` 这条**不经过 claims** 的路
   → 被吊销的设备 token 依然能聊天，"吊销"变成摆设。
   修法：把校验下沉到 `_claims_checked()`（两条依赖的共同入口），并补一条**咽喉点回归测试**。
   *教训*：安全校验要放在"所有路径都会经过的地方"，放在某一条依赖上等于没放。
2. **`require_user_account` 让 5 个既有 API 测试 401**：测试替身只覆盖了 `get_current_user`，
   新依赖读 `get_current_claims` 时拿不到 Authorization。这不是测试的错——它精确暴露了
   "新依赖改变了既有契约"。修法是补测试替身（一行）并在注释里写清原因。
3. **多模态 chunk 的 `content` 可能是 `list`**：旧代码直接当字符串 yield，SSE 会序列化出
   **数组**给前端（此前没暴露是因为一路都是纯文本模型）。已收口为 `_chunk_text()`。

### 16.3 Android 宿主（本轮）

端侧宿主（当时在独立 repo，**2026-09-18 已并入 `apps/android/`**，见 §17）第一批可运行代码：
Kotlin + Compose，Gradle 9.2.1 + AGP 9.0.0 + Kotlin 2.2.10（版本组合按本机已缓存工具链选定）。

| 模块 | 位置（新 repo 内） | 说明 |
|---|---|---|
| 平面路由 | `route/PlaneRouter.kt` | 与服务端 `plane_router.py` **同口径**：token 估算、档位→模型、预算、6 类信号、退化判定（≥40 字符） |
| 流式前缀守卫 | `route/StreamGuard.kt` | 先攒 60 字符再判，命中则丢弃前缀改道云端；`json_invalid` 不参与前缀判定 |
| SSE 解析 | `net/SseParser.kt` | 对齐 `chat_stream` 四类事件 + `done.meta.execution` → 执行位置徽标 |
| 设备工具 + 权限闸门 | `tools/` | 三道闸门（未注册/缺参数/未授权）+ 权限审计（越权拦截率的唯一来源） |
| 工具调用解析 | `tools/ToolCallJson.kt` | 宽容解析，把"意图对"与"语法对"分开统计 |

**已验**：`./gradlew :app:testDebugUnitTest` = **54 用例全绿**；`:app:assembleDebug` 产出 APK（17.9MB）。
**构建期抓到的真缺陷**（值得记一笔）：端侧输出预算最初照抄服务端的 300，
而 `chat` 角色预期输出 400 → **每一次聊天都被判去云端**，"端侧优先"名存实亡。
单元测试当场抓住，改为 512（依据 M0 实测 2b 86–116 tok/s），并加回归测试钉住默认值组合。

三个构建坑也一并记进了新 repo 的 README：AGP 9 **自带 Kotlin 支持**（再叠 `kotlin.android`
会报 `Cannot add extension with name 'kotlin'`）、`kotlin{}` 必须写在 `android{}` 外面、
本机只有 `android-36.1` 故需 `compileSdkMinor = 1`。

### 16.4 Android 宿主：模拟器真机 E2E（2026-09-18）

**14/14 全绿**（`adb shell am start --ez selftest true` + logcat；端侧走宿主机 Ollama，
云端走本地 SEKB）：端侧可达 / 三档模型在位 / 预热 / 路由决策 / 真实流式
（**预热后 TTFT 73–178ms，预热前 1780ms**）/ 权限拦截 / 无权限工具可用 /
审计越权拦截率 50% / 约束模式产出合法工具 JSON / 云端登录 / 设备接入（ttl=720h）/
聊天 SSE（106 字符 + `execution=cloud` + thinking 事件）/ 路由事件上报 / 统计查询
（`by_role` 里出现 `chat:2` → **设备上报的事件确实落库**）。

**这套 E2E 抓到两个只有真客户端能暴露的缺陷**：
1. 客户端按 OAuth 习惯读 `access_token`，而 SEKB 的 `LoginResponse` 是 `token` →
   服务端 200 OK、客户端报"登录失败"（单测喂的是**我以为**的响应体，所以照样全绿）；
2. **SEKB 流式路由事件缺 `model`** → 响应里 `execution.model` 为空。
   根因：流式路径在创建实例**之前**就写事件，只能退回配置里的模型名。
   修法：新增 `_resolved_model(role, plane)`（按平面**事先**解析，不依赖实例缓存），已修并补回归测试。

### 16.5 验收数字：工具调用 JSON 合法率（约束解码 ON/OFF）

结论先说：**在这批条件下约束解码没有可测量收益**，初始假设被推翻。
40 次调用（2B/4B × 易档/难档 × ON/OFF，各 10 条提示词）**全部 100% 合法、零工具名幻觉**；
延迟差在噪声范围（2B 190/183ms、4B 358/361ms）。难档已去掉"只调用工具"的明示指令，
2B 依然是 100%——所以"小模型必须靠 grammar"不成立。
**建议：先不为 grammar 付工程复杂度**，把 `response_format` 留作开关，等换更弱模型再验。
局限：系统提示仍给了完整 schema、每条件只跑 1 次（无方差估计）、工具集仅 3 个且不冲突。

### 16.6 UI 验收（2026-09-18）与它抓到的两个 UI 缺陷

自检走的是"编排器直连"，**绕过界面**；所以另做了一遍真实点击验收（`adb input tap`
+ `uiautomator dump` 定位控件 + 截图目视）：界面上完成接入（`设备：f26c077a59704b52`）、
发送、拿到回答，气泡上显示 **「本机完成 · qwen3.5-4b」** 与 `最近决策：edge · edge_preferred`。
截图存端侧仓库 `docs/screenshots/m2-ui-e2e.png`。

**抓到的两个真缺陷**（都已修）：
1. **密码框明文显示**——验收截图里密码白纸黑字可见（截图/投屏/旁人一瞥即泄漏）。
   `OutlinedTextField` 默认不做视觉转换，必须显式 `PasswordVisualTransformation`。
2. **徽标漏报"客户端侧升级"**——客户端因 `edge_unavailable` 改道云端时，
   服务端回传的 `execution.escalated=0`（它只统计**服务端内部**的升级），
   界面于是显示成平平无奇的"云端完成"，用户不知道自己的问题在端侧失败过。
   修法：徽标同时看客户端侧结果，并补 5 条 `BadgeTest` 钉住文案。

### 16.7 M2 待办

- [x] ~~Android 宿主第二/三批（凭证存储、传输抽象、两个客户端、编排器、UI、装配）~~ → 5 个提交，
      单测 91 用例全绿
- [x] ~~模拟器联调：端侧真实流式 / enroll / SSE / 上报落库~~ → §16.4（14/14）
- [x] ~~工具调用 JSON 合法率（约束解码开/关）~~ → §16.5
- [x] ~~越权拦截率~~ → 50%（1 拦截 / 2 调用；`READ_CONTACTS` 未授权路径）
- [x] ~~UI 人工走一遍~~ → §16.6（并抓到两个 UI 缺陷）
- [ ] **断网可用性**（飞行模式下端侧链路是否仍可用）——尚未测（不在 M2 验收数字之列，
      M3 评测项）
- [x] ~~**独立 repo 建仓 + push mirror**~~ → 2026-09-18 完成：Gitea `bo/sekb-ondevice-agent`
      （私有）+ GitHub `bozhang1214/sekb-ondevice-agent`（公开）+ 推送镜像（8h + 提交即同步），
      `gitea_mirror.py status` 五仓全绿；GitHub 侧用 **API** 核对到同一 commit
      （⚠️ 不能用 `git ls-remote https://github.com/...`：本机有 `insteadOf` 会把它重写成 Gitea，
      详见 `docs/ops/12-GITEA.md`；另 `sync_on_commit` 不保证及时，必要时显式触发同步）
- [ ] M3：llama.cpp NDK 真·端侧推理 + 真机性能数字

---

## 17. 仓库形态修正：从"端侧独立成仓"到 monorepo（2026-09-18）

### 17.1 为什么改 D2

原决策（D2）是端侧独立成仓。实际跑完 M2 后，业主提出三点，逐条成立：

1. **"希望用户一次 clone 拿到全量代码，按需编译各端"** —— 多仓 + 子模块会把这件事
   变成需要说明书的操作（本项目 `jobcopilot` 子模块已有先例，AGENTS.md 明确记着
   "子模块指针是全局单点：谁改了指针，别人一 pull 就跟着变"）。
2. **"后续一定支持鸿蒙、iOS"** —— 端越多，独立仓的维护成本越高（每加一端就要
   再建一个仓、再配一次镜像、再造一套跨仓同步）。
3. **协议是共享契约** —— `docs/ops/16-端云协同协议.md` 与三端实现必须在**同一个提交**里改，
   否则必然出现"文档说 A、代码做 B"。

### 17.2 改成了什么

| 项 | 决定 |
|---|---|
| 代码位置 | SEKB 主仓库 `apps/android/`（原 `sekb-ondevice-agent` 独立仓**冻结为只读**） |
| 历史 | 完整保留：用 `git merge -s ours --allow-unrelated-histories` + `git read-tree --prefix` 导入，合并提交有两个父提交（原仓 6 个提交可达）。⚠️ `git log --follow <新路径>` **跨不过导入边界**，查旧历史要用原路径或合并提交的第二父提交 |
| 多端目录 | `apps/android/`（可用）、`apps/ios/`、`apps/harmony/`（占位 + 开工须知） |
| 跨端纯逻辑 | 目标 `shared/`（Kotlin Multiplatform）：`route/` `net/` `tools/` `chat/` `eval/` 这批**不 import `android.*`** 的代码搬家即可，不是重写。抽出的时机 = 开始做 iOS 时 |
| 鸿蒙例外 | ArkTS 不能复用 Kotlin → 三条路线（ArkTS 重写 + 契约测试 / C 核心 + 三端绑定 / 瘦客户端）见 `apps/harmony/README.md` |
| 镜像 | 新代码随 `sekb` 仓库镜像（Gitea → GitHub）；原 `sekb-ondevice-agent` 镜像保留但不再更新 |

### 17.3 附带收获：构建不再需要仓库外的写权限

端侧代码进仓库后，把**构建状态**也收进仓库即可彻底摆脱"每次构建都要授权"：

| 变量 | 指向 | 为什么 |
|---|---|---|
| `GRADLE_USER_HOME` | `.tooling/gradle-home` | Gradle 默认写 `~/.gradle`（本机 1.4G） |
| `ANDROID_USER_HOME` | `.tooling/android-home` | AGP 要在这里生成 **debug.keystore**，否则 `assembleDebug` 直接失败 |
| `ANDROID_AVD_HOME` | `.tooling/android-avd` | 模拟器 AVD（可选搬入，4.4G） |

统一入口 `scripts/android.sh`（`test` / `assemble` / `install`）与 `scripts/emulator.sh`。

**实测结果（零额外授权）**：`test assemble` 通过并产出 APK；模拟器从仓库内的 AVD 启动到
`sys.boot_completed=1`；装包后跑端侧自检 **9/9 PASS**（真实 Ollama 流式、TTFT 294ms、
权限拦截率 50%、工具调用 JSON 合法）。**即：从构建到"模拟器里跑出端侧推理"全程没有
任何仓库外的写操作**。

踩出来的四个坑（都写进了脚本注释与 AGENTS.md §4.5，改脚本前先读）：
1. 不设 `ANDROID_USER_HOME` → `assembleDebug` 失败：`Unable to create debug keystore ... not writable`；
2. **同时**设 `ANDROID_PREFS_ROOT`（哪怕同一路径）→ AGP 9 崩在
   `AndroidLocationsBuildService ... AndroidDirectoryCreator`；
3. 模拟器把 jwk 写到 `$HOME/Library/Caches/TemporaryItems`，**写不进去会直接 Abort trap: 6**
   （不是降级）→ 必须把 `HOME`/`TMPDIR` 也指到仓库内；
4. adb 密钥必须与 AVD 里授权的那把一致（AVD 存的是**创建它时**的 `~/.android/adbkey`）——
   换了 `HOME` 会变成 `unauthorized`，装不了包；另外被杀掉的模拟器会留下 `*.lock`
   导致 "Running multiple emulators with the same AVD"。

### 17.4 工具链现状（本机实测，决定"哪些端现在就能做"）

| 端 | 工具链 | 结论 |
|---|---|---|
| Android | Android Studio（JBR 21）+ SDK platform 36.1 + AVD arm64 | ✅ 可构建可跑（已验证） |
| iOS | **Xcode 26.6** + iOS 26.3/26.4 模拟器运行时 + swift/swiftc | ✅ 可做，但 `xcode-select` 指向 CommandLineTools → 需 `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer` |
| 鸿蒙 | DevEco Studio（`Contents/sdk/default`、`tools/hvigor`、`tools/ohpm`、内置 node/jbr） | ✅ 命令行可构建（hvigor + ohpm），不必只用 IDE 界面 |

### 17.5 已定（2026-09-18 业主确认）

| 事项 | 结论 |
|---|---|
| 原独立仓 `sekb-ondevice-agent` | **删除**：Gitea 侧已删（复核 404）；GitHub 侧**已归档只读**（token 缺 `delete_repo` 权限，删除需业主在 UI 操作或给令牌加该权限） |
| 目录命名 | `apps/<平台>` ✅ |
| CI | 给 `apps/android` 加 path 过滤的任务；服务端改动不触发它（见 §17.6） |
| 下一步 | 真机未到 → 先做**端侧 RAG**（见 §19） |

---

## 18. M3 第一批：端侧 RAG（2026-09-18 起）

真机未到，先做端侧 RAG（业主指定）；模拟器只验**功能与协议**，性能数字留真机。

### 18.1 最要紧的一条：向量空间必须与云端一致，否则不许混用

§4.5-F 已经定了规则：**`embedding_space` 不一致 → 重算，不要复用缓存**。端侧 RAG 是这条规则
第一次真正被用到，所以先把话说死：

| 端侧嵌入实现 | 向量空间戳 | 与云端向量能否混用 |
|---|---|---|
| ONNX 版 `bge-small-zh-v1.5`（512 维，**目标实现**） | `BAAI/bge-small-zh-v1.5@512` | ✅ 与云端同空间 |
| 宿主 Ollama 的其它嵌入模型（开发便利） | 如 `bge-m3@1024` | ❌ **禁止**：维度与语义都不同，混用会返回"看起来相关但其实错"的结果 |
| 测试用确定性桩 | `stub@N` | ❌ 仅测试 |
| int8 量化版（同权重，`quantize_dynamic`） | `…-int8@512` | ❌ **量化会改变向量空间**（实测逐条余弦 0.96–0.97、分数分布上移、阈值需从 0.4 重标到 0.5） |

> 量化版的实测（体积 1/4、排序不变、设备嵌入 19ms vs fp32 33–38ms）见 §18.4 与端侧
> `docs/RETRIEVAL-EVAL.md` §3.5。**换空间必须原地重算索引**（`SqliteVectorStore.reembed`），
> 这条链路已在模拟器上端到端验证。

实现要求：索引里存 `embedding_space`；检索前先比对——不一致就**只在本机检索**并给结果打标，
不参与跨端融合。这条不写成"注意事项"，而是**代码里的分支**（下一轮落地时补测试钉住）。

### 18.2 分层（与 M2 的适配层同思路）

| 层 | 内容 | 实现策略 |
|---|---|---|
| 嵌入 | `EmbeddingProvider` | 可插拔：ONNX（目标）/ 宿主 Ollama（开发）/ 确定性桩（测试）。**必须能离线**，因为设备专属数据不许出端 |
| 切片 | `Chunking` | 纯函数：按段落 + 长度上限切分，带重叠；中文按字符数而不是 token（端侧不做子词统计） |
| 存储 | `VectorStore` | 首批：SQLite 表 + 暴力余弦（10k 段 × 512 维 ≈ 20MB，实测延迟足够）；**预留** sqlite-vec / HNSW 升级路径 |
| 检索 | `Retriever` | 查询 → 嵌入 → top-k → 相似度阈值 → 结果；空结果/低分是**升级信号**来源 |
| 接入 | 设备工具 `kb_search` | 模型可调用；同时要同步**服务端的 `DEFAULT_AVAILABLE_TOOLS`**（否则云端会把 `kb_search` 判成工具幻觉） |

### 18.2.1 索引的输入：本机文档与 PDF（2026-09-20 落地）

索引的输入从"内置样例"扩展到**用户自己的本机文档**：

| 输入 | 处理 |
|---|---|
| 纯文本（txt/md/json/csv） | SAF 读取 → 直接切片（≤2MB） |
| **PDF** | `PdfBox-Android`（Apache-2.0）抽**文本层** → 切片（≤20MB，抽取文本 ≤40 万字符） |
| 扫描件 PDF（无文本层） | **拒绝并如实说明需要 OCR**——不塞空内容/图片进索引（脏索引比空索引更糟） |
| 加密 PDF / 解析失败 | 拒绝并带上可读原因 |
| Word/Excel | 不在本期（见 BACKLOG） |

三个实现要点（都是踩出来的）：
1. **PdfBox 必须先 `PDFBoxResourceLoader.init(context)`**：资源在 aar 的 assets 里，
   没初始化会抛 `ExceptionInInitializerError`——它是 **Error 不是 Exception**，
   `catch (Exception)` 拦不住，会把线程干掉；所以抽取器捕获 **Throwable** 并转成可读结果。
2. **先判 PDF 魔数、再判二进制**：PDF 里必然有二进制字节，顺序反了会把所有 PDF 拒掉（有回归测试）。
3. **PDF 判定看 `%PDF` 而不是扩展名**：用户从聊天软件存的文件经常没有扩展名。

### 18.3 隐私与升级的关系（这一节决定端侧 RAG 的价值）

- **设备专属集合**（`device_only`）：嵌入与检索**只在端侧**，检索为空就如实说"本机资料里没有"，
  **绝不**为了答得更好把原文送去云端嵌入——这是 §5.2 硬边界在 RAG 上的具体形态。
- **非专属集合**：端侧检索为空/低分时，可升级到云端 RAG（走既有升级机制，带交接摘要）。

### 18.4 验收（模拟器阶段）——**已完成（2026-09-19）**

| 指标 | 实测（12 篇语料 / 33 条问题，ONNX bge-small-zh，模拟器） |
|---|---|
| 检索命中率 | **Hit@1 87%（26/30）、Hit@3 100%、MRR 0.928** |
| 延迟 | 嵌入 **38ms**（p95 57ms）、检索 **0.5ms** |
| **阈值标定** | 0.2→误召回 3/3（全部强行回答）；**0.4→误召回 0/3 且命中率不降**；0.6→开始伤召回。**默认阈值据此定为 0.4** |
| 空间一致性 | 端侧 `BAAI/bge-small-zh-v1.5@512` == 云端 → `cloudCompatible=true`；不一致时走"仅本机检索"分支（有单测与自检） |
| 端云协同 | 空检索/低分 → 升级信号可用；设备专属集合 `deviceOnly=true` 时非本机嵌入被拒（自检 PASS） |

评测工具：`apps/android/.../eval/RetrievalEvalSet.kt`（标注集）+ `RetrievalEvalRunner.kt`
（指标 + 标定曲线），入口 `--ez evalrag true`；完整数字与边界见
端侧仓库 [`docs/RETRIEVAL-EVAL.md`](../apps/android/docs/RETRIEVAL-EVAL.md)。

**这套评测的边界**：12 篇短文档、33 条问题，足以发现"阈值定错"量级的问题，
不足以支撑"准确率 87%"这类结论；性能数字来自模拟器，不是真机。

### 18.6 排查记录：一次"空洞的验证"与一个坏掉的权重缓存（2026-09-18）

端侧 ONNX 嵌入接上后，检索出现荒唐现象：**任何提问都命中同一篇文档，余弦恰好 1.000**。
排查链条值得完整记下来，因为它暴露的是**验证方法**的问题，不只是环境问题。

| 步骤 | 做法 | 结果 |
|---|---|---|
| 1 | 查分词器：把设备上的 token id 与 HuggingFace 金标准比对 | ✅ 分词器正确（不是它的锅） |
| 2 | 主机验证 ONNX 与 `sentence-transformers` 逐条比对（4 种长度） | ⚠️ 余弦全 **1.000000** —— 当时当成"导出成功" |
| 3 | 反证：换 `distilbert` 做同样测量 | CLS 余弦 0.86（有区分度）→ **bge 的 1.000000 不正常** |
| 4 | 分别用 `model.safetensors` 与 `pytorch_model.bin` 加载 bge | safetensors → **1.000000**；bin → **0.243864** ✅ |
| 5 | 查云端（生产容器内同一模型） | safetensors → **0.243864** ✅ 与本地 bin 一致 |

**根因**：这台 Mac 的 HF 缓存里 `model.safetensors` 是**退化权重**（体积正常 95.8MB、
snapshot 同名，但权重塌缩）。我的 ONNX 是从它导出的，所以把"所有向量几乎一样"
带进了端侧。云端缓存是好的，**生产 RAG 不受影响**。

**修法**：导出显式 `use_safetensors=False`（用 bin 权重）；`scripts/fetch_embedding_model.sh`
的验证步骤**增加"区分度检查"**——两段无关文本的余弦必须明显小于 1。

**这条最值得记**：第 2 步的"与参照实现一致"看起来是强验证，其实是**空洞的**——
ONNX 与 sentence-transformers 都不约而同地用了同一份坏权重，所以两边"完全一致"。
**与参照实现一致只能证明"两边做了同样的事"，不能证明"这件事是对的"。**
验证必须包含**可被证伪的性质**（这里是区分度：无关文本必须不相似）。

落地后的实测（模拟器）：嵌入区分度 0.244（与云端 0.243864 一致）、
查询"端侧 RAG 为什么隐私更好"→ 命中 `doc-rag` 得分 **0.696**、嵌入 36ms、检索 <1ms。

**遗留**：验证中有 1 条文本（`"端侧 RAG 把知识索引放在设备上，检索不出网。"`）
与 sentence-transformers 余弦 **0.958**（其余 3 条 1.000000）。影响有限（远高于阈值），
但尚未定位——列为 §13 的跟踪项。

### 18.7 Android 侧的两个环境约束（都已写进脚本）

| 约束 | 现象 | 处理 |
|---|---|---|
| ONNX Runtime 版本 | **1.30.0 在模拟器上 SIGILL**（`ILL_ILLOPC`，模拟器 CPU 未暴露 `i8mm`；`asimddp/bf16` 有） | 固定 **1.20.0**（实测可用）；真机可再评估更高版本 |
| 模型怎么推进设备 | `adb push` 到外部私有目录后**属主是 shell**（`drwxrws--- shell:ext_data_rw`），App 读不到 → 表现成"模型在但找不到" | `adb shell run-as <pkg> sh -c 'cat > <绝对路径>'`（整条命令必须是一个字符串，否则 `>` 由设备 shell 用户解释，报 Permission denied） |

---

## 相关文档

- [RFC-端云协同与端侧Agent.md](./RFC-端云协同与端侧Agent.md) —— **设计、决策与里程碑**（本文是它的实施记录）
- [ops/16-端云协同协议.md](./ops/16-端云协同协议.md) —— 端侧宿主 ↔ SEKB 接口契约
- [apps/README.md](../apps/README.md) —— 多端目录、按需编译、跨端逻辑分层
- [apps/android/docs/VERIFICATION.md](../apps/android/docs/VERIFICATION.md) —— 端侧验证记录
- [apps/android/docs/RETRIEVAL-EVAL.md](../apps/android/docs/RETRIEVAL-EVAL.md) —— 检索评测与阈值标定
