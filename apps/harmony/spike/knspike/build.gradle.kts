plugins { id("org.jetbrains.kotlin.multiplatform") }

kotlin {
    // ohosArm64 = 真机；ohosX64 = 模拟器/x86_64 开发机（本机模拟器是 x86_64，两者都要）
    ohosArm64 { binaries { sharedLib { baseName = "kn" } } }
    ohosX64 { binaries { sharedLib { baseName = "kn" } } }

    sourceSets {
        // 两个 ohos 目标共享同一份导出代码（官方 sample 的做法）
        val ohosMain by creating { dependsOn(commonMain.get()) }
        val ohosArm64Main by getting { dependsOn(ohosMain) }
        val ohosX64Main by getting { dependsOn(ohosMain) }
    }
}
