plugins { id("org.jetbrains.kotlin.multiplatform") }

kotlin {
    // **只留 ohosArm64**（C4）：本机模拟器 `nova 16 Pro` 的 abi 就是 arm
    // （`~/.Huawei/Emulator/deployed/lists.json` → `"abi": "arm"`，见 apps/harmony/README.md），
    // 所以 ohosX64 那份产物用不上，留着只会让 HAP 里多一半 native 体积。
    // 真需要 x86_64 开发机时再加回来（一行），但要同步给 shared-core 加同名目标。
    // 接住目标对象：cinterop 只能建在 **compilation** 上（写成
    // `kotlin.sourceSets.getByName("ohosArm64Main").cinterops` 会报 `Unresolved reference: cinterops`）。
    val ohosTarget = ohosArm64 { binaries { sharedLib { baseName = "kn" } } }

    sourceSets {
        val ohosMain by creating { dependsOn(commonMain.get()) }
        val ohosArm64Main by getting { dependsOn(ohosMain) }
    }

    // ── H5：RDB 的 NDK C API ────────────────────────────────────────────────
    // **不需要写 cinterop `.def`** —— OHOS Kotlin fork 的 **平台 klib** 里已经有一份
    // 完整的 ArkData/RDB 绑定（实测：`.tooling/konan/kotlin-native-prebuilt-*/klib/platform/ohos_arm64/`
    // 下有 `org.jetbrains.kotlin.native.platform.RDB`，manifest 里写着
    // `headers=database/rdb/relational_store.h … linkerOpts=-lnative_rdb_ndk.z`、
    // `package=platform.ArkData.RDB`）。也就是说 `import platform.ArkData.RDB.*`
    // 才能用到它，而**自建 `.def` 反而会重复一份**（第一版就是这么写的，已删）。
    //
    // 与注释掉的 ohcrypto 探针同一口径：平台绑定可用时，不要再造一份。

    // ── H4：HUKS 的 cinterop 绑定（**与 RDB 不同，这一份必须自建**）────────────
    // 为什么 HUKS 不能像 RDB 那样用平台 klib：平台 klib 里**没有** HUKS 包
    // （`ls .tooling/konan/.../klib/platform/ohos_arm64/ | grep -i huks` 为空），
    // 而 sysroot 里确实有头文件（`usr/include/huks/*.h`）与导入桩（`libhuks_ndk.z.so`，28 个符号）。
    // 设备侧可得性已由 `bash scripts/harmony.sh probe` 的 dlopen 探针给出（`LOAD OK libhuks_ndk.z.so`）。
    //
    // ⚠️ 头文件**不是**从 sysroot 直接 include 的：实测把 `-I<sysroot>/usr/include` 写进 `.def` 的
    // `compilerOpts` 时，本 fork 的 cinterop 会**静默产出空包**（exit 0、`package huks {}` 为空），
    // Kotlin 侧报的是 `Unresolved reference: huks`。因此头文件随仓库入库在
    // `src/nativeInterop/include/`（逐字节复制，来源与哈希见该目录的 README），
    // 重新同步：`bash scripts/harmony.sh sync-huks-headers`。
    ohosTarget.compilations.getByName("main").cinterops.create("huks") {
        definitionFile.set(project.file("src/nativeInterop/cinterop/huks.def"))
    }
}

// ── H1：依赖共享层（`apps/shared-core` = `apps/shared/src/commonMain` 的鸿蒙侧编译单元）──
// 它提供 41 个共享 .kt（路由/守卫/编排/检索/协议/JSON 门面）与 ohosMain 的三个 actual
// （`nowMillis` / `hmacSha256Hex` / `sha256Hex`）。
dependencies {
    add("commonMainImplementation", project(":shared-core"))
}

// ── 平台加密绑定（ohcrypto）：**默认关闭** ─────────────────────────────────────
// 它是"平台加密 C 接口能不能编"的探针，**不是**共享层的实现——共享层的 Hmac 走纯 Kotlin
// （理由与证据见 apps/shared-core/README.md §加密 API 的选型）。
// 复核方式：去掉下面三行注释，再跑 `bash scripts/harmony.sh smoke`。
//
// val ohosArm64Main by kotlin.sourceSets.getting
// ohosArm64Main.cinterops.create("ohcrypto") {
//     definitionFile.set(project.file("src/nativeInterop/cinterop/ohcrypto.def"))
// }
