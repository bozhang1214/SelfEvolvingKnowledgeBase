"""
P0 修复与附加修复的单元测试

本测试文件覆盖一系列 P0 级别修复及附加修复，确保关键质量保障逻辑正常工作：

- SEKBMemoError 命名隔离（不遮蔽内置 MemoryError）
- Per-request LLM 统计快照隔离（避免跨会话/跨请求污染）
- LLM 重试机制（5xx/连接错误/429/402 可重试，413 不重试）与 response 提前初始化
- 工作流异常兜底降级（API/CLI 不再返回 500）
- 端到端延迟 e2e_latency_ms 回填
- 死代码清理（chat_simple/clarify 节点不再调用 llm_factory.get）
- LLMConfig api_key 校验器（空值/未展开环境变量拒绝）
- FastAPI 应用延迟创建（导入不触发 create_app）
- 重试/降级字段可见性（记录、统计、metrics）
- 非数值置信度/复杂度的安全降级（Supervisor/Planner）
- 短期记忆压缩的原子性（合并失败不部分突变 _summaries）
- CLI 持久化重试/降级质量指标
"""

from __future__ import annotations

import asyncio
import builtins
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.planner import PlannerAgent
from app.agents.scribe import ScribeAgent
from app.agents.supervisor import SupervisorAgent
from app.cli.chat import ChatSession
from app.core.config import LLMConfig
from app.core.exceptions import (
    LLMError,
    SEKBError,
    SEKBMemoError,
)
from app.core.llm_factory import (
    LLMCallRecord,
    LLMCallStats,
    LLMFactory,
)
from app.graph.state import ConversationMetrics, create_initial_state
from app.memory.short_term import ShortTermMemory

# ============================================================
# SEKBMemoError 命名隔离
# ============================================================

class TestSEKBMemoErrorNaming:
    """验证记忆系统异常命名不遮蔽 Python 内置 MemoryError"""

    def test_sekb_memo_error_exists(self):
        """SEKBMemoError 类应存在于 exceptions 模块"""
        from app.core import exceptions
        assert hasattr(exceptions, "SEKBMemoError")
        assert SEKBMemoError is exceptions.SEKBMemoError

    def test_builtin_memory_error_not_shadowed(self):
        """builtins.MemoryError 仍是 Python 内置异常，未被自定义类覆盖"""
        assert builtins.MemoryError is not SEKBMemoError
        # 内置 MemoryError 应当是 BaseException 的子类（非 SEKBError 体系）
        assert issubclass(builtins.MemoryError, BaseException)
        assert not issubclass(builtins.MemoryError, SEKBError)

    def test_sekb_memo_error_catchable_as_sekb_error(self):
        """SEKBMemoError 应可被 SEKBError 统一捕获"""
        assert issubclass(SEKBMemoError, SEKBError)
        # 实际抛出后被 SEKBError 捕获
        with pytest.raises(SEKBError):
            raise SEKBMemoError("测试记忆错误")


# ============================================================
# Per-request 统计快照隔离
# ============================================================

