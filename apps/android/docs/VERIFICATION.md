# 验证记录（端侧宿主）

> 规则：**只写跑过的命令与真实输出**。"应该能跑"不算验证；没能验的写在最后一节。

环境：macOS（Apple Silicon）+ Android Studio JBR 21 + Android SDK platform 36.1 +
AVD `Medium_Phone_API_36.1`（arm64-v8a，google_apis_playstore）。

---

## 1. 已验（可在任何机器复现）

### 1.1 纯逻辑与端云全流程单测（88 用例）

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
./gradlew :app:testDebugUnitTest
```

| 测试类 | 用例 | 覆盖的风险 |
|---|---|---|
| `PlaneRouterTest` | 19 | 预算决策、`device_only` 硬边界、6 类升级信号、**前缀阶段不判 `json_invalid`**、token 估算口径 |
| `StreamGuardTest` | 7 | 攒够阈值才放行、退化前缀改道且**零字符外泄**、短输出退化为整段评估、JSON 角色不被半截 JSON 误杀 |
| `SseParserTest` | 7 | `thinking/token/done/error` 四类事件、`done.meta.execution` 解析、非 JSON 数据不静默丢弃 |
| `ToolCallJsonTest` | 9 | 围栏/单引号/尾随逗号宽容解析、缺工具名与"没有 JSON"分别归类、字符串内花括号 |
| `ToolRegistryTest` | 8 | 三道闸门（未注册/缺参数/未授权）、**越权拦截率**计算、工具异常不崩 |
| `PermissionAuditTest` | 4 | 审计容量裁剪、最近优先、空日志不产生 NaN |
| `ChatOrchestratorTest` | 10 | **端云协同全流程**（无网、无模拟器）：端侧完成 / 退化前缀改道且零字符外泄 / 端点不可用改道 / DEVICE_ONLY 不升级且保留端侧结果 / 工具轮执行 / 越权拦截 / 交接块内容与截断 |
| `EdgeLlmClientTest` | 7 | 请求体约定（`reasoning_effort=none` 且**不带** `think`、JSON 模式）、OpenAI 兼容流式解析、HTTP 错误与网络异常不抛 |
| `SekbApiTest` | 8 | enroll/refresh/heartbeat/上报/聊天的**请求形状**与错误分类（401/403/429）、SSE 执行位置解析 |
| `CredentialCodecTest` | 5 | 凭证编解码往返、坏数据解成 null 而不是崩、轮换阈值随服务端 TTL 变 |
| `ToolCallEvalTest` | 4 | 合法率分母是"尝试次数"而非全部回答 |

**构建期抓到的真缺陷**（单元测试的第一价值就是这些）：

1. **端侧输出预算照抄服务端的 300**，而 `chat` 角色的预期输出是 400 →
   **每一次聊天都被判去云端**，"端侧优先"名存实亡。改为 512（依据：M0 实测 2B
   decode 86–116 tok/s → 512 token ≈ 4.5–6s 最坏，首字延迟由 `maxTtftMs` 与前缀守卫兜住），
   并补回归测试 `default chat role must be able to run on device` 钉住默认值组合。
2. **改道成功后回答被重复输出一遍**：工具轮没走时 `toolRound.text` 就是刚吐过的那段，
   又补发了一次。修法：只有真的做了工具轮才补发（`toolRound.attempted`）。
3. **"端点连不上"被误报成"模型答了空"**：守卫的整段评估先跑，空输出命中 `empty`，
   把真正的失败原因盖掉了。修法：失败判定提到守卫之前。
4. **DEVICE_ONLY 场景下用户看到空回答**：判定该改道却不许改道时，守卫缓冲里的内容被丢弃了。
   修法：不允许改道时把缓冲内容保留并展示——宁可给一段不完美的本机回答，也不能给空白。

### 1.2 APK 构建

```bash
./gradlew :app:assembleDebug
# → app/build/outputs/apk/debug/app-debug.apk
```

### 1.4 验收数字：工具调用 JSON 合法率（约束解码 ON/OFF 对照）

```bash
adb shell am force-stop com.sekb.ondevice
adb shell am start -n com.sekb.ondevice/.MainActivity --ez eval true            # 易档
adb shell am start -n com.sekb.ondevice/.MainActivity --ez eval true --ez hard true  # 难档
adb logcat -d -s SEKB_EVAL:I
```

做法：同一批提示词 × 同一模型 × 同一温度，只切换
`response_format={"type":"json_object"}`（约束解码在 OpenAI 兼容协议里的对应物）；
分母用**尝试次数**（"模型有没有想调工具"）而不是全部回答——否则模型越不敢用工具，这个数字越好看。

| 档位 | 提示词 | 约束解码 | 尝试 | 合法 | 合法率 | 平均延迟 |
|---|---|---|---|---|---|---|
| qwen3.5-2b | 易档（含"只调用工具"） | ON | 10/10 | 10 | **100%** | 187ms |
| qwen3.5-2b | 易档 | OFF | 10/10 | 10 | **100%** | 163ms |
| qwen3.5-4b | 易档 | ON | 10/10 | 10 | **100%** | 319ms |
| qwen3.5-4b | 易档 | OFF | 10/10 | 10 | **100%** | 346ms |
| qwen3.5-2b | 难档（不给"只输出 JSON"指令） | ON | 10/10 | 10 | **100%** | 190ms |
| qwen3.5-2b | 难档 | OFF | 10/10 | 10 | **100%** | 183ms |
| qwen3.5-4b | 难档 | ON | 10/10 | 10 | **100%** | 358ms |
| qwen3.5-4b | 难档 | OFF | 10/10 | 10 | **100%** | 361ms |

**结论（如实说，包括它推翻的东西）**：40 次调用里**没有一次**非法，也没有一次工具名幻觉；
两种模式的延迟差在噪声范围（±20ms，且方向在两个档位间不一致）。
所以**在这批条件下，约束解码没有带来可测量的收益**——原本假设"小模型必须靠 grammar 才能产出合法
工具调用"，实测**不成立**（qwen3.5-2b 在难档下同样是 100%）。据此的建议是：
**先不要为 grammar 付出工程复杂度**，把 `response_format` 留成开关，等换了更弱的模型再验证。

**这个实验的局限（同样要说清楚）**：
- 系统提示里仍然给了完整的 JSON 形状与工具清单——没有测"完全不给 schema"的极端条件；
- 10 条提示词各只跑 **1 次**，没有重复采样，因此**没有方差估计**，100% 也可能只是运气好；
- 工具集只有 3 个且互不冲突，未测"多个相似工具里选错"的情况；
- 全部在**模拟器 + 宿主 Ollama** 上完成（见 §9.1：模拟器不产出性能结论，这里只做功能与协议判定）。

### 1.6 端侧 RAG（M3 第一批，2026-09-18）

单测：`rag.*` / `embed.*` / `tools.KbSearchToolTest` 共 **41 条**（首次构建时抓到一个真缺陷，见下）。

模拟器自检（`--ez selftest true`）新增六项，**PASS=15 FAIL=0 SKIP=1**：

```
rag_ingest          PASS  doc-diet:1块 doc-weather:1块 doc-rag:1块 空间=stub-hash@256
rag_retrieve        PASS  命中=doc-rag 分=0.360 嵌入=1ms 检索=0ms
rag_space_guard     PASS  索引空间=stub-hash@256 与云端一致=false（桩实现应为 false）
rag_space_refuses   PASS  embedding_space_mismatch:索引=stub-hash@256 当前模型=other-model@256
rag_device_only_guard PASS device_only_requires_on_device_embedding:当前嵌入=stub-hash@256 非本机计算
rag_tool_search     PASS  ok=true 输出=[0.348] doc-rag: 端侧 RAG 把知识索引放在设备上…
```

存储：SQLite 持久化（`SqliteVectorStore`）。**跨进程重启验证**（跑前 force-stop，每次都是新进程）：

```
第 1 次：rag_ingest … 启动时已有=0（SQLite 持久化）
第 2 次：rag_ingest … 启动时已有=3（SQLite 持久化）   ← 上一轮的索引真的落盘存活了
rag_sqlite_space_guard  PASS  索引的空间是 stub-hash@256，当前嵌入模型是 other-model@256：
                              换模型必须重建索引（RFC §18.1）
