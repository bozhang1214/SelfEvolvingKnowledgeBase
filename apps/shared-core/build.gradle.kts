// SEKB 端侧共享层 · **鸿蒙侧编译单元**（H1）
//
// ## 为什么有这个模块（而不是直接给 `apps/shared` 加 ohos 目标）
//
// `apps/shared` 的构建脚本用 **AGP 9 专属插件**（`com.android.kotlin.multiplatform.library`
// + `androidLibrary {}`），而鸿蒙 spike 用的 OHOS Kotlin fork 配套 **AGP 8.11.2**。
// H1 实测：`includeBuild(apps/shared)` 在**插件应用阶段**就失败（4s）——
// 不是"可能撞版本"，是插件层面就对不上。见 `apps/shared-core/README.md`。
//
// 因此把**纯逻辑**（零平台依赖的 commonMain）抽成本模块，两侧各自编译：
//   · Android / iOS：`apps/shared`（它把本目录的 commonMain 源码纳入自己的源集）
//   · 鸿蒙：`apps/harmony/spike` 里 `include(":shared-core")`（本文件生效）
// **一处源码、两侧编译**：`srcDirs` 指向 `apps/shared/src/commonMain/kotlin`，不复制任何 .kt。
//
// ## 本模块声明哪个目标
//
// **只声明 `ohosArm64`**（C4：HAP 只留 arm64）。
// 这里**不做**"检测当前构建是否支持 ohos"的条件判断——实测那不可靠：
// 用 `extensions.findByName("ohosArm64")` 判定会得到 null（fork 的 target 是惰性注册的），
// 于是目标根本没声明，报出来的是
// `Source set 'commonMain' couldn't resolve dependencies ... Unresolved platforms: [ohosArm64]`
// 这种**离现场很远**的错误。本模块只会被 `apps/harmony/spike/settings.gradle.kts` include，
// 那里必然是 OHOS fork；写错了就应该在配置阶段大声失败，而不是静默降级成一个空模块。
// ============================================================

plugins {
    id("org.jetbrains.kotlin.multiplatform")
}

// ── 一处源码：直接指向 apps/shared 的 commonMain（不复制 .kt）─────────────────
// 用**本 build 文件自身的路径**定位仓库根，而不是 `rootProject` 或 `project.projectDir`：
// 本文件被 `include(":shared-core")` 时 `projectDir` 取决于调用方写的是哪个目录
// （H1 实测：写 `file("../../shared-core")` → projectDir 就是它），而 rootProject 在
// 复合构建里也未必是仓库根。`buildFile` 不受这些影响，是唯一稳定的锚点。
val buildFileDir: File = buildFile.absoluteFile.parentFile
val repoRoot: File = buildFileDir.parentFile.parentFile // apps/shared-core → apps → 仓库根
val sharedCommonSrc: File = File(repoRoot, "apps/shared/src/commonMain/kotlin")

require(sharedCommonSrc.isDirectory) {
    "找不到共享层源码目录：$sharedCommonSrc —— shared-core 只引用不复制，" +
        "目录缺失说明仓库布局变了（H1 的口径是「一处源码两侧编译」）。"
}

kotlin {
    ohosArm64 {
        // 与 knspike 一样只要库：共享层是**被依赖方**，不需要自己的可执行产物。
        // 显式写出来是为了让"共享层真在鸿蒙侧编过"这件事在 build 目录里有迹可循。
        binaries { sharedLib { baseName = "knsharedcore" } }
    }
    sourceSets {
        // `kotlin.mpp.applyDefaultHierarchyTemplate=false`（spike 的 gradle.properties）
        // → 中间源集要手工接。与 knspike/build.gradle.kts 同一写法。
        val commonMain by getting {
            kotlin.srcDir(sharedCommonSrc)
            dependencies {
                // 与 apps/shared 同版本口径（`core/Json.kt` 的门面底层）。
                // 为什么是 `-1.0.0` 后缀：那是 eazytec nexus 给 ohos 目标的变体版本线
                // （官方线 1.8.1 **没有** ohos 变体，实测 klib 404；1.9.1-1.0.0 为 200）。
                implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.9.1-1.0.0")
            }
        }
        val ohosMain by creating { dependsOn(commonMain) }
        val ohosArm64Main by getting { dependsOn(ohosMain) }
    }
}
