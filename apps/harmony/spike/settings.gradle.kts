// M6 spike：独立 Gradle 构建（**故意不并进 apps/android 的构建**）
//
// 为什么独立：CPF-KMP-CMP 的 Kotlin fork 配套 AGP 8.11.2，而我们的 Android 构建是 AGP 9、
// 已经过全量验证。把 ohos target 塞进现有构建会同时动 Kotlin 版本与 AGP —— 那是"为了验 A 顺手把 B 弄坏"。
// 独立构建的代价只是多一份 wrapper，收益是**互不影响**。
pluginManagement {
    repositories {
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-public/")
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositories {
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-public/")
    }
}
rootProject.name = "sekb-ohos-spike"

// H1：共享层在本构建里以 `:shared-core` 的形态参与编译。
//
// 为什么不是 `includeBuild("../../shared")`（复合构建）：H1 实测在**插件应用阶段**就失败
// （apps/shared 用 AGP 9 专属插件，本构建的 fork 配套 AGP 8.11.2）——证据见 apps/shared-core/README.md。
// 现在：`apps/shared-core` 是**不含 Android 插件**的纯 KMP 模块，`srcDirs` 直接指向
// `apps/shared/src/commonMain/kotlin`，所以是「一处源码、两侧编译」，没有复制任何 .kt。
include(":shared-core")
project(":shared-core").projectDir = file("../../shared-core")
include(":knspike")
