import Foundation
import SharedCore

/// **仅供 UI 视觉验证**的演示数据种子（由 `SEKB_DEMO_SEED=1` 打开，默认关闭）。
///
/// ## 为什么需要它
///
/// 截图验证要回答的问题是"布局对不对、数据有没有渲染出来"，而**空列表证明不了这些**：
/// 历史页空着、文档页空着时，看起来和"这一页没实现"一模一样。
/// 所以这里造两条会话（含执行位置徽章、引用来源、Markdown 正文、一次真实的升级记录）
/// 与两份文档（一份文本 + 一份真的走 PDFKit 抽取的 PDF）。
///
/// ## 三条纪律
///
/// 1. **默认关闭**：不设 `SEKB_DEMO_SEED` 就一行都不执行，正常使用不受影响；
/// 2. **不伪造数字**：PDF 是走真实 `PdfTextExtractor` 抽取的，嵌入走真实 `KnowledgeIndex`
///    （空间戳、切片数都是真的算出来的），会话里的分数只是"看起来合理的示例"，
///    但**界面不会把示例分数当成评测结论**——评测结论只来自自检的 `rag_eval_calibrate`；
/// 3. **id 固定**（`demo-1` / `demo-2` / 固定文件名）：重复运行时覆盖而不是堆积重复条目。
enum SekbDemoSeed {

    static var isRequested: Bool {
        ProcessInfo.processInfo.environment["SEKB_DEMO_SEED"] == "1"
    }

    static func applyIfRequested(container: SekbContainer, store: FileConversationStore) {
        guard isRequested else { return }
        seedDocuments(container: container)
        seedConversations(store: store)
        NSLog("%@ demo_seed_applied — 演示数据已写入（仅 SEKB_DEMO_SEED=1 时）", SekbPlatform.selfTestPrefix)
    }

    // MARK: - 文档

    private static func seedDocuments(container: SekbContainer) {
        _ = container.knowledgeIndex.ingest(
            sourceId: "端侧 RAG 说明.md",
            text: """
            # 端侧 RAG 为什么更省电

            检索与嵌入都在设备上完成，数据不出网，因此隐私更好、延迟更低。
            向量空间戳决定了两份向量能不能混用：空间不同必须重算，而不是复用缓存。

            ## 阈值是标定出来的

            0.40 是在本机实际加载的嵌入空间上标定的：阈值调到 0.20 时，
            "库里没有答案"的问题会被强行回答（误召回 3/3），那是幻觉的来源。
            """,
            name: "端侧 RAG 说明.md",
            sizeBytes: 320,
            deviceOnly: true)

        // 真的走一遍 PDFKit：样本与 Android 端侧自检用的是同一份文件（随包分发）
        if let pdfPath = Bundle.main.path(forResource: "sample-text.pdf", ofType: nil),
           let data = try? Data(contentsOf: URL(fileURLWithPath: pdfPath)) {
            switch SekbDocumentImporter.decide(bytes: data, name: "sample-text.pdf") {
            case let .ok(name, text, sizeBytes):
                _ = container.knowledgeIndex.ingest(sourceId: name, text: text, name: name,
                                                    sizeBytes: sizeBytes, deviceOnly: true)
            case let .rejected(_, reason):
                NSLog("%@ demo_seed_pdf_rejected — %@", SekbPlatform.selfTestPrefix, reason)
            }
        }
    }

    // MARK: - 会话

    private static func seedConversations(store: FileConversationStore) {
        let now = TimeFmt.nowMillis()

        // demo-1：一段更早的对话（历史列表里要有"不止一条"才看得出排序与相对时间）
        let earlier = Conversation(
            id: "demo-1",
            title: "端侧检索为什么省电",
            updatedAtMillis: now - 2 * 3600_000,
            messages: [
                StoredMessage(fromUser: true, text: "端侧推理为什么更省电？", plane: "", model: "",
                              escalated: 0, reason: "", escalateReason: "", sources: []),
                StoredMessage(fromUser: false, text: "因为推理与检索都在本机完成，省掉了网络往返。",
                              plane: "edge", model: "qwen3.5-2b", escalated: 0,
                              reason: "edge_preferred", escalateReason: "", sources: []),
            ])
        store.save(c: earlier)

        // demo-2：当前会话——包含 Markdown 正文、引用来源、绿色徽章与**一次真实形态的升级**
        let markdownAnswer = """
        ## 核心结论

        本机资料里关于阈值有两条要点：

        - **0.40** 是当前空间的标定值（误召回 0/3）
        - 调到 0.20 会开始硬答，那是幻觉的来源

        ```swift
        let retriever = Retriever(provider: provider, store: store, minScore: 0.40)
        ```

        > 阈值必须按实际加载的空间标定，不能跨空间照抄。
        """
        let two = Conversation(
            id: "demo-2",
            title: "本机知识库里有什么",
            updatedAtMillis: now - 60_000,
            messages: [
                StoredMessage(fromUser: true, text: "我本机的知识库里有什么？", plane: "", model: "",
                              escalated: 0, reason: "", escalateReason: "", sources: []),
                StoredMessage(fromUser: false, text: markdownAnswer, plane: "edge",
                              model: "qwen3.5-2b", escalated: 0, reason: "edge_preferred",
                              escalateReason: "",
                              sources: [
                                  StoredSource(sourceId: "端侧 RAG 说明.md", score: 0.63,
                                               snippet: "检索与嵌入都在设备上完成，数据不出网，因此隐私更好、延迟更低。"),
                                  StoredSource(sourceId: "sample-text.pdf", score: 0.51,
                                               snippet: "这是自检用的样本 PDF，用于验证文本层抽取。"),
                              ]),
                StoredMessage(fromUser: true, text: "那要是端侧答不好呢？", plane: "", model: "",
                              escalated: 0, reason: "", escalateReason: "", sources: []),
                // 升级形态：徽章显示"已上云（端侧不达标）"，**解释必须与徽章同源**
                StoredMessage(fromUser: false,
                              text: "端侧这次中途开始重复，已经改由云端重答了一遍。",
                              plane: "cloud", model: "qwen3.5-4b", escalated: 1,
                              reason: "edge_preferred", escalateReason: "degenerate", sources: []),
            ])
        store.save(c: two)
        // 当前会话指针指向 demo-2：聊天页恢复出来的就是这一段（含徽章与来源）
        store.saveCurrentId(id: "demo-2")
    }
}
