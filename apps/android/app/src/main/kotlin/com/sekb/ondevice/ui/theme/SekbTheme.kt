package com.sekb.ondevice.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * 端侧宿主的主题。
 *
 * ## 为什么不用 Material3 的默认配色
 *
 * 默认是紫色（`#6750A4`），而 SEKB 网页端用的是 Ant Design 5 的**蓝 `#1677ff`**。
 * 端侧和网页端是同一个产品的两个入口，配色不一致会让人以为是两个东西——
 * 所以这里把主色对齐网页端，并按 AntD 的用法定了几个语义色
 * （成功 = 本机完成，警告 = 已上云，错误 = 失败）。
 *
 * ## 语义色为什么重要（不是装饰）
 *
 * 端侧产品有一条硬边界：**设备专属数据不许出端**。所以"这次在哪算的"必须一眼可辨——
 * [SekbColors.edge]、[SekbColors.cloud]、[SekbColors.onDeviceGreen] 就是为它服务的，
 * 不允许在界面其它地方随意复用（否则"绿色"不再等于"在本机算的"）。
 */
object SekbColors {
    /** 主色：与网页端 AntD `token.colorPrimary` 一致 */
    val Primary = Color(0xFF1677FF)
    val PrimaryContainer = Color(0xFFE6F0FF)
    val PrimaryDark = Color(0xFF0958D9)

    /** 页面底色（AntD `colorBgLayout`）与卡片色 */
    val BgLayout = Color(0xFFF5F7FA)
    val Card = Color(0xFFFFFFFF)
    val Fill = Color(0xFFF0F2F5)
    val Border = Color(0xFFE5E7EB)

    /** 文字 */
    val TextPrimary = Color(0xFF1F2329)
    val TextSecondary = Color(0xFF8C8C8C)

    /** 语义：本机完成（AntD green-7）/ 已上云（AntD gold-7）/ 失败（AntD red-7） */
    val OnDeviceGreen = Color(0xFF389E0D)
    val CloudGold = Color(0xFFD48806)
    val DangerRed = Color(0xFFD4380D)
}

/**
 * **语义色**：只有在表达"这条在哪算的"时才用（详见 [SekbColors] 的说明）。
 *
 * ## 为什么它必须随明暗切换（踩过的真实缺陷）
 *
 * 第一版把 `SekbColors.OnDeviceGreen` 之类当**常量**直接写在页面里。
 * 结果深色模式下：输入框还是近白的 `#F0F2F5`、空状态圆形还是近白的 `#E6F0FF`、
 * 边框在深底上几乎看不见——**"深色模式"只在 `SekbTheme` 里定义了配色，而页面根本没走主题**。
 * 所以语义色也必须有明暗两套（深色下要更亮才够对比度），并通过 [LocalSekbSemantic] 取。
 */
@Immutable
data class SekbSemantic(
    /** 本机完成 */
    val onDevice: Color,
    /** 已上云 / 端侧不可用 */
    val cloud: Color,
    /** 失败 */
    val danger: Color,
)

private val LightSemantic = SekbSemantic(
    onDevice = SekbColors.OnDeviceGreen,
    cloud = SekbColors.CloudGold,
    danger = SekbColors.DangerRed,
)

private val DarkSemantic = SekbSemantic(
    // 深色底上要提亮：直接用 light 的绿/金会糊在背景里
    onDevice = Color(0xFF73D13D),
    cloud = Color(0xFFFFC53D),
    danger = Color(0xFFFF7875),
)

val LocalSekbSemantic = staticCompositionLocalOf { LightSemantic }

private val LightScheme = lightColorScheme(
    primary = SekbColors.Primary,
    onPrimary = Color.White,
    primaryContainer = SekbColors.PrimaryContainer,
    onPrimaryContainer = SekbColors.PrimaryDark,
    secondary = SekbColors.PrimaryDark,
    background = SekbColors.BgLayout,
    onBackground = SekbColors.TextPrimary,
    surface = SekbColors.Card,
    onSurface = SekbColors.TextPrimary,
    surfaceVariant = SekbColors.Fill,
    onSurfaceVariant = SekbColors.TextSecondary,
    outline = SekbColors.Border,
    outlineVariant = SekbColors.Border,
    error = SekbColors.DangerRed,
    onError = Color.White,
)

private val DarkScheme = darkColorScheme(
    primary = Color(0xFF4C9AFF),
    onPrimary = Color(0xFF00244D),
    primaryContainer = Color(0xFF11304F),
    onPrimaryContainer = Color(0xFFD6E8FF),
    background = Color(0xFF14161A),
    onBackground = Color(0xFFE6E8EB),
    surface = Color(0xFF1C1F24),
    onSurface = Color(0xFFE6E8EB),
    surfaceVariant = Color(0xFF262A31),
    onSurfaceVariant = Color(0xFF9BA1A9),
    outline = Color(0xFF3A3F47),
    error = Color(0xFFFF7875),
)

/** 字号比 M3 默认略小一档：聊天界面信息密度高，默认字号会显得"很空"。 */
private val SekbTypography = Typography().let { base ->
    base.copy(
        bodyLarge = base.bodyLarge.copy(fontSize = 15.sp, lineHeight = 22.sp),
        bodyMedium = base.bodyMedium.copy(fontSize = 14.sp, lineHeight = 21.sp),
        bodySmall = base.bodySmall.copy(fontSize = 12.sp, lineHeight = 18.sp),
        titleLarge = base.titleLarge.copy(fontSize = 19.sp, fontWeight = FontWeight.SemiBold),
        titleMedium = base.titleMedium.copy(fontSize = 16.sp, fontWeight = FontWeight.SemiBold),
        labelSmall = TextStyle(fontSize = 11.sp, lineHeight = 16.sp),
    )
}

@Composable
fun SekbTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    CompositionLocalProvider(
        LocalSekbSemantic provides if (darkTheme) DarkSemantic else LightSemantic,
    ) {
        MaterialTheme(
            colorScheme = if (darkTheme) DarkScheme else LightScheme,
            typography = SekbTypography,
            content = content,
        )
    }
}
