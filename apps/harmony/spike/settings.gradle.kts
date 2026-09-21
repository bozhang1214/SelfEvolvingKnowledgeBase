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
include(":knspike")
