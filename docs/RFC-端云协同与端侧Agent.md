---
title: 端云协同与端侧 Agent（Android 宿主）实现方案
layer: 设计层
owner: SEKB Team
status: confirmed
version: v0.2.0
last-updated: 2026-09-17
based-on-commit: 9f058b3
related: [docs/RFC-自迭代闭环设计, docs/ops/15-MCP-ENDPOINT, docs/tech/03-MODULES]
---

# 端云协同与端侧 Agent（Android 宿主）· 实现方案（待确认）

> **本文回答什么问题**：把 SEKB 改造成「端云协同」应用——端侧跑本地小模型、云端保留重推理——具体怎么做、
> 分几步、每步的交付物与验收数字是什么。
> **适合谁读**：owner（拍板与验收）、实现者（Android 侧 + SEKB 侧）。
> **状态**：**D1–D8 已由 owner 拍板确认（2026-09-17）**，本文为实现基线。v0.2.0 新增：
> ① Mac 端侧提前到第一阶段的决策与依据（§1.1）；② Ollama 模型清单（§2.5）；
> ③ **端云协同最难的问题——状态一致性模型**（§4.5）；④ 无真机情况下的验证策略（§9.1）；
> ⑤ 里程碑按「Mac 先行 → Android 模拟器验功能 → 真机测性能」重排（§9）。
> 初版依据本地讨论区的《端侧 Agent 部署方案（2026-09-17）》，那份文档不进版本库，以本文为准。

---

## 0.0 已确认的决策（2026-09-17）

| # | 决策 | 结论 |
|---|---|---|
| D1 | 鸿蒙本期做吗 | **不做**（列为 M3 后 stretch；鸿蒙不兼容 APK = 第二套 App + 第二套推理栈，额外 2–4 周） |
| D2 | 仓库形态 | ~~独立 repo `sekb-ondevice-agent`~~ → **修正为 monorepo**：端侧并入 SEKB 主仓库的 `apps/`（见 §17，2026-09-18）；SEKB 只加 §8 的 S1–S3 |
| D3 | 云端对话入口 | 走**已有的** SEKB REST/SSE（`/api/v1/chat` + `/stream`），不给内核加 chat 工具 |
| D4 | 设备身份 | **设备级 token**；共享 `JOBCOPILOT_HTTP_TOKEN` 与 LLM Key **绝不进 APK** |
| D5 | 端侧 RAG | **直接上向量**（bge-small-zh INT8 + sqlite-vec），与云端同 512 维空间；跳过 BM25 |
| D6 | 默认模型档 | **2B 档**起步；3–4B 作为对比档 |
| D7 | 是否改 SEKB LLM 层做「云端内部分级」 | **暂不做**（§8 三处硬约束；且客户端路由已能覆盖题眼） |
| D8 | 评测集 | 用自有 JD/简历场景造 **20–50 条**标注集，进作品 repo |
| **D9（新增）** | **Mac 端侧是否做** | **做，且提前到第一阶段**。依据：owner 有 MacBook Pro（M5 Pro/48GB）；实测代码里 `llm_factory.py:500` **已经在下发 `base_url`** → 接 Ollama 的 OpenAI 兼容端点是**配置级改动**，不是重构（见 §1.1） |
| **D10（新增）** | **没有 Android 真机怎么办** | **模拟器验功能，真机测性能**（见 §9.1）；性能数字在 Mac 上先拿到，Android 真机数字最后补 |
| **D11（新增）** | Ollama 模型缓存目录 | 由 owner 提供/设置（`OLLAMA_MODELS`）；起步三档约 16GB，见 §2.5 |

---

## 0. 这份方案与前一版的差异（先看差异，再看细节）

| # | 前一版 | 本方案 | 为什么改 |
|---|---|---|---|
| 1 | 以「隐私敏感走端侧」为**核心卖点** | 以**设备独有数据 + 延迟**为端侧支点；隐私降为**数据分级边界** | owner 已把优先级定为 **延迟 > 成本 > 隐私 > 离线**。而且通讯录/定位不是"隐私所以放本地"，是**云端根本拿不到**——这是能力问题 |
| 2 | 云端只用 **MCP** | **两条协议**：MCP 只承载工具；对话/多智能体走 SEKB 的 REST + SSE | jobcopilot MCP 只暴露 **7 个工具**，没有"对话/多智能体"入口；把 SEKB 的 LangGraph 对话硬塞进 MCP 是自找麻烦 |
| 3 | 未提端侧**性能预算** | 新增 §2：prefill / KV cache / 上下文预算 / prefix cache | 端侧延迟的真正决定因素，前一版完全没有 |
| 4 | 端侧 RAG「先 BM25 再向量」 | **直接上小 embedding + 向量**，且**与云端同一向量空间** | BM25 在中文要不小的分词成本；而 bge-small-zh 端侧只要几十 MB，还能和云端共用向量空间做分层检索 |
| 5 | 未提鉴权 | **设备级 token**，明确"共享 token / LLM Key 绝不进 APK" | 现在只有 `JOBCOPILOT_HTTP_TOKEN` 一个共享令牌；打进 APK 等于泄露 |
| 6 | 里程碑：M2 同时做 RAG + 端云协同 | **端云协同提前到 M2，RAG 移到 M3** | 面试题眼是"端云协同与模型路由"，不是端侧 RAG |
| 7 | 鸿蒙与 Android 并列 | **Android 先行；鸿蒙单列为需拍板的 stretch** | 鸿蒙（NEXT/6）不兼容 APK，等于第二套 App（ArkTS/ArkUI）+ 第二套推理栈，不是"同一份代码编两遍" |

---

## 1. 目标与已确认的边界