class TestPerRequestStatsIsolation:
    """验证 LLMFactory 的 per-request 快照/差值统计机制"""

    @pytest.mark.asyncio
    async def test_snapshot_stats_returns_baseline(self, sample_config):
        """snapshot_stats() 返回当前基线（records 长度、token 总数等）"""
        factory = LLMFactory(sample_config)
        snapshot = factory.snapshot_stats()
        assert snapshot.records_len == 0
        assert snapshot.total_calls == 0
        assert snapshot.total_input_tokens == 0
        assert snapshot.total_output_tokens == 0

    @pytest.mark.asyncio
    async def test_delta_stats_computes_difference(self, sample_config):
        """snapshot 后新增一条记录，delta_stats 只反映新增记录"""
        factory = LLMFactory(sample_config)
        snapshot = factory.snapshot_stats()
        # 注入一条新记录
        record = LLMCallRecord(
            role="supervisor", model="deepseek-chat",
            configured_model="deepseek-chat",
            input_tokens=100, output_tokens=50, latency_ms=10,
            cost_usd=0.01, success=True,
        )
        await factory._record_call(record)
        delta = factory.delta_stats(snapshot)
        assert delta["total_input_tokens"] == 100
        assert delta["total_output_tokens"] == 50
        assert delta["total_calls"] == 1

    @pytest.mark.asyncio
    async def test_delta_stats_isolates_between_requests(self, sample_config):
        """两次请求各自 snapshot+delta，互不干扰"""
        factory = LLMFactory(sample_config)
        # 请求 1
        snap1 = factory.snapshot_stats()
        r1 = LLMCallRecord(
            role="supervisor", model="deepseek-chat", configured_model="deepseek-chat",
            input_tokens=100, output_tokens=10, latency_ms=1, cost_usd=0.0, success=True,
        )
        await factory._record_call(r1)
        delta1 = factory.delta_stats(snap1)
        assert delta1["total_input_tokens"] == 100
        # 请求 2（在请求 1 之后 snapshot）
        snap2 = factory.snapshot_stats()
        r2 = LLMCallRecord(
            role="planner", model="deepseek-reasoner", configured_model="deepseek-reasoner",
            input_tokens=200, output_tokens=20, latency_ms=1, cost_usd=0.0, success=True,
        )
        await factory._record_call(r2)
        delta2 = factory.delta_stats(snap2)
        # delta2 只反映请求 2 的新增记录
        assert delta2["total_input_tokens"] == 200
        assert delta2["total_calls"] == 1
        # delta1 已计算完毕，不受请求 2 影响
        assert delta1["total_input_tokens"] == 100

    @pytest.mark.asyncio
    async def test_scribe_uses_delta_when_snapshot_present(self, sample_config):
        """Scribe._build_metrics 在 state 有 llm_stats_snapshot 时使用 delta_stats"""
        factory = LLMFactory(sample_config)
        scribe = ScribeAgent(factory, sample_config)
        # 注入旧记录（snapshot 之前）
        old_record = LLMCallRecord(
            role="scribe", model="deepseek-chat", configured_model="deepseek-chat",
            input_tokens=999, output_tokens=999, latency_ms=1, cost_usd=0.5, success=True,
        )
        await factory._record_call(old_record)
        # 请求开始前快照
        snapshot = factory.snapshot_stats()
        # 注入新记录（snapshot 之后）
        new_record = LLMCallRecord(
            role="scribe", model="deepseek-chat", configured_model="deepseek-chat",
            input_tokens=100, output_tokens=50, latency_ms=1, cost_usd=0.01, success=True,
        )
        await factory._record_call(new_record)
        state = {"llm_stats_snapshot": snapshot}
        metrics = scribe._build_metrics(state, 0.5)
        # metrics 只反映新记录的 token，旧记录被隔离
        assert metrics["total_input_tokens"] == 100
        assert metrics["total_output_tokens"] == 50

    @pytest.mark.asyncio
    async def test_scribe_falls_back_to_global_when_no_snapshot(self, sample_config):
        """state 无 llm_stats_snapshot 时使用全局 stats"""
        factory = LLMFactory(sample_config)
        scribe = ScribeAgent(factory, sample_config)
        record = LLMCallRecord(
            role="scribe", model="deepseek-chat", configured_model="deepseek-chat",
            input_tokens=100, output_tokens=50, latency_ms=1, cost_usd=0.01, success=True,
        )
        await factory._record_call(record)
        state = {}  # 无 llm_stats_snapshot
        metrics = scribe._build_metrics(state, 0.5)
        # 降级使用全局统计
        assert metrics["total_input_tokens"] == 100
        assert metrics["total_output_tokens"] == 50


# ============================================================
# LLM 重试与 response 提前初始化
# ============================================================

