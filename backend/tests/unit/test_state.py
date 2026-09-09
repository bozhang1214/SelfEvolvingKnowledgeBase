"""
Graph State 的单元测试

测试内容：
- create_initial_state 返回正确字段
- IntentType 枚举值
- TaskStatus 枚举值
- ReflectionResult 枚举值
"""

from __future__ import annotations

from app.graph.state import (
    IntentType,
    ReflectionResult,
    TaskStatus,
    create_initial_state,
)

# ============================================================
# create_initial_state 测试
# ============================================================

class TestCreateInitialState:
    """测试 create_initial_state 函数"""

    def test_returns_correct_user_input(self):
        """测试 user_input 字段正确设置"""
        state = create_initial_state("你好世界", "conv-123")
        assert state["user_input"] == "你好世界"

    def test_returns_correct_conversation_id(self):
        """测试 conversation_id 字段正确设置"""
        state = create_initial_state("测试", "conv-abc-456")
        assert state["conversation_id"] == "conv-abc-456"

    def test_default_user_id(self):
        """测试默认 user_id 为 'default'"""
        state = create_initial_state("测试", "conv-1")
        assert state["user_id"] == "default"

    def test_custom_user_id(self):
        """测试自定义 user_id"""
        state = create_initial_state("测试", "conv-1", user_id="alice")
        assert state["user_id"] == "alice"

    def test_auto_generated_trace_id(self):
        """测试 trace_id 自动生成（UUID 格式）"""
        state = create_initial_state("测试", "conv-1")
        trace_id = state["trace_id"]
        assert trace_id is not None
        assert len(trace_id) > 0
        # UUID 字符串格式：8-4-4-4-12
        parts = trace_id.split("-")
        assert len(parts) == 5

    def test_custom_trace_id(self):
        """测试自定义 trace_id"""
        state = create_initial_state("测试", "conv-1", trace_id="custom-trace")
        assert state["trace_id"] == "custom-trace"

    def test_trace_id_unique(self):
        """测试多次调用生成不同的 trace_id"""
        state1 = create_initial_state("测试", "conv-1")
        state2 = create_initial_state("测试", "conv-2")
        assert state1["trace_id"] != state2["trace_id"]

    def test_initial_collections_empty(self):
        """测试初始化时集合字段为空"""
        state = create_initial_state("测试", "conv-1")
        assert state["conversation_history"] == []
        assert state["compressed_summaries"] == []
        assert state["pre_retrieval_results"] == []
        assert state["task_steps"] == []
        assert state["tool_calls"] == []
        assert state["errors"] == []

    def test_initial_defaults(self):
        """测试初始化时默认值正确"""
        state = create_initial_state("测试", "conv-1")
        assert state["intent"] == ""
        assert state["intent_confidence"] == 0.0
        assert state["needs_clarification"] is False
        assert state["clarification_question"] == ""
        assert state["task_complexity"] == 0.0
        assert state["replan_count"] == 0
        assert state["should_replan"] is False
        assert state["final_answer"] == ""
        assert state["summary"] == ""
        assert state["importance_score"] == 0.0
        assert state["should_persist"] is False

    def test_custom_history(self):
        """测试传入自定义对话历史"""
        from langchain_core.messages import HumanMessage

        history = [HumanMessage(content="之前的对话")]
        state = create_initial_state("新问题", "conv-1", history=history)
        assert state["conversation_history"] == history
        assert len(state["conversation_history"]) == 1

    def test_returns_graph_state_type(self):
        """测试返回值是 GraphState（TypedDict 的实例，即 dict）"""
        state = create_initial_state("测试", "conv-1")
        assert isinstance(state, dict)


# ============================================================
# IntentType 枚举测试
# ============================================================

class TestIntentType:
    """测试意图类型枚举"""

    def test_chitchat_value(self):
        """测试闲聊意图值"""
        assert IntentType.CHITCHAT.value == "chitchat"

    def test_kb_strict_value(self):
        """测试严格知识库意图值"""
        assert IntentType.KB_STRICT.value == "kb_strict"

    def test_kb_prefer_value(self):
        """测试优先知识库意图值"""
        assert IntentType.KB_PREFER.value == "kb_prefer"

    def test_web_default_value(self):
        """测试默认联网意图值"""
        assert IntentType.WEB_DEFAULT.value == "web_default"

    def test_task_plan_value(self):
        """测试复杂任务规划意图值"""
        assert IntentType.TASK_PLAN.value == "task_plan"

    def test_clarify_value(self):
        """测试澄清意图值"""
        assert IntentType.CLARIFY.value == "clarify"

    def test_all_intents_count(self):
        """测试意图枚举总数为 6"""
        assert len(list(IntentType)) == 6

    def test_intent_is_string_enum(self):
        """测试意图枚举是字符串枚举"""
        assert isinstance(IntentType.CHITCHAT, str)
        assert IntentType.CHITCHAT == "chitchat"


# ============================================================
# TaskStatus 枚举测试
# ============================================================

class TestTaskStatus:
    """测试任务状态枚举"""

    def test_pending_value(self):
        assert TaskStatus.PENDING.value == "pending"

    def test_in_progress_value(self):
        assert TaskStatus.IN_PROGRESS.value == "in_progress"

    def test_done_value(self):
        assert TaskStatus.DONE.value == "done"

    def test_failed_value(self):
        assert TaskStatus.FAILED.value == "failed"

    def test_skipped_value(self):
        assert TaskStatus.SKIPPED.value == "skipped"

    def test_all_statuses_count(self):
        """测试任务状态枚举总数为 5"""
        assert len(list(TaskStatus)) == 5


# ============================================================
# ReflectionResult 枚举测试
# ============================================================

class TestReflectionResult:
    """测试反思结果枚举"""

    def test_pass_value(self):
        assert ReflectionResult.PASS.value == "pass"

    def test_needs_replan_value(self):
        assert ReflectionResult.NEEDS_REPLAN.value == "needs_replan"

    def test_needs_rewrite_value(self):
        assert ReflectionResult.NEEDS_REWRITE.value == "needs_rewrite"

    def test_all_results_count(self):
        """测试反思结果枚举总数为 3"""
        assert len(list(ReflectionResult)) == 3