**优先级（owner 2026-09-17 定）**：**延迟 > 省成本 > 隐私 > 离线**。
**离线**：**不是要求**（端云协同允许在线）。
**隐私**：只有**云端拿不到的数据**构成硬边界（设备侧数据：定位精确值/通讯录/相册/通知），
简历等文本数据**允许出网**（优先保证质量）。
**首要目标**：把端云协同**落实到项目上**，成为可展示的作品（端侧 Agent 基础设施方向）。

**明确不做**（诚实边界，与前一版一致）：
- ❌ 端侧跑重推理（批量分析、长上下文深度合成、资讯日报生成）
- ❌ 端侧模型微调/训练
- ❌ iOS / Flutter / KMP（Android 原生先行，纯 Kotlin 模块留复用口）

---

## 1.1 为什么 Mac 端侧提前到第一阶段（D9）

**因为它是唯一能立刻拿到真实数字的端侧，而且接入成本几乎是配置级。**

| 事实 | 证据 | 含义 |
|---|---|---|
| SEKB 的 LLM 工厂**下发 `base_url`** | `backend/app/core/llm_factory.py:500`（`"base_url": self.config.llm.base_url`） | 把 `llm.base_url` 指向 `http://127.0.0.1:11434/v1` 即可接 Ollama |
| 代码里已有「OpenAI 兼容自定义端点」先例 | `backend/app/tools/image_processor.py`（视觉模型走 `ChatOpenAI` + 自定义 `base_url`） | 这条路已被验证可行，不是新发明 |
| Ollama 提供 OpenAI 兼容 API | `http://localhost:11434/v1` | 客户端/服务端都不需要新 SDK |
| 但 `base_url` 是**全局单点** | `backend/app/core/config.py::LLMConfig` | 只能"整个 SEKB 走本地"或"整个走云"，**不能按角色分流**（这正是 D7 暂不做 S5 的代价；MAC 阶段用"两套 profile"绕过） |

**Mac 阶段的两种用法（都不需要改 SEKB 架构）**：
1. **全本地 profile**：`llm.base_url` → Ollama + 11 个角色的 model 换成 `qwen3.5:4b-mlx` →
   得到「**完全离线可用的 SEKB**」，这是端侧最硬的展示；
2. **对照实验**：同一问题分别在「全本地 profile」与「全云 profile」上跑，产出 §9.1 的对比数字
   （延迟/质量/成本），**这就是端云协同的论证材料**。

> 客户端路由（端 or 云）**在 Mac 阶段同样成立**：它住在宿主进程里，与平台无关。
> 而 SEKB 侧保持不动 —— 这也印证了 D7「暂不做 S5」是自洽的。

---

## 2. 端侧性能预算（本方案的地基）

> 这一节是「端侧能不能做」的物理约束。数字与推导已在本轮对话中给 owner 讲过，这里只留结论。

### 2.1 两条规律

| 阶段 | 瓶颈 | 谁决定快慢 | 对设计的要求 |
|---|---|---|---|
| **Prefill**（读 prompt） | **算力**（并行矩阵乘） | CPU/GPU 核心数、NPU | 长 prompt 的**首字延迟**靠算力；端侧要**控 prompt 长度** |
| **Decode**（逐 token 生成） | **内存带宽** | `tok/s ≈ 带宽 ÷ 每 token 需读字节 × 0.6~0.7` | 端侧**输出越长越吃亏**；上下文越长越慢（KV 也要读） |

### 2.2 KV cache：端侧长上下文的真正天花板

```
KV 每 token 字节 ≈ 2(K+V) × 层数 × KV头数 × head_dim × 精度字节
```

以 8B 级模型（36 层 / 8 个 KV 头 / head_dim 128 / fp16）为例：**≈ 144 KB/token**
→ 8K 上下文 ≈ 1.2 GB；32K ≈ 4.6 GB；128K ≈ 18.4 GB；**262K ≈ 37.7 GB**。

**结论（写进设计约束）**：
1. 端侧**不要相信"原生 262K 上下文"**——KV 才是内存杀手；
2. 端侧会话上下文预算设 **2K~4K token**，超预算走**滚动摘要**；
3. KV 量化（Q8）可再省一半，但要实测质量；
4. 上下文 8K→32K，decode 大约**掉一半**（KV 读占带宽）。

### 2.3 端侧参考速度（区分两类设备，别互相套用）

| 设备 | 带宽量级 | 2B Q4 decode | 3–4B Q4 decode | 用途 |
|---|---|---|---|---|
| **Mac（M5 Pro 48GB）** | 350 GB/s | ~110 tok/s | 8B 实测 **61 tok/s** | 开发机 / 桌面端宿主 |
| **旗舰 Android 手机** | ~60–70 GB/s | ~20–40 tok/s | ~10–20 tok/s | 端侧宿主（**以实测为准**） |

> ⚠️ 手机上的数字**必须自己测**：同一颗 SoC，走 CPU（llama.cpp）、GPU（Adreno OpenCL/Vulkan）、
> NPU（QNN/Genie，需要模型专门量化）差异可达数倍；网上"100+ tok/s"通常是 NPU 专用产物。

### 2.4 端侧推理层设计