```

**四个"拒绝/约束"路径是重点**（比"能检索"更能说明设计成立）：

| 场景 | 期望行为 | 实测 |
|---|---|---|
| 索引空间 ≠ 当前嵌入模型（换模型没重建索引） | **拒绝检索**，而不是混算余弦 | ✅ 返回 `embedding_space_mismatch` + 空结果 |
| 设备专属集合 + 嵌入非本机 | **拒绝**（宁可答不出来） | ✅ 返回 `device_only_requires_on_device_embedding` |
| 桩实现的空间 ≠ 云端空间 | 本机可检索，但标记**不可与云端融合** | ✅ `cloudCompatible=false` |
| 换嵌入模型后打开旧索引（SQLite） | **打开就失败**并要求重建 | ✅ 抛出并带明确原因 |

### 端侧 ONNX 嵌入（已接入，2026-09-18）

| 项 | 实测 |
|---|---|
| 提供者 | **ONNX `bge-small-zh-v1.5`**（不是桩） |
| 空间 | `BAAI/bge-small-zh-v1.5@512` → `cloudCompatible=**true**`（与云端同空间） |
| 区分度自检 | 两段无关文本余弦 **0.244**（云端参照 0.243864；桩会是 1.000 那种"塌缩"） |
| 检索 | 查询"端侧 RAG 为什么隐私更好" → 命中 **doc-rag 得分 0.696**；嵌入 36ms、检索 <1ms |
| 工具 | `kb_search` 返回 doc-rag 0.642 |
| 隐私闸门 | 同空间但 `isOnDevice=false` → 拒绝，原因 `device_only_requires_on_device_embedding` |
| 自检汇总 | **PASS=20 FAIL=0 SKIP=1** |
| 检索评测（33 条标注集） | Hit@1 **87%**、Hit@3 **100%**、MRR **0.928**、误召回 **0/3**；嵌入 38ms（p95 57ms）、检索 0.5ms —— 见 [`RETRIEVAL-EVAL.md`](RETRIEVAL-EVAL.md) |
| 阈值标定 | 由评测定出 **0.4**（0.2 时 3 条"库里没有"的问题全被强行回答） |

**两个环境约束**（都写进了脚本）：

1. **ONNX Runtime 必须用 1.20.0**：1.30.0 在模拟器上 **SIGILL**（`ILL_ILLOPC`）——
   模拟器 CPU 暴露了 `asimddp/bf16` 但**没有 `i8mm`**，ORT 新版 arm64 内核用到了它。
2. **模型不能 `adb push` 到外部私有目录**：推过去的属主是 `shell`、目录权限
   `drwxrws--- shell:ext_data_rw`，App 不在该组里 → 读不到（表现为"模型在但找不到"）。
   改用 `adb shell run-as <pkg> sh -c 'cat > <绝对路径>'`（整条远程命令必须是一个字符串）。
   日常用 `bash scripts/android.sh push-model` 即可（已封装）。

**踩过的坑（值得单独记）**：主机上"ONNX 与 sentence-transformers 余弦 1.000000"曾被当成
导出成功的证据，其实是**空洞验证**——两边都用了我这台 Mac 缓存里**退化的 `model.safetensors`**。
用 `pytorch_model.bin` 加载才正常（0.243864，与云端 safetensors 完全一致）。
详见 SEKB RFC §18.6；`scripts/fetch_embedding_model.sh --verify` 现在会额外做**区分度检查**。

> 桩（`stub-hash@256`）仍保留：模型不在设备上时自动退回，并在自检里如实标注提供者。

**首次构建抓到的真缺陷**：`DeviceTool.args` 被同时当作"给模型看的 schema"和"必填参数"，
于是 `kb_search` 的可选参数 `top_k` 让**每次调用都变成"缺少参数: top_k"**（工具直接不可用）。
修法：`DeviceTool` 拆出 `requiredArgs`（默认取 `args.keys`，可选参数显式声明）——已补 1 条回归测试。

**两个环境坑**（都写进了脚本/记录）：

1. `scripts/emulator.sh` 原先只等 adb 认到设备，会在"能 adb 但 PackageManager 还没起完"时
   返回假就绪 → `install` 报 `Error: device is still booting`。**已改为同时等 `sys.boot_completed=1`**。
2. 把 `ANDROID_USER_HOME` 搬进仓库会**换掉 debug 签名密钥**，于是模拟器上旧装的 APK 无法覆盖安装
   （`INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match`）→ 先 `adb uninstall` 一次即可。
   这是一次性代价，之后签名稳定。

### 1.5 UI 人工路径验收（2026-09-18）

自检（§1.3）走的是"编排器直连"，**绕过了界面**；这一节补的是界面本身：真实点击、"设备接入"、
发送、渲染。做法：`adb shell input tap` + `uiautomator dump` 定位控件（不靠猜坐标），
每步 dump 校验状态，最后截图目视确认。

**过程中踩到的两个环境坑**（都会让"点击无效"看起来像 App 的 bug）：

1. **Gboard 窗口盖住下半屏**：点击全打在输入法上 → 按钮没反应。解法：
   `adb shell ime disable com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME`
   （`input text` 不依赖输入法，禁掉后点击才落到 App）。
2. **`input text` 的空格要写 `%s`**：`input text 'What is the answer'` 只会输入 `What`，
   其余被当成 `input` 的子命令。中文也无法用 `input text` 输入。
3. 焦点切换用 `KEYCODE_TAB`（点击密码框在部分状态下不生效）。

**结果**（`docs/screenshots/m2-ui-e2e.png`）：

```
设备：f26c077a59704b52                     ← 界面上完成接入（enroll）
用户气泡：Hello
助手气泡：你好！有什么我可以帮你的吗？
        本机完成 · qwen3.5-4b              ← 执行位置徽标（绿色）
