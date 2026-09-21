package com.sekb.ondevice.tools

import com.sekb.ondevice.model.ToolResult
import com.sekb.ondevice.rag.Retriever

/**
 * 设备工具 `kb_search`：检索**本机**知识索引。
 *
 * ⚠️ `deviceOnly` 是**构造期注入**的，绝不允许从模型参数里来：
 * 一份资料是不是"设备专属"是数据属性，由 App 决定；让模型自己声明"我这次不算泄露"
 * 等于把隐私闸门的钥匙交给被约束的一方。
 */
object KbSearchTool {

    const val NAME = "kb_search"

    fun create(
        retriever: Retriever,
        deviceOnly: Boolean = false,
        defaultTopK: Int = 5,
    ): DeviceTool = DeviceTool(
        name = NAME,
        description = "在本机知识库中检索与问题相关的片段（离线，数据不出设备）",
        requiredPermission = null,
        args = mapOf("query" to "检索问题（自然语言）", "top_k" to "返回条数，默认 $defaultTopK"),
        requiredArgs = setOf("query"),
        run = { args ->
            val query = args["query"].orEmpty().trim()
            if (query.isEmpty()) {
                ToolResult(ok = false, reason = "query 不能为空")
            } else {
                val topK = args["top_k"]?.toIntOrNull()?.coerceIn(1, 20) ?: defaultTopK
                val outcome = retriever.retrieve(query, topK = topK, deviceOnly = deviceOnly)
                Retriever.toToolResult(outcome, retriever)
            }
        },
    )
}
