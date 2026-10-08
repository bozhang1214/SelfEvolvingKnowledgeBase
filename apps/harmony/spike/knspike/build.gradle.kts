plugins { id("org.jetbrains.kotlin.multiplatform") }

kotlin {
    // **只留 ohosArm64**（C4）：本机模拟器 `nova 16 Pro` 的 abi 就是 arm
    // （`~/.Huawei/Emulator/deployed/lists.json` → `"abi": "arm"`，见 apps/harmony/README.md），
    // 所以 ohosX64 那份产物用不上，留着只会让 HAP 里多一半 native 体积。
    // 真需要 x86_64 开发机时再加回来（一行），但要同步给 shared-core 加同名目标。
    ohosArm64 { binaries { sharedLib { baseName = "kn" } } }

    sourceSets {
        val ohosMain by creating { dependsOn(commonMain.get()) }
        val ohosArm64Main by getting { dependsOn(ohosMain) }
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
