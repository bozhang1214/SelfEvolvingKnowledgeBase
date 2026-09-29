# 端侧宿主暂不开启混淆（release 也 isMinifyEnabled=false）。
# 真要开启时注意：OkHttp 与 Keystore 相关类需要保留规则。

# ── PDFBox 的**可选** JPEG2000 解码器 ────────────────────────────────────────────
# R8 报 Missing class com.gemalto.jp2.JP2Decoder（由 tom_roush PDFBox 的 JPXFilter 引用）。
# 这是 PDFBox 的**可选**依赖：只有解析 PDF 里 JPEG2000 编码的**图像**时才需要。
# 本项目的 PdfExtractor 只用 `PDFTextStripper` 抽**文本**，不走图像路径，因此
# 该缺失不可能影响我们的能力；`-dontwarn` 让 R8 安心剥离它，避免为了一个用不到的解码器
# 把包体做大（规则文件由 R8 自己生成：build/outputs/mapping/release/missing_rules.txt）。
#
# 残留限制（如实记录）：若将来要**渲染/提取 PDF 内嵌的 JPEG2000 图像**，必须加回
# `jp2-android` 依赖，否则那条路径会在运行时抛错。当前无此需求。
-dontwarn com.gemalto.jp2.JP2Decoder

# ── ONNX Runtime（R8 真机验证抓到的真问题）──────────────────────────────────────
# 现象：R8 后的 release 包跑自检时**卡在第 11 项 `rag_provider`**（前 10 项 0 FAIL），
# 无异常、无崩溃、进程存活——即 ONNX 嵌入提供者创建不出来/卡住。
# 原因：ORT 的 Java API `ai.onnxruntime.**` 通过 **JNI + 反射**加载 native 库与类，
# R8 看不到这些反射引用，会把相关类/成员剥掉或改名 → 运行时拿不到类。
# 这正是"R8 必须做一轮 E2E"的价值所在：体积数字好看不代表还能跑。
-keep class ai.onnxruntime.** { *; }
-keepclassmembers class ai.onnxruntime.** { *; }
-dontwarn ai.onnxruntime.**
