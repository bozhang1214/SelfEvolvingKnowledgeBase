// SEKB 端侧宿主 · 构建配置
//
// 依赖来源优先级：本机已验证可用的阿里云镜像 → google/mavenCentral。
// 为什么这么写：这台机器上 Google Maven 可达但拉大件慢，镜像实测更快；
// 保留官方源作为兜底，避免镜像缺件时构建直接失败。
pluginManagement {
    repositories {
        maven("https://maven.aliyun.com/repository/gradle-plugin")
        gradlePluginPortal()
        google()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        maven("https://maven.aliyun.com/repository/google")
        maven("https://maven.aliyun.com/repository/public")
        google()
        mavenCentral()
    }
}

rootProject.name = "sekb-ondevice-agent"
include(":app")

// 端侧共享层（KMP）：源码在 apps/shared，但挂在 Android 构建里（wrapper 与 CI 都不用动）。
// 为什么不上移 wrapper：那样要同时改 scripts/android.sh 与 .github/workflows/android.yml 的缓存键，
// 属于"能少动就少动"的基建改动；等 iOS 侧真的需要独立构建时再评估（记录在 KMP 方案文档）。
include(":shared")
project(":shared").projectDir = file("../shared")
