package com.sekb.ondevice.ui

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * **UI 配色纪律**的机械检查。
 *
 * ## 为什么值得写（这不是洁癖，是踩过的缺陷）
 *
 * 第一版把 `SekbColors.Fill` / `PrimaryContainer` / `Border` 等当常量**直接写在页面里**，
 * 于是 `SekbTheme` 里那份 `DarkScheme` **根本没人用**——深色模式下输入框还是近白的
 * `#F0F2F5`、空状态圆形还是近白的 `#E6F0FF`、边框在深底上几乎看不见。
 * "我定义了深色配色"这句话当时是**不成立的**。
 *
 * 这类回归**在浅色模式下完全看不出来**，只能靠机制守：**页面里不许出现硬编码色常量**，
 * 一律走 `MaterialTheme.colorScheme` / `LocalSekbSemantic`。
 * 允许的例外只有 `theme/SekbTheme.kt`（那里本来就是定义颜色的地方）。
 *
 * 与仓库里既有的 `doc_guard`、mypy 基线门禁、`lint_shell_unicode_vars.sh` 同一思路：
 * 靠人记住的规则会失效，靠机器检查的不会。
 */
class ThemeColorDisciplineTest {

    /** 从工作目录往上找 UI 源码目录（不同 Gradle 版本的 test working dir 不保证一致）。 */
    private fun uiSourceDir(): File? {
        var dir: File? = File(".").absoluteFile
        repeat(6) {
            val d = dir ?: return null
            val cand = File(d, "src/main/kotlin/com/sekb/ondevice/ui")
            if (cand.isDirectory) return cand
            val cand2 = File(d, "app/src/main/kotlin/com/sekb/ondevice/ui")
            if (cand2.isDirectory) return cand2
            dir = d.parentFile
        }
        return null
    }

    @Test
    fun `screens use theme colors instead of hardcoded palette constants`() {
        val ui = uiSourceDir()
        // 找不到目录就**明确失败**而不是静默通过：静默通过等于这条检查不存在
        assertTrue(
            "找不到 UI 源码目录（test working dir 变了？），本检查无法执行",
            ui != null,
        )

        val offenders = mutableListOf<String>()
        var checked = 0
        ui!!.listFiles { f -> f.isFile && f.name.endsWith(".kt") }?.sorted()?.forEach { f ->
            // theme/ 是定义颜色的地方，豁免
            if (f.parentFile.name == "theme") return@forEach
            checked++
            f.readLines().forEachIndexed { i, line ->
                val code = line.substringBefore("//")
                // 允许在**注释**里提到常量名（大量设计说明都写在注释里）
                if (code.contains("SekbColors.")) {
                    offenders += "${f.name}:${i + 1}  ${line.trim().take(90)}"
                }
            }
        }

        assertTrue("没扫到任何文件，检查形同虚设", checked >= 3)
        assertTrue(
            "页面里出现了硬编码色常量（深色模式下会不跟随主题）：\n" + offenders.joinToString("\n") +
                "\n请改用 MaterialTheme.colorScheme.* 或 LocalSekbSemantic.current.*",
            offenders.isEmpty(),
        )
    }

    /** 纯逻辑文件不许依赖 Compose：它们要能被 JVM 单测直接跑（`Markdown`/`PlaneExplain` 都是这样）。 */
    @Test
    fun `pure logic files stay free of compose imports`() {
        val ui = uiSourceDir() ?: return
        for (name in listOf("Markdown.kt", "PlaneExplain.kt", "TimeFmt.kt")) {
            val f = File(ui, name)
            assertTrue("$name 不存在了？检查需要更新", f.isFile)
            val bad = f.readLines().filter { it.startsWith("import androidx.compose") }
            assertTrue("$name 不应依赖 Compose：$bad", bad.isEmpty())
        }
    }
}