最近决策：edge · edge_preferred
权限审计：调用 0 次，拦截 0 次（越权拦截率 0%）
```

**这一轮 UI 验收抓到两个真缺陷**（都已修）：

1. **密码框明文显示**——截图里密码白纸黑字可见（截图/投屏/旁人一瞥即泄漏）。
   修法：`visualTransformation = PasswordVisualTransformation()`。
2. **徽标漏报"客户端侧升级"**——客户端因 `edge_unavailable` 改道云端时，服务端回传的
   `execution.escalated=0`（它只统计**服务端内部**的升级），于是界面显示成平平无奇的
   "云端完成"，用户不知道自己的问题在端侧失败过。修法：徽标同时看客户端侧结果，
   并补 5 条 `BadgeTest` 钉住文案。

### 1.3 模拟器真机 E2E（14 项全绿，2026-09-18）

环境：macOS Apple Silicon + AVD `Medium_Phone_API_36.1`（arm64-v8a，`-memory 4096`）
+ 宿主机 Ollama（`10.0.2.2:11434`）+ **本地** SEKB 后端（`10.0.2.2:8010`，双平面档）。

```bash
# 1) 起模拟器与本地后端
emulator -avd Medium_Phone_API_36.1 -memory 4096 &
cd <SEKB>/backend && HF_HUB_OFFLINE=1 SEKB_CONFIG_PATH=/tmp/sekb-e2e.yaml \
  .venv/bin/python -m uvicorn app.api.main:app --host 0.0.0.0 --port 8010