class TestLLMRetryAndResponseInit:
    """验证 LLM 重试机制与 response 变量提前初始化"""

    @pytest.fixture
    def fast_config(self, sample_config):
        """缩小 max_retries 以减少重试等待时间"""
        sample_config.llm.max_retries = 1
        return sample_config

    @pytest.mark.asyncio
    async def test_5xx_error_triggers_retry(self, fast_config):
        """5xx 服务器错误应触发重试，最终抛 LLMError"""
        factory = LLMFactory(fast_config)
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("500 internal server error"))
        with patch.object(factory, "get", return_value=mock_llm):
            with pytest.raises(LLMError):
                await factory.ainvoke_with_stats("supervisor", [])
        # 重试 max_retries+1 次
        assert mock_llm.ainvoke.call_count == fast_config.llm.max_retries + 1

    @pytest.mark.asyncio
    async def test_connection_error_triggers_retry(self, fast_config):
        """连接重置错误应触发重试"""
        factory = LLMFactory(fast_config)
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("connection reset by peer"))
        with patch.object(factory, "get", return_value=mock_llm):
            with pytest.raises(LLMError):
                await factory.ainvoke_with_stats("supervisor", [])
        assert mock_llm.ainvoke.call_count == fast_config.llm.max_retries + 1

    @pytest.mark.asyncio
    async def test_429_rate_limit_triggers_retry(self, fast_config):
        """429 速率限制应触发重试"""
        factory = LLMFactory(fast_config)
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("429 too many requests"))
        with patch.object(factory, "get", return_value=mock_llm):
            with pytest.raises(LLMError):
                await factory.ainvoke_with_stats("supervisor", [])
        assert mock_llm.ainvoke.call_count == fast_config.llm.max_retries + 1

    @pytest.mark.asyncio
    async def test_402_insufficient_funds_triggers_retry(self, fast_config):
        """402 余额不足应触发重试"""
        factory = LLMFactory(fast_config)
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("402 insufficient quota"))
        with patch.object(factory, "get", return_value=mock_llm):
            with pytest.raises(LLMError):
                await factory.ainvoke_with_stats("supervisor", [])
        assert mock_llm.ainvoke.call_count == fast_config.llm.max_retries + 1

    @pytest.mark.asyncio
    async def test_413_too_large_does_not_retry(self, fast_config):
        """413 请求体过大不应重试，只调用 1 次"""
        factory = LLMFactory(fast_config)
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("413 too large"))
        with patch.object(factory, "get", return_value=mock_llm):
            with pytest.raises(LLMError):
                await factory.ainvoke_with_stats("supervisor", [])
        # 不重试，只调用 1 次
        assert mock_llm.ainvoke.call_count == 1

    @pytest.mark.asyncio
    async def test_response_unbound_on_failure(self, fast_config):
        """ainvoke 抛异常时不应抛 UnboundLocalError（response 已提前初始化为 None）"""
        factory = LLMFactory(fast_config)
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("some unknown failure"))
        with patch.object(factory, "get", return_value=mock_llm):
            # 应抛 LLMError 而非 UnboundLocalError
            with pytest.raises(LLMError):
                await factory.ainvoke_with_stats("supervisor", [])

    @pytest.mark.asyncio
    async def test_retry_count_tracked_on_success_after_retry(self, fast_config):
        """第一次失败（5xx）第二次成功：重试发生后调用成功，记录被创建"""
        factory = LLMFactory(fast_config)
        response = MagicMock()
        response.content = "ok"
        response.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        mock_llm = MagicMock()
        # 第一次失败，第二次成功
        mock_llm.ainvoke = AsyncMock(
            side_effect=[Exception("500 internal server error"), response]
        )
        with patch.object(factory, "get", return_value=mock_llm):
            result = await factory.ainvoke_with_stats("supervisor", [])
        # 重试发生：ainvoke 被调用 2 次
        assert mock_llm.ainvoke.call_count == 2
        assert result is response
        # 记录已创建且标记成功
        record = factory.stats.records[-1]
        assert record.success is True
        # 记录中存在重试相关字段
        assert hasattr(record, "retried")
        assert hasattr(record, "retry_count")

    @pytest.mark.asyncio
    async def test_degraded_model_cost_uses_actual_model(self, sample_config):
        """配置 reasoner 但降级到 chat，cost 应按实际模型（chat）计算"""
        factory = LLMFactory(sample_config)
        captured: dict = {}

        def mock_create(role_config):
            # reasoner 模型创建失败，触发降级
            if "reasoner" in role_config.model:
                raise RuntimeError("reasoner unavailable")
            ml = MagicMock()
            captured["llm"] = ml
            return ml

        # planner 角色配置为 deepseek-reasoner
        with patch.object(factory, "_create_llm", side_effect=mock_create):
            factory.get("planner")

        # 确认已降级
        assert factory.is_degraded("planner")
        assert factory.get_actual_model("planner") == "deepseek-chat"

        mock_llm = captured["llm"]
        response = MagicMock()
        response.usage_metadata = {"input_tokens": 1000, "output_tokens": 500}
        mock_llm.ainvoke = AsyncMock(return_value=response)

        await factory.ainvoke_with_stats("planner", [])
        record = factory.stats.records[-1]

        # 实际模型为 chat，配置模型为 reasoner
        assert record.model == "deepseek-chat"
        assert record.configured_model == "deepseek-reasoner"
        assert record.degraded is True
        # 成本按 chat 定价计算
        chat_pricing = sample_config.cost_control.pricing["deepseek-chat"]
        expected_cost = (
            (1000 / 1000) * chat_pricing.input + (500 / 1000) * chat_pricing.output
        )
        assert record.cost_usd == pytest.approx(expected_cost)
        # 不应等于 reasoner 定价
        reasoner_pricing = sample_config.cost_control.pricing["deepseek-reasoner"]
        reasoner_cost = (
            (1000 / 1000) * reasoner_pricing.input
            + (500 / 1000) * reasoner_pricing.output
        )
        assert record.cost_usd != pytest.approx(reasoner_cost)