| 项 | 决定 | 理由 |
|---|---|---|
| 运行时 | **llama.cpp（NDK/JNI）** | Android 最成熟、GGUF 生态最全、**支持 GBNF grammar 约束解码** |
| 量化 | **Q4_K_M**（KV cache 可 Q8） | 4bit 让 decode 快近一倍；小模型别用 Q2/Q3 |
| 上下文 | 2–4K + 滚动摘要 | 见 §2.2 |
| **prefix cache** | **必做** | ReAct 每跳都带同一前缀；不复用就要**重算 prefill**，是端侧延迟第一杀手 |
| 工具调用 | **grammar/JSON schema 约束** + 严格 function-calling 模板 | 小模型自由生成必翻车；这是端侧工具调用能否稳的关键 |
| NPU | **不在 M0–M2 范围内**，M3 后作为优化项 | NPU 需要 QNN/Genie 等专用产物，投入产出比低、风险高 |
| **思考模式（think）** | **短任务必须关**（Ollama `think: false`） | 实测同一意图分类任务：开思考 **190 token / 1926ms**，关思考 **6 token / 96ms**，答案完全相同——**20 倍延迟差**。端侧白烧 10–30 倍算力 |
| **冷启动预热** | 首次调用某模型要**加载权重**（2B 实测 **~6.5s**），之后同模型 0.1–0.7s | 实测：同一 supervisor 任务，冷启动 6547ms vs 预热后 222ms。端侧宿主应在启动/空闲时预热当前档位模型，否则「端侧赢延迟」在第一次调用上完全不成立 |

**模型档位（可下载）**：

| 档 | 模型（建议） | 体积(Q4) | 职责 |
|---|---|---|---|
| tiny | Qwen3.5-0.8B / Qwen2.5-0.5B | ~0.3–0.6 GB | 意图分类、路由（超低端机） |
| **default** | **Qwen3.5-2B** / Qwen2.5-1.5B | ~1.0–1.4 GB | 意图分类 + 工具选择 + 短答 |
| large | Qwen3.5-4B / Ministral-3-3B（**function calling 取向**）/ Gemma-3n-E2B | ~2.0–2.6 GB | 本地知识问答、轻总结 |

> 选型要**实测**而不是看参数：用 §9 的 function-calling 测试集比"工具名+参数 JSON 合法率/正确率"。
> 注意小模型的 4bit 精度损失**比大模型更明显**，所以档位不能一味压小。

---

## 2.5 Mac 端侧模型清单（Ollama，实测可用 tag）

> 下面每个 tag 都是在 ollama.com 上核对过的（2026-09-17）；`-mlx` 是 Apple Silicon 的 MLX 优化版本，
> **Mac 上优先用 MLX 版**（同一模型比 GGUF 路径更适合统一内存）。Qwen3.5 全系为**多模态 + 256K 上下文 + 支持 tools**。

**起步三档（合计约 16GB，建议先只拉这三个）**

```bash
ollama pull qwen3.5:2b-mlx     # 3.1GB  对照档：与 Android 端同规模，用于跨端对比
ollama pull qwen3.5:4b-mlx     # 4.0GB  默认主力：路由/工具选择/短答/轻总结
ollama pull qwen3.5:9b-mlx     # 8.9GB  质量档：本地知识问答、较长输出
```

**可选：能力档（展示"Mac 端侧也能跑大模型"）**

```bash
ollama pull qwen3.5:35b-mlx    # 22GB  MoE（35B 总参 / 小激活）——48GB 可常驻，解码接近小模型速度
```

| 用途 | 建议 tag | 体积 | 说明 |
|---|---|---|---|
| 与 Android 对齐的对照档 | `qwen3.5:2b-mlx` | 3.1GB | 跨端"同模型不同算力"的对比基准 |
| Mac 默认 | `qwen3.5:4b-mlx` | 4.0GB | 端侧路由/工具调用主力 |
| 质量档 | `qwen3.5:9b-mlx` | 8.9GB | 端侧能做"更像样"的回答 |
| 能力档（可选） | `qwen3.5:35b-mlx` | 22GB | MoE，慢一点但质量跳档；用来证明"端侧不是只能跑小模型" |
| ~~embedding~~ | 见下方说明 | — | **不要用 Ollama 做端侧 embedding**，见下 |

> ⚠️ **embedding 的坑（重要）**：Ollama 有 `qwen3-embedding:0.6b/4b/8b`，但它们的向量维度
> （1024/2560/4096）**与 SEKB 云端用的 `bge-small-zh-v1.5`（512 维）不一致** → 一旦用错，
> 「端侧粗检索 → 云端精排」这条分层链路就不成立（向量空间不同，无法比较）。
> 所以端侧 embedding **走 ONNX 版 bge-small-zh-v1.5（512 维）**，与云端**同空间**；Ollama 的
> embedding 只作为"纯端侧、与云端无关"的实验项。

**缓存目录**：Ollama 用环境变量 `OLLAMA_MODELS` 指定（默认 `~/.ollama/models`）。
起步三档 ~16GB 放内置盘即可；若拉 35B 档（+22GB）建议指向外置 SSD：
`export OLLAMA_MODELS=/Volumes/<你的SSD>/ollama-models`（写进 `~/.zshrc`）。

---

## 3. 总体架构

```
┌────────────── Android App（端侧宿主，Kotlin 原生）──────────────┐
│ UI：对话 / 工具调用可视化 / 路由日志（本次走端还是云、为什么）    │
│ ────────────────────────────────────────────────────────────  │
│ Agent Runtime（手写 ReAct loop）                              │
│  ├─ 端侧 LLM：llama.cpp + GGUF Q4（grammar 约束）              │
│  ├─ 端侧工具：定位/通讯录/相机/通知/剪贴板 + 权限与审计          │
│  ├─ 端侧 RAG：bge-small-zh(ONNX INT8) + sqlite-vec            │
│  └─ Router：端云决策矩阵 + 升级信号 + 路由日志                  │
│ ────────────────────────────────────────────────────────────  │
│ 协议层（两条，各司其职）                                        │
│  ├─ MCP Client（JSON-RPC / SSE）→ 云端**工具**                 │
│  └─ Chat Client（REST + SSE）  → 云端**对话/多智能体**          │
│ 鉴权：设备级 token（登录 SEKB 换取），**不含任何共享密钥**        │
└───────────────────────────┬───────────────────────────────────┘
                            │ HTTPS
┌───────────────────────────▼───────────────────────────────────┐
│ SEKB 云侧                                                      │
│  ├─ jobcopilot MCP 端点（7 工具）— 已存在，见 ops/15            │
│  ├─ 对话/多智能体：/api/v1/chat + /stream（SSE）— 已存在         │
│  └─ 【新增】设备身份 + 端侧路由事件上报 + 端侧可见的执行位置       │
└───────────────────────────────────────────────────────────────┘
```

