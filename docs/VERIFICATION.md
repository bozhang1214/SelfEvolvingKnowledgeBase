# 验证记录（端侧宿主）

> 规则：**只写跑过的命令与真实输出**。"应该能跑"不算验证；没能验的写在最后一节。

环境：macOS（Apple Silicon）+ Android Studio JBR 21 + Android SDK platform 36.1 +
AVD `Medium_Phone_API_36.1`（arm64-v8a，google_apis_playstore）。

---

## 1. 已验（可在任何机器复现）

### 1.1 纯逻辑单测（54 用例）

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

**构建期抓到的一个真缺陷**（值得记录）：端侧输出预算最初照抄服务端的 300，
而 `chat` 角色的预期输出是 400 → 每一次聊天都被判去云端，"端侧优先"名存实亡。
单元测试当场抓住，改为 512（依据：M0 实测 2B decode 86–116 tok/s → 512 token ≈ 4.5–6s 最坏，
首字延迟由 `maxTtftMs` 与前缀守卫兜住）。并补了回归测试
`default chat role must be able to run on device` 钉住这个默认值组合。

### 1.2 APK 构建

```bash
./gradlew :app:assembleDebug
# → app/build/outputs/apk/debug/app-debug.apk
```

---

## 2. 待验（需要模拟器 / 联网 / 云端）

这一节是**尚未完成**的部分，不要当成已验：

- [ ] 模拟器安装并启动 App（真机联调第一步）
- [ ] 端侧链路：App → 宿主机 Ollama（`10.0.2.2:11434`）真实流式回答
- [ ] 端云协同：设备 enroll / 聊天 SSE / 执行位置徽标 / 路由事件上报落库
- [ ] 验收数字（RFC §9）：**工具调用 JSON 合法率**（约束解码开/关对比）、**越权拦截率**、断网可用性
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
- 设备凭证存于 App 私有目录 + Keystore 加密（`device/`，M2 第二批实现）；
  当前提交里尚未包含凭据存储与网络客户端。
