package com.sekb.ondevice.eval

/**
 * 端侧检索评测集（RFC §18.4 的验收：命中率 + 延迟）。
 *
 * 设计上的三条原则：
 * 1. **文档短、主题互不重叠**：12 篇讲不同的事，命中与否不会含糊；
 * 2. **提问尽量不复用文档原句**：否则测的是"词面匹配"，端侧嵌入的真实语义能力看不出来；
 * 3. **必须有无答案的问题**：只测"有答案时能不能命中"会掩盖另一类错误——
 *    把不相关的内容硬塞给模型（幻觉的温床）。所以留 3 条"库里没有"的问题，
 *    用来看**误召回率**。
 */
object RetrievalEvalSet {

    data class Doc(val id: String, val text: String)

    /** 一个"问题 → 期望命中的文档"的标注；`expected = null` 表示库里没有答案。 */
    data class Question(val q: String, val expected: String?, val note: String = "")

    val corpus: List<Doc> = listOf(
        Doc("doc-edge-latency", "端侧推理把计算留在本机，省掉了网络往返与云端排队，因此首字延迟更低。"),
        Doc("doc-edge-power", "端侧推理省电的主要原因是不用把数据上传下载，网络传输本身很耗电。"),
        Doc("doc-rag-local", "端侧 RAG 把知识索引放在设备上，检索过程不出网，隐私更好。"),
        Doc("doc-vector-space", "不同嵌入模型产出的向量处在不同的语义空间，即使维度相同也不能混用，否则结果会 silently 出错。"),
        Doc("doc-permission", "设备工具执行前要检查 Android 权限，未授权必须拦下并写审计，这就是越权拦截率。"),
        Doc("doc-privacy", "设备专属数据永不出端：宁可答不出来，也不能为了答得更好把原文送去云端。"),
        Doc("doc-route", "端云协同的决策依据是输入输出预算：超出端侧预算的请求直接交给云端，不必先试。"),
        Doc("doc-escalation", "端侧输出如果在半截就明显跑偏，应在前缀阶段改道云端，此时用户还没看到任何字符。"),
        Doc("doc-quantize", "量化会把模型体积压到四分之一左右，但向量会轻微变化，因此量化后的索引必须换一个空间标记。"),
        Doc("doc-onnx", "ONNX Runtime 让同一份模型能在 Android 上跑，无需 Python 运行时。"),
        Doc("doc-ann", "语料上万条之后暴力余弦会变慢，那时才需要换成近似最近邻索引。"),
        Doc("doc-tv-car", "车机与电视的交互模型和手机差别很大，焦点导航与语音优先，界面通常要单独做。"),
    )

    val questions: List<Question> = listOf(
        // —— 有答案（30 条）——
        Question("为什么端侧算比云端算响应更快？", "doc-edge-latency"),
        Question("本机推理的延迟优势从哪来？", "doc-edge-latency"),
        Question("端侧推理为什么更省电？", "doc-edge-power"),
        Question("哪些因素让端侧更省能源？", "doc-edge-power"),
        Question("把索引放在手机上检索有什么好处？", "doc-rag-local"),
        Question("离线检索对隐私的帮助是什么？", "doc-rag-local", "同文档第二种问法"),
        Question("两个维度相同的向量能不能直接比较？", "doc-vector-space"),
        Question("换了嵌入模型以后索引还能用吗？", "doc-vector-space"),
        Question("读联系人之前要做什么检查？", "doc-permission"),
        Question("工具越权时系统怎么处理？", "doc-permission"),
        Question("为什么有些数据不能在云端处理？", "doc-privacy"),
        Question("数据不出设备的边界怎么划？", "doc-privacy"),
        Question("什么样的请求应该直接交给云端？", "doc-route"),
        Question("端云路由靠什么判断走哪边？", "doc-route"),
        Question("端侧答到一半发现不对怎么办？", "doc-escalation"),
        Question("什么时候改道云端最划算？", "doc-escalation"),
        Question("模型压缩之后向量会变吗？", "doc-quantize"),
        Question("int8 量化对索引有什么影响？", "doc-quantize"),
        Question("手机上怎么跑 BERT 这类模型？", "doc-onnx"),
        Question("不装 Python 能在 Android 上推理吗？", "doc-onnx"),
        Question("语料很大以后检索会变慢吗？", "doc-ann"),
        Question("什么时候该上近似最近邻？", "doc-ann"),
        Question("为什么车机界面不能直接抄手机？", "doc-tv-car"),
        Question("电视上的交互和手机有什么不同？", "doc-tv-car"),
        Question("端侧首字更快的原因是什么？", "doc-edge-latency", "第三种问法"),
        Question("省电和网络传输有什么关系？", "doc-edge-power", "第三种问法"),
        Question("本机知识库检索会联网吗？", "doc-rag-local", "第三种问法"),
        Question("维度一样就代表语义一样吗？", "doc-vector-space", "第三种问法"),
        Question("没有权限时工具会怎么执行？", "doc-permission", "第三种问法"),
        Question("哪些数据绝对不许离开设备？", "doc-privacy", "第三种问法"),

        // —— 库里没有答案（3 条）：用来看误召回 ——
        Question("今天北京的天气怎么样？", null, "库里没有天气信息"),
        Question("帮我订一张明天去上海的高铁票。", null, "库里没有票务信息"),
        Question("我上个月的信用卡账单是多少？", null, "库里没有账单信息"),
    )

    /** 标注集规模（自检/报告里直接引用，避免各处写死数字）。 */
    val size: Int get() = questions.size
    val answerable: Int get() = questions.count { it.expected != null }
    val unanswerable: Int get() = questions.count { it.expected == null }
}