**目录分层（为第二端留口，但不在本期做）**：
`modules/mcp-client`、`modules/chat-client`、`modules/agent-loop`、`modules/router`
（纯 Kotlin，无 Android 依赖）+ `app/`（Android 原生：llama.cpp NDK、设备工具、权限）。

---

## 4. 端云路由（**本方案的题眼**）

### 4.1 决策矩阵（可编码，不是口号）

| 信号 | 取值来源 | 判给端侧 | 判给云端 |
|---|---|---|---|
| 任务类型 | 端侧意图分类（tiny 档模型） | 分类/路由/短答/总结/改写 | 长文合成、批量、多跳规划 |
| 预估输出长度 | 意图→模板 | **≤ ~300 token**（2B 档 decode 86–116 tok/s） | **> ~500 token 且要求质量**（云端 158–218 tok/s） |
| 输入长度 | token 计数 | **≤ 2K**（923 token 实测 TTFT 427ms） | > 2–4K（云端 prefill 更稳） |
| 数据类别 | 见 §5.2 | `DEVICE_ONLY` 强制端侧、`SENSITIVE` 脱敏后可云 | `NORMAL` 自由 |
| 设备状态 | 电量/温度/可用内存 | 充足且未降频 | 电量<20%、过热、内存不足 |
| 网络 | ConnectivityManager | 离线 → 必须端侧（或直接拒答） | 在线 |
| 历史升级率 | 本地统计 | 该意图端侧成功率高 → 端侧 | 该意图经常升级 → 直接走云 |

### 4.2 升级（端→云）信号——必须可自动化

1. **JSON/grammar 校验失败**（工具参数不合 schema、字段缺失）
2. **工具名幻觉**（不在注册表里）
3. **输出退化**：空答案、循环重复、明显截断
4. **自评/弃答**：模型输出"无法确定"类标记，或置信度低于阈值
5. **超出延迟预算**：首 token 超过阈值（如 3s）或总时长超阈值
6. **上下文超预算**：需要更长上下文才能答（端上不该硬撑）

> 复用点：JobCopilot 内核已有"**7 段全空 = LLM 不可用**"这类退化检测思路（见 `docs/ops/15-MCP-ENDPOINT.md`），
> 端侧升级信号与它是同一类设计。

### 4.3 升级后的**一致性**（最容易做坏的地方）

- 升级**不是重来**：把端侧已得结论（意图、已选工具、部分事实）作为**结构化上下文**传给云端；
- 云端回答要标注"已包含端侧结论"，避免用户看到两次不同答案；
- **端侧 vs 云端的记忆**：会话历史以云端为准（owner 要云端备份），端侧只保留最近窗口 + 摘要。

### 4.4 可观测（验收依据）

每次请求落一条**路由日志**：`意图 / 决策(端|云|混合) / 决策依据 / 端侧耗时 / 云端耗时 / 是否升级 / 升级原因`。
本地可查 + 可选上报云端。**核心指标是「端侧完成率」与「升级率」**——这两个数字就是作品的说服力。

---

## 4.5 端云协同**最难的问题**：状态一致性（设计）

> owner 特别要求把这一节做透。结论先行：**端云协同的难点不在推理，而在「同一件事有两个版本时谁说了算」。**
> 它会以三种面目出现：
> ①「我在手机上说的，Mac 上为什么不知道」（状态不同步）；
> ②「它怎么突然换了语气 / 忘了前面」（上下文断裂）；
> ③「离线时记的笔记，联网后被覆盖了」（写入冲突）。
> 下面九条是对应的设计。

### A. 先钉死权威源（SSOT）——不先做这一步，后面全是补丁

| 状态 | 权威源 | 端侧的角色 | 冲突策略 |
|---|---|---|---|
| 对话消息 | **云端**（owner 要云端备份） | 最近窗口缓存 + 离线 outbox | **append-only → 天然无冲突** |
| 工具调用结果 | 实际执行方（端/云） | 端侧执行的结果上报 | `idempotency_key` 去重 |
| 用户画像 | **云端** | 只读缓存，**不写** | 端侧不产生写入 → 无冲突 |
| 云端知识库 L3 | **云端** | 只读子集镜像 + 版本戳 | 版本不符 → 重取 |
| 设备数据（定位/通讯录/相册） | **端侧（唯一持有者）** | 全部 | 不出端，无冲突 |
| 端侧本地知识（笔记/JD/简历） | **端侧** | 全部 | 用户明示才上行 |
| 任务中间状态 | **当前执行方** | 升级时随请求迁移 | 见 C |
| 用量 / 路由日志 | 双方各自记账 | 上报聚合 | 幂等 |

> **关键洞察（能省掉一整类复杂度）**：对话是**只追加**的，所以它**不需要 CRDT**——
> 只要"每条消息全局唯一 id + 服务端单调序号"就够。真正会冲突的只有**可覆盖字段**
> （画像、设置、会话标题）→ 用**版本号 + LWW + 冲突可见**即可。
> **别一上来上 CRDT**：那是给"同一字段多点并发编辑"准备的，当前需求没有它。

