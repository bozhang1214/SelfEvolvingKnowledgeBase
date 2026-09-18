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
