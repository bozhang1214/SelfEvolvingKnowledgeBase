## 2026-09-20（端侧 PDF 导入：PdfBox-Android 文本层抽取）

- **改动**（端侧仓库）：
  - `ui/PdfExtractor.kt`：PdfBox-Android 2.0.27.0（Apache-2.0）抽**文本层**，
    结果分四类——有文本 / **无文本层（扫描件）** / 加密 / 解析失败，每类都给可读原因
    （端侧没有服务端兜底，界面必须说清"为什么失败"）。
  - `ui/DocumentImporter.kt`：PDF 分支（**先判 `%PDF` 魔数再判二进制**，否则 PDF 会被当二进制拒掉）、
    PDF 上限 20MB、抽取文本上限 40 万字符；判定逻辑做成可注入抽取器的纯函数，JVM 可测。
  - 单测夹具：**手写最小 PDF**（有文本 / 无文本各一份，各 ~700B，含二进制流对象），
    用 `pypdf` 交叉验证过；11 条纯逻辑测试覆盖分类与分支顺序。

- **踩到的坑（写进代码注释与 RFC §18.2.1）**：PdfBox 资源在 aar 的 assets 里，
  必须先 `PDFBoxResourceLoader.init(context)`；未初始化时抛 `ExceptionInInitializerError`
  ——**是 Error 不是 Exception**，`catch (Exception)` 拦不住、线程直接死（自检当时没有汇总行即此因）。
  已改为：启动时 init + 抽取器/自检包装捕获 **Throwable**。

- **验证**：模拟器 E2E **PASS=26 FAIL=0**：真实 PdfBox 抽取 78 字符/1 页 →
  入库 1 段 → 检索命中 `sekb-sample.pdf` 分 **0.538** → 删除后剩余切片 3。
  端侧单测 **183 用例**全绿。