### B. 端侧离线写入：Outbox（发件箱）模式

1. 端侧所有"需要同步的事"先写本地 **outbox**（append-only，带 `event_id` / `device_id` / `client_ts`）；
2. 联网后按序上传；服务端按 `event_id` **幂等**（重复上传不产生副作用）；
3. 上传成功才删本地条目；服务端返回 `server_seq`，端侧据此对齐顺序；
4. 失败重试 + 指数退避；**界面上显示"待同步 N 条"**（绝不能静默丢）。

### C. 升级（端 → 云）：**结构化交接，不是重来**

```
POST /api/v1/edge/chat            （或复用 /api/v1/chat，带 edge 段）
{
  "session_id": "...", "device_id": "...",
  "escalation": {
    "reason": "json_invalid | degenerate | low_confidence | timeout | context_overflow",
    "edge_model": "qwen3.5:2b-mlx",
    "edge_steps": 3,
    "intent": "job_analysis",
    "tool_calls_done": [ {"name": "get_profile", "idempotency_key": "..."} ],
    "handoff_summary": "已确认用户关注支付方向；已取到画像；未完成：市场分析",
    "already_streamed_chars": 128
  },
  "messages": [ ... ]
}
```

三条硬规则：
1. **不重放已完成的工具调用**（按 `idempotency_key` 去重）——否则副作用执行两次；
2. **`already_streamed_chars` 告诉云端"用户已经看到多少"**，云端**从那里续写**，不要重头流式；
3. `handoff_summary` 作为**「已建立背景」注入**（不是新指令），保证模型"记得"端侧做过什么。

### D. 降级（云 → 端）：把"不可用"提前暴露

- 触发：网络不可达 / 云端 5xx / 云端超时 / 用户主动选"本机执行"；
- 云端要在**流式开始之前**就暴露不可用（否则用户看到半截答案，比直接失败更糟）；
- 降级后若任务超出端侧能力（长文合成、批量分析）→ **明确告知"此任务需要联网"**，
  绝不用弱模型硬答（这是体验崩塌最常见的来源）；
- 用户可见：每条回答标注 `执行位置：本机 / 云端`，可展开看"为什么"。

### E. 交接摘要（handoff summary）——解决"上下文断裂"

无论端→云还是云→端，**切换点必须注入一段结构化交接**：

```
<handoff from="edge" reason="timeout">
  意图 / 已完成步骤 / 已确认事实 / 未完成项 / 本会话内的用户偏好
</handoff>
```

- 由**切换前的一方**生成；端侧生成不了时，由云端从端侧上报的 `edge_steps` 里补；
- 它放在 **prompt 前缀**位置 → 切换后的推理能**命中 prefix cache**，不必重算全历史
  （这与 §2.4 的 prefix cache 是同一机制的两面）。

### F. 版本对齐：防止"同名任务、不同结果"

跨端交换的产物一律带版本戳：`prompt_version / model_id / embedding_space_version / tool_schema_version`。

- 不一致时：**拒绝复用、重算**（而不是静默用错结果）；
- 端侧每次上报都带版本，云端写进路由日志 → 出问题能立刻定位"是哪个版本组合"。

### G. 隐私边界要**可证明**，不能靠自觉

对 `DEVICE_ONLY`（§5.2）数据：
1. **代码级闸门**：Router 直接返回本地结果，**不存在"把它带出去"的代码路径**；
2. **统一出站出口 + 出站断言**：所有出站请求过一个出口，出口处断言请求体不含敏感模式
   （手机号/通讯录字段名等）；命中即拒绝并告警；
3. **自动化测试**：喂 `DEVICE_ONLY` 数据 → 断言出站请求体不含它（**这条进作品 repo 的 CI**）；
4. **审计日志**：每次"是否出端"落本地日志，用户可查、可导出。

### H. 跨端成本记账

端侧也要记 `tokens / 耗时 / 能耗估算`，并上报云端聚合。否则你优先级里的第 2 条
（省成本）**拿不出数字**——而"端侧省了多少"恰恰是最有说服力的展示。

### I. 多端并发：先用「分片」把最难的一类冲突删掉

MVP **不做**"同一会话在手机和 Mac 上同时编辑"。改为：

- 会话按 **`(user, device)` 分片**；跨端**只读查看**，需要时"导出到另一台继续"；
- 这样直接把"同一会话并发写"从设计里删掉；
- 等真有需求，再上版本号 + CRDT（那时你已经知道要解决什么冲突了）。

### J. 设备身份要有生命周期

设备 token 的**签发 / 续期 / 吊销**（设备丢失）、"我的设备"列表与踢下线。
**没有吊销能力，"设备级 token"就只是换了个名字的共享密钥。**

---

## 5. 端侧工具、权限与数据分级

### 5.1 工具清单（M1 交付 3 个即可）

| 工具 | 能力 | 权限 | 返回最小化 |
|---|---|---|---|
| `get_location` | 当前定位（可只回城市级） | `ACCESS_COARSE/FINE_LOCATION` | 默认只回城市/区，精确值需二次确认 |
| `search_contacts` | 通讯录检索 | `READ_CONTACTS` | 只回匹配项的必要字段 |
| `take_photo` / `pick_image` | 相机/相册 | `CAMERA` / 相册选择器 | 只回本地 URI + 端侧描述 |
| `get_notifications` | 通知摘要 | `NotificationListenerService` | 只回应用名+时间，不回正文 |
| `read_clipboard` | 剪贴板 | 前台限制 | 单次读取 |

