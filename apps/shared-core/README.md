# apps/shared-core · 共享层的**鸿蒙侧编译单元**（H1）

**这里没有一行 .kt**——它是"一套源码、两侧编译"里的那一侧：

```
apps/shared/src/commonMain/kotlin/**        ← 唯一的实现（41 个 .kt，零平台依赖）
        │
        ├── apps/android/app → :shared（AGP 9 + 标准 Kotlin 2.2.10）   ← Android / iOS / Mac
        │        └── apps/shared/build.gradle.kts 把上面的 srcDirs 纳入自己的 commonMain
        │
        └── apps/harmony/spike → :shared-core（OHOS Kotlin fork 2.2.21-0.3.0-07）
                 └── apps/shared-core/build.gradle.kts 把同一批 srcDirs 纳入 commonMain
                          + src/ohosMain/kotlin/**  ← 本目录唯一的代码：三个 actual
```

## 为什么不能合并成一个构建（H1 的判定点，有实测证据）

`apps/shared` 用 **AGP 9 专属插件**（`com.android.kotlin.multiplatform.library` + `androidLibrary {}`），
而鸿蒙 spike 的 Kotlin fork 配套 **AGP 8.11.2**。H1 先按 HB1 试了复合构建：

```bash
cd apps/harmony/spike
echo 'includeBuild("../../shared")' >> settings.gradle.kts
./gradlew --no-daemon projects
```

**4 秒即失败，且失败在插件应用阶段**（不是"可能撞版本"，是插件层面就对不上）：

```
* Where:
Build file '/Users/zhangbo/.../apps/shared/build.gradle.kts' line: 14
* What went wrong:
Plugin [id: 'org.jetbrains.kotlin.multiplatform'] was not found in any of the following sources:
- Gradle Core Plugins (plugin is not in 'org.gradle' namespace)
- Included Builds (No included builds contain this plugin)
- Plugin Repositories (plugin dependency must include a version number for this source)
```

→ 因此采用《鸿蒙端方案》§7.2 H1 备注里的**推荐形态**：抽出不含 Android 插件的纯 KMP 模块，
**一处源码两侧编译，不动已全量验证的 Android 基线**。

## 本目录里有什么

| 路径 | 作用 |
|---|---|
| `build.gradle.kts` | 只在**鸿蒙 spike 构建**里生效：声明 `ohosArm64`、把 `apps/shared/src/commonMain/kotlin` 加入 commonMain、依赖 kotlinx-serialization-json 的 ohos 变体 |
| `settings.gradle.kts` | **占位**（防"以为这里是独立构建"）；正常用法是在 spike 构建里 `include(":shared-core")` |
| `src/ohosMain/kotlin/com/sekb/shared/core/{Clock,Hmac}.ohos.kt` | 共享层三个 `expect` 的 ohos `actual`（`nowMillis` / `hmacSha256Hex` / `sha256Hex`） |
| `src/ohosMain/kotlin/com/sekb/ohos/Sha256Pure.kt` | 纯 Kotlin SHA-256 + HMAC-SHA256（**选型理由与验证方式写在该文件类注释里**） |

> `Digests`（`apps/shared/.../core/Digests.kt`）只是包一层的 `object`，不需要 ohos 专属实现。

## 加密 API 的选型（H1 的一条实打实的调研结论）

| 候选 | 结论 |
|---|---|
| `CryptoArchitectureKit`（`OH_CryptoDigest_*` / `OH_CryptoMac_*`，`libohcrypto.so`） | sysroot **有头文件与导入桩**（114 个符号，`OH_CryptoDigest_*` 自 API 12、`OH_CryptoMac_*` 自 API 20）；但**无法在本环境证明设备上真的加载得到**——设备探测里 `test -e /system/lib64/libohcrypto.so` 报 MISSING，而对**已确认在跑的** `libmindspore_lite_ndk.so` 同样报 MISSING（SELinux 限制，探测结论不可信）。HAP 若 `DT_NEEDED` 一个不存在的 `.so` 会**整个加载失败** |
| 纯 Kotlin（本目录采用） | 零运行时依赖；只用语言运算，可用 RFC 4231 / FIPS 180-4 公知向量**逐位**验证；且**只放在 `ohosMain`**，不影响 Android(`javax.crypto`)/Apple(`CommonCrypto`) |

**这是"风险判断"不是"平台不支持"**：一旦设备侧能证明 `libohcrypto.so` 可得，
换成系统实现只需改 `Hmac.ohos.kt` 一个文件（接口不变，冒烟向量不变）。

## 怎么验

```bash
bash scripts/harmony.sh smoke     # 编 ohos 产物 + 冒烟（公知向量 / canonical JSON / ToolCallJson / Fmt）
bash scripts/harmony.sh selftest  # 装到模拟器，抓 SEKB_HARMONY_SELFTEST 的 PASS/FAIL 表
```
