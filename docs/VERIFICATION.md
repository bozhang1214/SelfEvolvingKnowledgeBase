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
- [ ] **UI 手工走一遍**：自检走的是编排器直连，Compose 界面只做了编译验证，还没人工点过
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