### 5.2 数据分级（把 owner 的"只有简历"落成规则）

| 级别 | 例子 | 规则 |
|---|---|---|
| `DEVICE_ONLY` | 精确定位、通讯录明细、相册原图、通知正文 | **只在端侧处理，永不升云**（云端拿不到也不该拿） |
| `SENSITIVE` | 手机号、身份证、薪资明细等 | 脱敏/取摘要后可升云，**原始值不出端** |
| `NORMAL` | 简历文本、JD、公开知识、对话 | 按 §4.1 自由调度（owner 已同意简历可出网） |

### 5.3 权限与审计（命中 JD 的"权限控制/安全审计"）

- 运行时权限 + **敏感工具调用前显式确认**（可记住"本次会话内允许"）
- **审计日志**：时间 / 工具 / 参数摘要 / 授权结果 / 是否出端，本地可查、可导出
- 端侧不出网的保证方式：**Android 侧网络白名单 + 敏感数据在 Router 层就拒绝出端**（代码级约束，不靠自觉）

---

## 6. 端侧 RAG

| 项 | 决定 | 理由 |
|---|---|---|
| 检索 | **直接上向量**：bge-small-zh-v1.5 ONNX **INT8**（约 30–100MB）+ **sqlite-vec** | 端侧毫秒级、离线；比 BM25 省掉中文分词依赖 |
| **向量空间** | **与云端同一模型同一维度（512 维）** | 可做「**端侧粗检索 → 云端精排/合成**」的分层，离线时退化成本地检索 |
| 切片 | 与云端同口径（复用 SEKB 的切片策略） | 端云结果可比、可互换 |
| 范围 | 只索引"端侧真的会离线问"的资料（简历、常用 JD、笔记） | 别把云端知识库全量下发 |

---

## 7. 协议与鉴权（对前一版的关键修正）

| 用途 | 走什么 | 现状 | 要做什么 |
|---|---|---|---|
| 工具调用（职位分析/画像/提示词） | **MCP**（streamable HTTP 或 SSE） | ✅ 已有：`/jobcopilot/mcp`、`/jobcopilot/sse`，7 个工具，见 `docs/ops/15-MCP-ENDPOINT.md` | Android 手写 MCP Client（initialize → tools/list → tools/call） |
| 对话 / 多智能体 | **SEKB REST + SSE** | ✅ 已有：`backend/app/api/routes/chat.py:419`（`POST /api/v1/chat`）与 `:469`（`/stream`，SSE） | 客户端加 SSE 解析；服务端加**设备身份**与路由字段 |
| 鉴权 | **设备级 token** | ⚠️ 目前只有共享 `JOBCOPILOT_HTTP_TOKEN` | 新增设备注册/换取短时 token；**共享令牌与 LLM Key 绝不写入 APK** |

> ⚠️ 这条是安全红线：把 `JOBCOPILOT_HTTP_TOKEN` 或任何 LLM Key 打包进 APK，等于公开发布密钥
> （反编译即可提取）。端侧"BYOK"应由**用户在 App 内自行填写**，或统一由云端代付（服务端持有 key）。

**Mac 端侧的接入方式（比 Android 简单得多，见 §1.1）**

| 场景 | 做法 |
|---|---|
| 全本地 SEKB | `llm.base_url = http://127.0.0.1:11434/v1`、`llm.api_key = ollama`（占位）、11 个角色 model 换 `qwen3.5:4b-mlx` |
| 端云对照 | 两套 profile（`config.local.yaml` / `config.yaml`）切换，跑同一批问题拿对比数字 |
| 客户端路由 | 宿主进程里的 Router 决定"这次问本地还是问云端"——**与 Android 共用同一份逻辑**（纯 Kotlin/或纯 Python 模块） |

---

## 8. SEKB 侧需要的改动（清单 + 量级）

| # | 改动 | 位置 | 量级 | 必要性 |
|---|---|---|---|---|
| S1 | 设备身份：注册/换取设备 token（JWT 域内新增 `device` 概念） | `backend/app/api/routes/auth.py` + 新路由 | 1–2 天 | **M2 必需** |
| S2 | 路由事件上报 + 端侧可见的执行位置 | 新 `edge` 路由 + `chat` 响应加字段 | 1 天 | M2 必需（可观测） |
| S3 | 聊天响应带"本次执行位置/理由" | `chainlit`/`chat` 的服务端字段 | 0.5 天 | M2 |
| S4 | 端侧可用的小模型档位（云端也能用同一模型做对比评测） | `backend/config.yaml` 的 `llm.roles` | 0.5 天 | M3 评测用 |
| S5 | （按需）**per-role provider 路由** | `backend/app/core/config.py::LLMConfig`、`llm_factory.py` | 3–5 天 | ⚠️ 仅当要把"云端内部也分级"落地时才做，见 §11 风险 |

**已核实的三个硬约束**（做 S5 前必须知道）：
1. `LLMConfig` 只有**一个全局** `provider/api_key/base_url`，`LLMRoleConfig` 只有 `model/temperature/max_tokens/response_format`
   → 现在**无法**"某角色走本地、某角色走云端"；
2. `llm_factory.py` 里 `return ChatDeepseek(...)` 是**硬编码**，配置的 `provider` 字段是装饰性的（无分支）；
3. 现有降级链 `_should_fallback` / `_get_fallback_config` 靠**模型名含 "reasoner"** 匹配，而 11 个角色已全是
   `deepseek-flash` → **该降级链根本不会触发**。"端失败升云"要从零建，别以为配一下就有的用。

---

## 9. 里程碑（重排后）与交付物

