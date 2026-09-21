// SEKB 端侧共享层（Kotlin Multiplatform）
//
// 这里只放**纯逻辑**：端云路由决策、升级信号、前缀守卫、工具调用校验、编排、分块、向量数学、
// 检索、SSE 解析、协议客户端、评测集、JSON 门面与常量。
// 平台实现（Compose UI、OkHttp、Keystore、SQLite、ONNX、设备工具）**仍留在各端模块**——
// shared 保持"零平台依赖"，才能在 iOS/鸿蒙原样编译。
//
// 插件组合的选择（实测后定的，别再随手改）：
//   AGP 9 的 KMP 专用插件（`com.android.kotlin.multiplatform.library` + `androidLibrary {}` DSL）
//   **没有 `compileSdkMinor`** —— 而本机只装了 `platforms/android-36.1`，没有 `android-36`，
//   于是它报 "Failed to install platforms;android-36"（SDK 目录不可写，也不该让构建去装系统 SDK）。
//   改用经典组合 `com.android.library` + `androidTarget()`：`android {}` 块支持 `compileSdkMinor`，
//   与 app 模块同一套口径（`-PsekbCompileSdkMinor` 可覆盖，CI 上是 0）。
plugins {
    id("org.jetbrains.kotlin.multiplatform")
    id("com.android.kotlin.multiplatform.library")
}

// 与 app 模块同口径：本机只有 android-36.1，CI/标准 SDK 上是 android-36
val sekbCompileSdkMinor = (project.findProperty("sekbCompileSdkMinor") as String?)?.toIntOrNull() ?: 1

kotlin {
    androidLibrary {
        namespace = "com.sekb.shared"
        // 用平台号而不是字符串：该 DSL 没有 compileSdkMinor，也不接受 compileSdkVersion 字符串。
        // 本机只有 android-36.1 → 由仓库内 `.tooling/android-sdk` 提供 `platforms/android-36`
        // （软链 + 改写 source.properties），见 local.properties 与 KMP 方案文档的说明。
        compileSdk = 36
        minSdk = 26
    }

    // iOS / Mac（M5 / M8 用）——**默认不声明**，用 `-PsekbNativeTargets=true` 打开。
    //
    // 为什么做成开关（实测教训）：声明 native target 后，Gradle 配置阶段就会去准备
    // Kotlin/Native 工具链（首次约数百 MB，从 Maven 拉 `kotlin-native-prebuilt`）。
    // 那会让"只想跑 Android 单测"的日常构建变成几分钟起步，甚至撞超时。
    // 所以：Android/JVM 的日常构建保持秒级；要验 iOS 时显式开这个开关（CI 上另开 job）。
    val nativeTargets = (project.findProperty("sekbNativeTargets") as String?)?.toBoolean() ?: false
    if (nativeTargets) {
        iosArm64()
        iosSimulatorArm64()
        macosArm64()
    }

    sourceSets {
        commonMain.dependencies {
            // JSON 门面的底层：JVM 与 native 都有产物，所以能放进 commonMain
            implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.8.1")
        }
        commonTest.dependencies {
            implementation(kotlin("test"))
        }
    }
}
