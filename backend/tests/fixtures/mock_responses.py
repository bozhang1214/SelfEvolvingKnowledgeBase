"""
Mock LLM 响应数据模块

定义各 Agent 节点的 Mock LLM 响应，用于测试中替代真实 API 调用。
所有响应结构与 app.graph.state 中定义的 TypedDict 保持一致。
"""

from __future__ import annotations

# ============================================================
# Supervisor（意图识别）的 Mock 响应
# ============================================================

SUPERVISOR_RESPONSE_CHITCHAT = {
    "intent": "chitchat",
    "confidence": 0.95,
    "reasoning": "用户在打招呼，属于闲聊",
    "needs_clarification": False,
    "clarification_question": "",
}

SUPERVISOR_RESPONSE_WEB_DEFAULT = {
    "intent": "web_default",
    "confidence": 0.88,
    "reasoning": "用户询问实时信息，需要联网搜索",
    "needs_clarification": False,
    "clarification_question": "",
}

SUPERVISOR_RESPONSE_CLARIFY = {
    "intent": "clarify",
    "confidence": 0.45,
    "reasoning": "用户输入模糊，需要进一步澄清",
    "needs_clarification": True,
    "clarification_question": "请问您是想了解最新的信息，还是查询已有知识？",
}

SUPERVISOR_RESPONSE_KB_STRICT = {
    "intent": "kb_strict",
    "confidence": 0.92,
    "reasoning": "用户明确要求基于知识库回答",
    "needs_clarification": False,
    "clarification_question": "",
}


# ============================================================
# Planner（任务规划）的 Mock 响应
# ============================================================

PLANNER_RESPONSE = {
    "steps": [
        {
            "step_id": 1,
            "description": "搜索最新信息",
            "tool": "web_search",
            "tool_input": {"query": "测试查询"},
            "depends_on": [],
            "status": "pending",
            "result": None,
            "error": None,
            "latency_ms": 0,
        },
        {
            "step_id": 2,
            "description": "基于搜索结果生成回答",
            "tool": "llm_generate",
            "tool_input": {"prompt": "根据以下信息回答用户问题"},
            "depends_on": [1],
            "status": "pending",
            "result": None,
            "error": None,
            "latency_ms": 0,
        },
    ],
    "complexity": 0.3,
    "reasoning": "简单问题，两步即可完成",
}


# ============================================================
# Critic（审查者）的 Mock 响应
# ============================================================

CRITIC_RESPONSE_PASS = {
    "passed": True,
    "result": "pass",
    "groundedness_score": 0.9,
    "coherence_score": 8.5,
    "relevance_score": 0.88,
    "issues": [],
    "suggestions": [],
    "reasoning": "答案质量良好，通过审查",
    "model_used": "deepseek-chat",
}

CRITIC_RESPONSE_FAIL = {
    "passed": False,
    "result": "needs_replan",
    "groundedness_score": 0.4,
    "coherence_score": 4.0,
    "relevance_score": 0.5,
    "issues": ["答案缺乏事实依据", "逻辑不连贯"],
    "suggestions": ["重新搜索获取更准确的信息", "组织答案结构"],
    "reasoning": "答案锚定度不足，需要重规划",
    "model_used": "deepseek-chat",
}


# ============================================================
# Scribe（记录者）的 Mock 响应
# ============================================================

SCRIBE_RESPONSE = {
    "summary": "用户询问了测试相关问题，Agent 通过搜索后给出了回答。",
    "importance_score": 0.5,
    "should_persist": True,
    "final_answer": "这是最终的测试回答。",
}


# ============================================================
# Executor 工具调用记录的 Mock 数据
# ============================================================

TOOL_CALL_WEB_SEARCH_SUCCESS = {
    "tool_name": "web_search",
    "input": {"query": "测试查询"},
    "output": {"results": [{"title": "测试结果", "snippet": "测试内容"}]},
    "success": True,
    "latency_ms": 500,
    "error": None,
}

TOOL_CALL_WEB_SEARCH_FAILURE = {
    "tool_name": "web_search",
    "input": {"query": "测试查询"},
    "output": None,
    "success": False,
    "latency_ms": 300,
    "error": "网络超时",
}