| 阶段 | 周期 | 交付物 | 验收数字 |
|---|---|---|---|
| **M0** Mac 端侧跑通（**先做，因为能实测**） | 3–5 天 | Ollama 三档模型 + SEKB 全本地 profile（`base_url`→Ollama） | **真实数字**：decode tok/s、TTFT、内存峰值、单轮耗时；以及"完全离线可用"的证据 |
| **M1** 端云一致性（**题眼里的最难点**） | 1 周 | §4.5 的 SSOT / outbox / 升级-降级 / 交接摘要 / 版本戳 / 路由日志 | 升级率、端侧完成率、端↔云切换后**上下文不丢**（可复现的用例） |
| **M2** Android 端侧宿主（模拟器验功能） | 1–1.5 周 | llama.cpp NDK + ReAct + grammar 工具调用 + 3 个设备工具 + 权限审计；MCP/REST 双客户端 | **工具调用 JSON 合法率**（grammar 开/关）、越权拦截率、断网可用性 |
| **M3** 端侧 RAG + 评测 + 真机 | 1 周 | bge-small-zh(ONNX) + sqlite-vec；端 vs 云对比评测；真机性能数字 | §9.1 的六组数字 + Demo 视频 |
| stretch | 另评 | 车机/座舱第二端（复用纯 Kotlin 模块）**或**鸿蒙端 | 见 §11 |

> 与 v0.1.0 相比：**Mac 提到 M0**（唯一能立刻拿到真实性能数字的端侧，且接入是配置级）、
> **一致性（原 M2 的一部分）提到 M1**（题眼里最难的部分，与平台无关，先在能实测的平台上做对）、
> **Android 往后放**（没有真机 → 性能数字要等真机，先只用模拟器验功能）、**端侧 RAG 留在 M3**。

### 9.1 没有 Android 真机：验证策略（D10）

**结论：模拟器能验"功能对不对"，验不了"性能好不好"。两者要分开做，别混。**

| 要在模拟器上验（✅ 可以做） | 模拟器验不了（❌ 必须真机） |
|---|---|
| NDK 编译、arm64-v8a 加载、JNI 通路 | decode tok/s、TTFT（模拟器跑在 Mac 的 CPU 上，不是手机的 SoC） |
| 模型加载/换档、GGUF 解析、grammar 约束生效 | **内存带宽相关的一切**（手机 ~60–70GB/s vs Mac 350GB/s，量级差 5 倍） |
| ReAct 循环、工具注册与调用、参数校验 | GPU/NPU 路径（无 Adreno/Hexagon 透传；llama.cpp 的 Vulkan 后端本身在 ARM iGPU 上口碑就差） |
| 权限申请/拒绝流程、审计日志、DEVICE_ONLY 闸门 | 热降频、电量消耗、后台被杀 |
| 离线行为、路由决策、升级/降级、UI | 真实机型的"能不能装下/跑得动" |

**做法（三层，成本递增）**：
1. **Mac 上拿性能真值**（M0 已完成这件事）——手机性能可以按带宽比例做**保守外推**：
   手机带宽约为 Mac 的 1/5 → 同一模型 decode 预期约为 Mac 的 1/5（实测校准）；
2. **Android 模拟器（arm64-v8a 镜像，Apple Silicon 走 Hypervisor）验功能**——把 AVD 内存调大（≥4GB），
   `x86_64` 镜像不要用（会走非 NEON 路径，与真机差异更大）；
3. **真机数字**：借一台 / 二手千元档（骁龙 8 Gen 2/3 级别即可）/ 云真机平台。**这一步不能省**，
   因为简历与面试都会被问"你实测多少"。

> 顺带一句话结论：**llama.cpp 在 Android 上别指望 GPU/NPU**（社区实测 Vulkan 后端的 ARM iGPU 表现不佳），
> 先用 CPU 路径把功能与基线跑通，NPU 作为后续优化项——这与 §2.4 的判断一致。


### 9.1 评测设计（产出简历上的"量化结果"）

| 指标 | 测法 | 用途 |
|---|---|---|
| 意图分类准确率 | 20–50 条标注集，端侧 vs 云端 | 证明"端侧够用"的边界 |
| 工具调用合法率/正确率 | 同一集合，grammar 开/关对比 | 端侧 Agent 可用性的关键证据 |
| 首 token / 总延迟 | 端侧 vs 云端，按任务类型分组 | 支撑"延迟优先"的调度策略 |
| 资源占用 | 内存峰值 / 单轮耗电(mAh) / 温升 | 端侧部署的现实约束 |
| 路由质量 | 端侧完成率、升级率、误判案例 | **端云协同的核心指标** |
| 离线可用率 | 断网下可完成的任务比例 | 端侧价值（非本期硬要求） |

---

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 小模型工具调用不稳 | Agent 不可用 | grammar 约束 + 严格模板 + 升级信号；选型先跑测试集 |
| ReAct 上下文膨胀 | 延迟飙升 | prefix cache + 2–4K 预算 + 滚动摘要 |
| 手机上速度远低于预期 | 体验不可用 | M0 先实测；按机型分档；重任务一律走云 |
| NPU 落差 | 性能不达标 | NPU 不进 M0–M2；llama.cpp 走 CPU/GPU 先跑通 |
| 共享令牌/Key 进 APK | **密钥泄露** | 设备级 token；BYOK 由用户填或云端代付 |
| 鸿蒙需第二套 App + 推理栈 | 周期翻倍 | 单列 stretch，见 §11 |
| 端云切换导致答案跳跃 | 体验割裂 | §4.3 一致性设计 + 用户可见的"执行位置"标注 |
| 模型分发（GB 级） | 首次体验差 | 分档下载 + 断点续传 + 校验和 + WiFi 提示；起步只装 tiny/default |