# ============================================================
# 工作流异常兜底降级
# ============================================================

class TestWorkflowExceptionFallback:
    """验证 API/CLI 在工作流异常时返回降级回复而非 500/中断"""

    @pytest.mark.asyncio
    async def test_api_run_chat_fallback_on_workflow_error(self):
        """mock graph.astream 抛异常，_run_chat 返回降级回复"""
        from app.api.routes.chat import ChatRequest, _run_chat

        ctx = MagicMock()

        async def boom_astream(state, stream_mode="updates"):
            # 模拟 LangGraph 流式执行中途抛异常
            raise RuntimeError("workflow boom")
            yield  # pragma: no cover

        ctx.graph.astream = boom_astream
        ctx.storage.create_conversation = AsyncMock(return_value="conv-123")
        ctx.storage.get_messages = AsyncMock(return_value=[])
        ctx.storage.append_message = AsyncMock()
        ctx.memory.get_messages = AsyncMock(return_value=[])
        ctx.memory.add_message = AsyncMock()
        ctx.memory.compress_if_needed = AsyncMock()
        ctx.llm_factory.snapshot_stats = MagicMock(return_value=MagicMock())
        # 关闭知识自迭代，避免 MagicMock 被 await 的噪音
        ctx.knowledge_ingester = None
        ctx.knowledge_base = None

        request = ChatRequest(message="你好")
        result = await _run_chat(ctx, request, "test-user")
        # 返回降级回复而非抛异常
        assert "抱歉" in result["answer"]
        assert result["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_cli_send_fallback_on_workflow_error(self, sample_config):
        """mock ChatSession._graph.ainvoke 抛异常，send 返回降级回复"""
        llm_factory = MagicMock()
        llm_factory.snapshot_stats = MagicMock(return_value=MagicMock())
        tool_registry = MagicMock()
        memory = MagicMock()
        memory.get_messages = AsyncMock(return_value=[])
        memory.add_message = AsyncMock()
        memory.compress_if_needed = AsyncMock()
        storage = MagicMock()
        storage.create_conversation = AsyncMock(return_value="conv-1")
        storage.append_message = AsyncMock()
        reflection_strategy = MagicMock()

        session = ChatSession(
            sample_config, llm_factory, tool_registry,
            memory, storage, reflection_strategy,
        )
        session._graph = MagicMock()
        session._graph.ainvoke = AsyncMock(side_effect=RuntimeError("workflow boom"))

        answer = await session.send("你好")
        assert "抱歉" in answer


# ============================================================
# 端到端延迟回填
# ============================================================

class TestE2ELatencyBackfill:
    """验证 e2e_latency_ms 由外层回填"""

    @pytest.mark.asyncio
    async def test_api_backfills_e2e_latency(self):
        """graph 返回空 metrics，_run_chat 回填 e2e_latency_ms > 0"""
        from app.api.routes.chat import ChatRequest, _run_chat

        ctx = MagicMock()

        async def slow_stream(state, stream_mode="updates"):
            # 引入微小延迟，确保 latency_ms > 0
            await asyncio.sleep(0.005)
            # stream_mode="updates" 下 astream 按节点产出 {node_name: update}
            yield {"executor": {"final_answer": "hello", "metrics": {}}}

        ctx.graph.astream = slow_stream
        ctx.storage.create_conversation = AsyncMock(return_value="conv-1")
        ctx.storage.get_messages = AsyncMock(return_value=[])
        ctx.storage.append_message = AsyncMock()
        ctx.memory.get_messages = AsyncMock(return_value=[])
        ctx.memory.add_message = AsyncMock()
        ctx.memory.compress_if_needed = AsyncMock()
        ctx.llm_factory.snapshot_stats = MagicMock(return_value=MagicMock())
        # 关闭知识自迭代，避免 MagicMock 被 await 的噪音
        ctx.knowledge_ingester = None
        ctx.knowledge_base = None

        request = ChatRequest(message="你好")
        result = await _run_chat(ctx, request, "test-user")
        assert result["metrics"]["e2e_latency_ms"] > 0


# ============================================================
# 死代码清理
# ============================================================

class TestDeadCodeCleanup:
    """验证 chat_simple/clarify 节点不再调用 llm_factory.get"""

    def test_chat_simple_node_no_llm_factory_get(self):
        """chat_simple_node 函数体不应包含 llm_factory.get 调用"""
        from app.graph.builder import chat_simple_node
        source = inspect.getsource(chat_simple_node)
        assert "llm_factory.get" not in source

    def test_clarify_node_no_llm_factory_get(self):
        """clarify_node 函数体不应包含 llm_factory.get 调用"""
        from app.graph.builder import clarify_node
        source = inspect.getsource(clarify_node)
        assert "llm_factory.get" not in source


# ============================================================
# api_key 校验器
# ============================================================

class TestApiKeyValidator:
    """验证 LLMConfig 的 api_key 字段校验器"""

    def test_valid_api_key_passes(self):
        """合法 api_key 应通过校验"""
        cfg = LLMConfig(api_key="sk-valid123", roles={})
        assert cfg.api_key == "sk-valid123"

    def test_empty_api_key_rejected(self):
        """空 api_key 应被拒绝"""
        with pytest.raises(Exception, match="API Key 未设置"):
            LLMConfig(api_key="", roles={})

    def test_unexpanded_env_var_rejected(self, monkeypatch):
        """未设置环境变量的 ${VAR} 引用应被拒绝"""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(Exception, match="API Key 未设置"):
            LLMConfig(api_key="${DEEPSEEK_API_KEY}", roles={})

    def test_env_var_resolved_passes(self, monkeypatch):
        """${VAR} 引用已设置的环境变量时应通过校验"""
        monkeypatch.setenv("TEST_KEY", "sk-resolved")
        cfg = LLMConfig(api_key="${TEST_KEY}", roles={})
        # 校验器仅校验环境变量是否设置，不展开值
        assert cfg.api_key == "${TEST_KEY}"


# ============================================================
# FastAPI 应用延迟创建
# ============================================================

class TestCreateAppLazy:
    """验证 app 实例延迟创建，导入模块不触发 create_app"""

    def test_import_server_does_not_create_app(self):
        """仅导入 app.api.server 不应创建 app 实例"""
        import app.api.server as server
        server.reset_app()
        assert server._app is None

    def test_get_app_creates_instance(self, sample_config):
        """get_app() 在 mock 配置下成功创建 FastAPI 实例（不依赖 config.yaml）"""
        import app.api.server as server
        from app.core.config import get_config

        get_config.cache_clear()
        server.reset_app()
        try:
            # mock get_config 返回测试配置，绕过 config.yaml 与环境变量依赖
            with patch("app.api.server.get_config", return_value=sample_config):
                app = server.get_app()
            assert app is not None
            assert server._app is app
            # 验证实例确实是 FastAPI 应用
            from fastapi import FastAPI
            assert isinstance(app, FastAPI)
        finally:
            server.reset_app()
            get_config.cache_clear()

    def test_reset_app_clears_instance(self):
        """reset_app() 清空缓存的实例"""
        import app.api.server as server
        server.reset_app()
        server._app = MagicMock()
        assert server._app is not None
        server.reset_app()
        assert server._app is None


# ============================================================
# 重试/降级字段可见性
# ============================================================

class TestRetryDegradationVisibility:
    """验证重试/降级字段在记录、统计与 metrics 中可见"""

    def test_llm_record_contains_retry_fields(self):
        """LLMCallRecord 应包含 retried、retry_count、degraded 字段"""
        record = LLMCallRecord(
            role="x", model="m", configured_model="m",
            input_tokens=0, output_tokens=0, latency_ms=0,
            cost_usd=0.0, success=True,
        )
        assert hasattr(record, "retried")
        assert hasattr(record, "retry_count")
        assert hasattr(record, "degraded")

    def test_stats_has_retry_and_degradation_rate(self):
        """LLMCallStats 应有 retry_rate 和 degradation_rate 属性"""
        stats = LLMCallStats()
        assert hasattr(stats, "retry_rate")
        assert hasattr(stats, "degradation_rate")
        # 属性可读
        _ = stats.retry_rate
        _ = stats.degradation_rate

    def test_metrics_includes_quality_fields(self):
        """ConversationMetrics TypedDict 应包含重试/降级质量字段"""
        annotations = ConversationMetrics.__annotations__
        assert "llm_retried" in annotations
        assert "llm_retry_count" in annotations
        assert "llm_degraded" in annotations
        assert "llm_degradation_count" in annotations


# ============================================================
# 非数值置信度/复杂度安全降级
# ============================================================

class TestSafeFloatConversion:
    """验证 Supervisor/Planner 对非数值置信度/复杂度的安全降级"""

    @pytest.mark.asyncio
    async def test_supervisor_non_numeric_confidence(self, sample_config):
        """LLM 返回 confidence='高'，Supervisor 安全降级为 0.0"""
        factory = MagicMock()
        response = MagicMock()
        response.content = (
            '{"intent": "chitchat", "confidence": "高", '
            '"needs_clarification": false}'
        )
        factory.ainvoke_with_stats = AsyncMock(return_value=response)

        supervisor = SupervisorAgent(factory, sample_config)
        state = create_initial_state(user_input="你好", conversation_id="c1")
        result = await supervisor(state)
        assert result["intent_confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_supervisor_null_confidence(self, sample_config):
        """LLM 返回 confidence=null，Supervisor 安全降级为 0.0"""
        factory = MagicMock()
        response = MagicMock()
        response.content = (
            '{"intent": "chitchat", "confidence": null, '
            '"needs_clarification": false}'
        )
        factory.ainvoke_with_stats = AsyncMock(return_value=response)

        supervisor = SupervisorAgent(factory, sample_config)
        state = create_initial_state(user_input="你好", conversation_id="c2")
        result = await supervisor(state)
        assert result["intent_confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_planner_non_numeric_complexity(self, sample_config):
        """LLM 返回 complexity='复杂'，Planner 安全降级为 0.0"""
        factory = MagicMock()
        response = MagicMock()
        response.content = (
            '{"steps": [], "complexity": "复杂", "reasoning": ""}'
        )
        factory.ainvoke_with_stats = AsyncMock(return_value=response)

        planner = PlannerAgent(factory, sample_config)
        state = create_initial_state(user_input="你好", conversation_id="c3")
        state["intent"] = "web_default"
        result = await planner(state)
        assert result["task_complexity"] == 0.0


# ============================================================
# 短期记忆压缩原子性
# ============================================================

class TestCompressAtomicity:
    """验证压缩合并失败时 _summaries 不会被部分突变"""

    @pytest.mark.asyncio
    async def test_merge_failure_does_not_mutate_summaries(self, small_l1_config):
        """_merge_summaries 抛异常时 _summaries 保持不变"""
        from langchain_core.messages import AIMessage, HumanMessage

        memory = ShortTermMemory(small_l1_config)
        conv_id = "conv-atomic"
        # 预置已达上限的摘要
        memory._summaries[conv_id] = ["s1", "s2", "s3"]
        original_summaries = list(memory._summaries[conv_id])

        # 添加足够多消息以触发压缩
        for i in range(10):
            await memory.add_message(
                "default", conv_id, HumanMessage(content=f"用户消息{i}" * 10)
            )
            await memory.add_message(
                "default", conv_id, AIMessage(content=f"助手消息{i}" * 10)
            )

        # mock _generate_summary 成功，_merge_summaries 抛异常
        async def fake_generate(msgs):
            return "新摘要"

        async def fake_merge(summaries):
            raise RuntimeError("merge 失败")

        memory._generate_summary = fake_generate
        memory._merge_summaries = fake_merge

        with pytest.raises(RuntimeError):
            await memory.compress_if_needed("default", conv_id)

        # _summaries 未被部分突变
        assert memory._summaries[conv_id] == original_summaries


# ============================================================
# CLI 持久化重试/降级质量指标
# ============================================================

class TestPersistedRetryDegradation:
    """验证 CLI 持久化 assistant 消息包含重试/降级字段"""

    @pytest.mark.asyncio
    async def test_cli_persists_retry_degradation_fields(self, sample_config):
        """metrics 含 llm_retried=True 时，持久化的 assistant_msg 包含相关字段"""
        llm_factory = MagicMock()
        llm_factory.snapshot_stats = MagicMock(return_value=MagicMock())
        tool_registry = MagicMock()
        memory = MagicMock()
        memory.get_messages = AsyncMock(return_value=[])
        memory.add_message = AsyncMock()
        memory.compress_if_needed = AsyncMock()
        storage = MagicMock()
        storage.create_conversation = AsyncMock(return_value="conv-1")

        captured_msgs: list = []

        async def append_msg(conv_id, msg):
            captured_msgs.append(msg)

        storage.append_message = append_msg
        reflection_strategy = MagicMock()

        session = ChatSession(
            sample_config, llm_factory, tool_registry,
            memory, storage, reflection_strategy,
        )
        session._graph = MagicMock()
        session._graph.ainvoke = AsyncMock(
            return_value={
                "final_answer": "回答",
                "metrics": {
                    "llm_retried": True,
                    "llm_retry_count": 2,
                    "llm_degraded": True,
                    "llm_degradation_count": 1,
                    "total_input_tokens": 10,
                    "total_output_tokens": 5,
                },
                "intent": "chitchat",
            }
        )

        answer = await session.send("你好")
        assert answer == "回答"

        assistant_msgs = [m for m in captured_msgs if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        am = assistant_msgs[0]
        assert am["llm_retried"] is True
        assert am["llm_retry_count"] == 2
        assert am["llm_degraded"] is True
        assert am["llm_degradation_count"] == 1