# 2) 装机 + 跑自检（**必须先 force-stop**：extras 只在 onCreate 生效）
./gradlew :app:installDebug
adb shell am force-stop com.sekb.ondevice
adb shell am start -n com.sekb.ondevice/.MainActivity --ez selftest true \
  --es sekb "http://10.0.2.2:8010" --es email <账号> --es password <密码>
adb logcat -d -s SEKB_SELFTEST:I
```

实测结果（原样摘录 logcat）：

| 项 | 结果 |
|---|---|
| `edge_reachable` | PASS `http://10.0.2.2:11434/v1` |
| `edge_models` | PASS qwen3.5-2b/4b/9b 等 8 个模型在位 |
| `edge_warmup` | PASS qwen3.5-4b keep_alive=30m |
| `router_decision` | PASS plane=edge reason=edge_preferred tier=default model=qwen3.5-4b |
| `edge_stream` | PASS 39–59 字符真实流式，**预热后 TTFT 73–178ms**（预热前 1780ms） |
| `permission_gate` | PASS 未授权 `READ_CONTACTS` → 拦截 |
| `permissionless_tool` | PASS `device_time` 正常返回 |
| `audit_stats` | PASS 总调用=2 拦截=1 **越权拦截率 50%** |
| `edge_tool_json` | PASS 约束模式产出 `{"tool":"device_time","args":{}}` |
| `cloud_login` | PASS 真实 SEKB 登录 |
| `cloud_enroll` | PASS device_id=… ttl=720h |
| `cloud_chat` | PASS 106 字符流式 + `execution=cloud` + `reason=output_over_edge_budget(800>300)\|stream_no_escalate` + thinking 事件 |
| `cloud_route_event` | PASS 上报幂等键 `selftest-…`，服务端 200 |
| `cloud_stats` | PASS `by_plane={"edge":7,"cloud":28}` `by_role={...,"chat":2,...}` ← **设备上报的事件真的落库了** |