---

## 11. 待 owner 确认的决策（8 条）

> **状态：D1–D8 已确认；D9–D11 为本轮 owner 提问后新增。**

| # | 决策 | 结论 |
|---|---|---|
| D1 | **鸿蒙是否本期做** | **不做**，列为 M3 后的 stretch。理由：鸿蒙不兼容 APK，等于第二套 App（ArkTS/ArkUI）+ 第二套推理栈（MindSpore Lite/HiAI）+ 华为上架流程，**额外 2–4 周**；而目标岗（小米/字节）的题眼是 Android 深度 |
| D2 | 仓库形态 | ✅ **已确认（2026-09-18 修正为 monorepo）**：端侧代码放 SEKB 主仓库 `apps/<平台>/`；SEKB 只加 §8 的 S1–S3 |
| D3 | 云端对话入口 | ✅ **已确认** |
| D4 | 设备身份 | ✅ **已确认** |
| D5 | 端侧 RAG | ✅ **已确认** |
| D6 | 默认模型档 | ✅ **已确认**（模型改为 `qwen3.5:2b-mlx` 起步，见 §2.5） |
| D7 | 是否做 S5（云端内部模型分级） | ✅ **已确认：暂不做**（§8 三处硬约束；客户端路由已覆盖题眼） |
| D8 | 评测集 | ✅ **已确认** |

---

## 12. 与 SEKB 的复用边界

| 复用（不改） | 新增（本期做） |
|---|---|
| jobcopilot 内核 7 工具 + MCP 端点（`docs/ops/15-MCP-ENDPOINT.md`） | Android 端侧宿主（llama.cpp + ReAct + 设备工具 + Router） |
| SEKB 对话 API（`/api/v1/chat` + `/stream`） | 设备身份与路由事件（S1–S3） |
| 提示词体系、评测思路、三层记忆设计 | 端侧 function-calling 模板 + grammar 约束 |
| 安全加固思路（JWT/审计/注入防护） | 端侧权限模型 + 越权拦截 + 审计日志 |

---

## 13. 待跟踪项（按 owner 决定：Android 先虚拟机验功能，其余挂账）

| # | 待跟踪项 | 何时做 / 触发条件 |
|---|---|---|
| T1 | **Android 真机性能验证**（tok/s / TTFT / 内存峰值 / 温升 / 耗电） | 拿到真机时（借、二手千元档、或云真机）。模拟器给不了这些数字（§9.1） |
| T2 | NPU 路径（QNN / Genie / 专用量化产物） | CPU/GPU 基线跑通之后，作为**优化项**，不进 M0–M2 |
| T3 | 鸿蒙端（ArkTS/ArkUI + MindSpore Lite/HiAI） | 作品落定后作为生态扩展（额外 2–4 周，D1 已确认本期不做） |
| T4 | 车机/座舱第二端（Android Automotive） | stretch，复用纯 Kotlin 模块（`mcp-client`/`agent-loop`/`router`） |
| T5 | SEKB 云端内部模型分级（per-role provider，即 §8 的 S5） | 出现"云端内部也要按角色分流"的真实需求时（D7 本期不做） |
| T6 | 多端并发写同一会话（+ CRDT） | 真有多端同时编辑需求时；当前用 `(user, device)` 分片规避（§4.5-I） |
| T7 | 端侧模型微调（自有 JD/简历数据） | 端侧 baseline 数字出来后，若质量不达标再做 |
| T8 | macOS 独立宿主 App（Tauri/原生壳） | 当前「SEKB + Ollama 全本地档」已能演示；**需要分发给他人**时再做 |
| T9 | 端侧 embedding 落地（bge-small-zh ONNX INT8） | M3 端侧 RAG 时做（必须与云端同 512 维空间，见 §2.5） |
| T10 | 模型分发与版本管理（分档下载/校验和/断点续传） | Android 要分发给他人时 |

---

## 14. M0 实施记录（2026-09-17）

### 14.1 关键发现：本地档**不需要改代码**

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

### 18.3 隐私与升级的关系（这一节决定端侧 RAG 的价值）

- **设备专属集合**（`device_only`）：嵌入与检索**只在端侧**，检索为空就如实说"本机资料里没有"，
  **绝不**为了答得更好把原文送去云端嵌入——这是 §5.2 硬边界在 RAG 上的具体形态。
- **非专属集合**：端侧检索为空/低分时，可升级到云端 RAG（走既有升级机制，带交接摘要）。

### 18.4 验收（模拟器阶段）

| 指标 | 测法 |
|---|---|
| 检索命中率 | 20–50 条标注集（问题 → 期望片段），端侧 top-k 命中率 |
| 延迟 | 嵌入耗时 / 检索耗时（分开测，便于判断瓶颈） |
| 空间一致性 | 同文本分别用端侧与云端嵌入，比对 `embedding_space`；不一致时必须走"仅本机检索"分支 |
| 端云协同 | 空检索触发升级的案例、设备专属集合不升级的案例（各 1 条即可） |

---

## 相关文档

- [MCP 端点运维手册（7 个工具、鉴权、自测）](ops/15-MCP-ENDPOINT.md)
- [JobCopilot 内核接入（技术）](tech/03-MODULES.md) §11.5
- [自迭代闭环设计 RFC](./RFC-自迭代闭环设计.md)（发布与回滚策略）
- [上线数据：M5 Pro 端侧带宽/解码实测](https://contracollective.com/blog/apple-silicon-memory-bandwidth-local-llm-tokens-per-second-m5-2026)
- [2026 小模型（SLM）选型综述](https://www.bentoml.com/blog/the-best-open-source-small-language-models)
