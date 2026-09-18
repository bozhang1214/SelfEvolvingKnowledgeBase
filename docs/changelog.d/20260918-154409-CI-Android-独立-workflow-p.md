## 2026-09-18（CI：Android 独立 workflow（path 过滤）+ 端云协议三方一致守卫）

- **背景**：端侧代码并入主仓后，服务端 CI 不该因为改 Android 而跑，反之亦然；
  另外协议文档（`docs/ops/16-端云协同协议.md`）是三端共享契约，**它能以三种方式静默漂移**：
  文档写了服务端不存在的端点、客户端调了文档未声明的端点、客户端调了已删除的端点。

- **改动**：
  - `.github/workflows/android.yml`：**独立 workflow + 原生 `paths` 过滤**
    （`apps/android/**`、`scripts/android.sh` 变更才触发；不引第三方 paths-filter）。
    单元测试 → 打 APK → 上传产物。SDK platform **先试 `android-36.1`、缺失退回 `android-36`**
    （不赌 runner 上有哪个），并把选中的次版本用 `-PsekbCompileSdkMinor` 传给 Gradle。
  - 构建支持覆盖：`apps/android/app/build.gradle.kts` 的 `compileSdkMinor` 可覆盖，
    `scripts/android.sh` 尊重外部 `GRADLE_USER_HOME`（便于 CI 缓存）。
  - `scripts/check_protocol_paths.py`：解析服务端真实路由（`APIRouter(prefix=)` + `@router.x`），
    与文档声明、客户端调用三方比对；接进 `doc_guard.sh` 作为 **5/5**，CI 的 docs-guard 作业自动覆盖。

- **验证**：`python3 scripts/check_protocol_paths.py` → 服务端 71 路由 / 文档 14 / 客户端 8，
  三方一致；**反向验证**（往客户端注入 `/api/v1/secret/backdoor`）→ 精确报出 2 处不一致、
  退出码 1，还原后恢复绿灯（证明守卫不是"永远绿灯"）。
