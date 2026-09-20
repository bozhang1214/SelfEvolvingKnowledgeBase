// app 模块构建配置
//
// 关键取舍（都写在这里，避免下一个人"顺手改一下"踩坑）：
//
// 1. compileSdk = 36 + compileSdkMinor = 1
//    本机只装了 `platforms/android-36.1`（Android 16，API 36.1），没有 android-36。
//    AGP 9 用 `compileSdkMinor` 表达这种"次版本"SDK，缺了它会报 platform not found。
// 2. minSdk = 26：Keystore AES/GCM 的 `setIsStrongBoxBacked` 等 API 与
//    `EncryptedSharedPreferences` 替代实现都需要 23+，取 26 是为了覆盖绝大多数在用机型。
// 3. 依赖尽量用**本机 Gradle 缓存里已有**的版本（见根 build.gradle.kts 注释）：
//    少一次下载就少一次"构建卡在网络上"的可能。
// compileSdk 的**次版本**（API 36.1 = Android 16 QPR2）可覆盖：
//   本机（与模拟器镜像一致）装的是 `platforms/android-36.1` → 默认 1；
//   CI / 标准 SDK 往往只有 `android-36` → 传 `-PsekbCompileSdkMinor=0`。
// 为什么要可覆盖：不能让"某台机器上装了哪个 platform"变成构建脚本的硬依赖。
val sekbCompileSdkMinor = (project.findProperty("sekbCompileSdkMinor") as String?)?.toIntOrNull() ?: 1

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.sekb.ondevice"
    compileSdk = 36
    if (sekbCompileSdkMinor > 0) compileSdkMinor = sekbCompileSdkMinor

    defaultConfig {
        applicationId = "com.sekb.ondevice"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        // 端侧要连两个东西，都做成可配置（模拟器里宿主机是 10.0.2.2）：
        //   edgeBaseUrl → 本机/宿主机上的 OpenAI 兼容端点（Ollama）
        //   sekbBaseUrl → SEKB 云端（端云协同的"云"这一侧）
        buildConfigField("String", "DEFAULT_EDGE_BASE_URL", "\"http://10.0.2.2:11434/v1\"")
        // 云端地址可在**构建期**覆盖（联调/UI 验收用本地后端）：
        //   ./gradlew :app:assembleDebug -PsekbBaseUrl=http://10.0.2.2:8010
        // 这样就不必在模拟器上手打地址（软键盘会遮挡下方控件，脚本点击很容易错位）。
        buildConfigField(
            "String", "DEFAULT_SEKB_BASE_URL",
            "\"${project.findProperty("sekbBaseUrl") ?: "https://bos-studio.tech/sekb"}\"",
        )
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"),
                          "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}")
    }
}

// Kotlin 编译选项必须放在 android{} **外面**：AGP 9 里 `android { kotlin { … } }`
// 会重复注册名为 kotlin 的 extension，报 "extension already registered"。
kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.activity:activity-compose:1.8.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.9.4")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.4")

    val composeBom = platform("androidx.compose:compose-bom:2024.09.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")

    // 网络：SSE（流式）必须用 OkHttp 的流式 body，不能用一次性请求
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // 端侧嵌入：ONNX Runtime（跑 bge-small-zh-v1.5，与云端同空间，见 embed/OnnxBgeEmbedding.kt）
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.20.0")
    // PDF 文本抽取（Apache-2.0）。只抽文本层，不做 OCR——见 ui/PdfExtractor.kt 的说明。
    implementation("com.tom-roush:pdfbox-android:2.0.27.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.9.0")
    // JVM 单测里没有 Android 的 org.json 实现（android.jar 是桩），补一个真实现
    testImplementation("org.json:json:20240303")
}
