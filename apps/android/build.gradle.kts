// 根构建脚本：只声明插件版本，不在这里 apply（各模块自己 apply）。
//
// 版本组合是**按本机 Gradle 缓存实测选定**的（2026-09-17）：
//   Gradle 9.2.1（已在 ~/.gradle/wrapper/dists 缓存）+
//   AGP 9.0.0 + Kotlin 2.2.10（compose 编译器随 Kotlin 2.x 发布）
// 换版本前请先跑 `./gradlew :app:assembleDebug`，否则很可能要重新下载整套工具链。
plugins {
    id("com.android.application") version "9.0.0" apply false
    id("org.jetbrains.kotlin.android") version "2.2.10" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.2.10" apply false
}