**这套 E2E 抓到两个只有真客户端能暴露的缺陷**：

1. **登录字段名猜错**：客户端按 OAuth 习惯读 `access_token`，而 SEKB 的
   `LoginResponse` 是 `{"user":…,"token":…}` → 服务端 200 OK、客户端却报"登录失败"。
   单测当时喂的是我**以为**的响应体，所以照样全绿（这就是"用假传输测出来的绿"的边界）。
2. **SEKB 流式路由事件缺 `model`**：`execution.model` 为空。修在 SEKB 侧
   （新增 `_resolved_model`，不依赖实例缓存），并补了回归测试。

**环境注意事项**（踩过，写下来省下一次）：

- 本地后端必须 `HF_HUB_OFFLINE=1`：否则 sentence-transformers 会对 huggingface.co
  发 HEAD 探测（本网络不可达），每次调用重试 5×2s，聊天能拖到 10 分钟并触发客户端读超时。
- 本地后端要关掉资讯调度（`news.enabled=false`）：单 worker 下长报告生成会独占 LLM 容量。
- 用 `/tmp` 下的临时配置时要**显式指定 `storage.data_dir`**，否则数据目录跟着配置走，
  已注册的账号会"消失"。
- 自检用 `am force-stop` 后再 `am start`（extras 只在 `onCreate` 生效）。

---

## 2. 待验（需要模拟器 / 联网 / 云端）

这一节是**尚未完成**的部分，不要当成已验：

- [x] ~~模拟器安装并启动 App~~ → §1.3
- [x] ~~端侧链路：App → 宿主机 Ollama 真实流式回答~~ → §1.3（预热后 TTFT 73–178ms）
- [x] ~~端云协同：enroll / SSE / 执行位置 / 上报落库~~ → §1.3
- [ ] **工具调用 JSON 合法率**：约束解码开/关的**同一批提示词对比**（当前只验了"能产出合法 JSON"，
      还没跑成组的合法率对比——需要固定一组提示词 + 各跑 N 次）
- [x] ~~**UI 手工走一遍**~~ → §1.5（并抓到两个真缺陷：密码明文、徽标漏报客户端升级）
- [ ] 断网可用性（飞行模式下端侧链路是否仍可用）
- [ ] 真机性能（decode tok/s、TTFT、内存峰值）——**模拟器测不了**，属 M3

## 3. 怎么验（模拟器联调步骤）

前置：

1. 宿主机起 Ollama 并确认模型就绪：
   ```bash
   ollama list | grep qwen3.5        # 期望看到 qwen3.5-2b/4b/9b
   curl -s http://127.0.0.1:11434/v1/models | head -c 200
   ```
2. 起模拟器（**内存给到 4G**：默认 2048 太小，加载模型会抖）：
   ```bash
   ~/Library/Android/sdk/emulator/emulator -avd Medium_Phone_API_36.1 -memory 4096 &
   adb wait-for-device
   ```
3. 安装并启动：
   ```bash
   ./gradlew :app:installDebug
   adb shell am start -n com.sekb.ondevice/.MainActivity
   ```

> `10.0.2.2` 是模拟器里指向**宿主机**的固定地址（不是 localhost）。
> 真机联调时把 App 里的 edge base url 改成开发机的局域网 IP，
> 并把该 IP 加进 `app/src/main/res/xml/network_security_config.xml` 的明文白名单。

## 4. 已知限制

- **无真机**：所有性能类结论都不在本仓库产出（RFC §9.1 的 D10 决策）。
- 设备凭证存储（Keystore AES-GCM）与网络客户端**只做了 JVM 可验的部分**：
  Keystore 本身、真实 SSE 连接、真实 Ollama 调用都必须在模拟器上验（见 §2）。
- 未做：Compose UI、Android 运行时装配（把上面这些接起来）、约束解码开关的对比实验。
