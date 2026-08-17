"""
断言引擎模块

定义评估体系的断言检查逻辑，提供：
- AssertionResult 数据类：单条用例的断言结果
- AssertionEngine 引擎：根据 expected dict 检查 GraphState

支持的断言规则（从 expected dict 读取）：
- intent: 检查 state.intent 是否匹配
- intent_confidence_min: 检查置信度 >= 阈值
- max_replan: 检查 replan_count <= 阈值
- max_latency_ms: 检查端到端延迟 <= 阈值
- min_groundedness: 检查 groundedness >= 阈值
- should_not_call_tools: 检查 tool_calls 为空
- should_call_web_search: 检查是否调用了 web_search

使用方式：
    from app.eval.assertion import AssertionEngine
    engine = AssertionEngine(config)
    result = engine.check(state, expected={"intent": "chitchat"})
    print(result.passed, result.metric_results)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import AppConfig
from app.core.exceptions import EvaluationError
from app.core.logging import get_logger
from app.eval.metrics import MetricsCollector
from app.graph.state import ConversationMetrics, GraphState

logger = get_logger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class AssertionResult:
    """
    单条测试用例的断言结果。

    Attributes:
        test_id: 测试用例 ID
        passed: 是否所有断言通过
        metric_results: 各指标的值与等级，结构为 {metric_name: (value, level)}
        error: 失败原因（如有），None 表示无错误
    """

    test_id: str
    passed: bool
    metric_results: dict[str, tuple[float, str]] = field(default_factory=dict)
    error: str | None = None


# ============================================================
# 断言引擎
# ============================================================

class AssertionEngine:
    """
    断言引擎。

    根据 expected dict 中定义的规则检查 GraphState，输出 AssertionResult。
    支持单条检查与批量检查。

    Attributes:
        config: 应用全局配置（用于读取指标阈值）
        metrics_collector: 指标收集器实例
    """

    # web_search 工具名（与 ToolRegistry 中注册的名字保持一致）
    WEB_SEARCH_TOOL_NAME: str = "web_search"

    def __init__(self, config: AppConfig) -> None:
        """
        初始化断言引擎。

        Args:
            config: 应用全局配置
        """
        self.config: AppConfig = config
        self.metrics_collector: MetricsCollector = MetricsCollector()

    def check(self, state: GraphState, expected: dict) -> AssertionResult:
        """
        检查单条测试用例。

        根据 expected dict 中的断言规则逐一验证 state，
        同时收集指标值与等级（基于 config.evaluation.metrics 阈值）。

        Args:
            state: LangGraph 执行后的最终状态
            expected: 断言规则字典，可包含以下键：
                - intent: 期望的意图字符串
                - intent_confidence_min: 置信度下限
                - max_replan: 重规划次数上限
                - max_latency_ms: 延迟上限（毫秒）
                - min_groundedness: 答案锚定度下限
                - should_not_call_tools: 是否期望不调用工具（bool）
                - should_call_web_search: 是否期望调用 web_search（bool）

        Returns:
            AssertionResult 数据类实例
        """
        test_id = str(expected.get("test_id", "unknown"))
        failures: list[str] = []
        metric_results: dict[str, tuple[float, str]] = {}

        try:
            # 提取指标
            metrics = self.metrics_collector.from_state(state)

            # 收集指标值与等级
            self._collect_metric_levels(metrics, metric_results)

            # ============ 逐条断言检查 ============

            # 1. intent 匹配
            if "intent" in expected:
                actual_intent = str(state.get("intent", "") or metrics.get("intent_type", ""))
                expected_intent = str(expected["intent"])
                if actual_intent != expected_intent:
                    failures.append(
                        f"intent 不匹配: expected={expected_intent}, actual={actual_intent}"
                    )

            # 2. intent_confidence_min
            if "intent_confidence_min" in expected:
                conf = float(metrics.get("intent_confidence", 0.0))
                threshold = float(expected["intent_confidence_min"])
                if conf < threshold:
                    failures.append(
                        f"intent_confidence 过低: {conf} < {threshold}"
                    )

            # 3. max_replan
            if "max_replan" in expected:
                replan = int(metrics.get("replan_count", 0))
                max_allowed = int(expected["max_replan"])
                if replan > max_allowed:
                    failures.append(
                        f"replan_count 超限: {replan} > {max_allowed}"
                    )

            # 4. max_latency_ms
            if "max_latency_ms" in expected:
                latency = int(metrics.get("e2e_latency_ms", 0))
                max_latency = int(expected["max_latency_ms"])
                if latency > max_latency:
                    failures.append(
                        f"e2e_latency_ms 超限: {latency} > {max_latency}"
                    )

            # 5. min_groundedness
            if "min_groundedness" in expected:
                groundedness = float(metrics.get("answer_groundedness", 0.0))
                min_g = float(expected["min_groundedness"])
                if groundedness < min_g:
                    failures.append(
                        f"answer_groundedness 过低: {groundedness} < {min_g}"
                    )

            # 6. should_not_call_tools
            if expected.get("should_not_call_tools") is True:
                tool_calls = state.get("tool_calls", []) or []
                if tool_calls:
                    tool_names = [tc.get("tool_name", "unknown") for tc in tool_calls]
                    failures.append(
                        f"期望不调用工具，但调用了: {tool_names}"
                    )

            # 7. should_call_web_search
            if expected.get("should_call_web_search") is True:
                tool_calls = state.get("tool_calls", []) or []
                called_tools = [tc.get("tool_name", "") for tc in tool_calls]
                if self.WEB_SEARCH_TOOL_NAME not in called_tools:
                    failures.append(
                        f"期望调用 web_search，但实际调用的工具为: {called_tools}"
                    )

        except EvaluationError as e:
            logger.error(
                "断言检查异常",
                test_id=test_id,
                error=str(e),
                exc_info=True,
            )
            return AssertionResult(
                test_id=test_id,
                passed=False,
                metric_results=metric_results,
                error=f"EvaluationError: {e.message}",
            )
        except Exception as e:
            logger.error(
                "断言检查未知异常",
                test_id=test_id,
                error=str(e),
                exc_info=True,
            )
            return AssertionResult(
                test_id=test_id,
                passed=False,
                metric_results=metric_results,
                error=f"{type(e).__name__}: {e}",
            )

        passed = len(failures) == 0
        error = "; ".join(failures) if failures else None
        return AssertionResult(
            test_id=test_id,
            passed=passed,
            metric_results=metric_results,
            error=error,
        )

    def check_batch(
        self,
        states: list[GraphState],
        expecteds: list[dict],
    ) -> list[AssertionResult]:
        """
        批量检查多条用例。

        Args:
            states: GraphState 列表
            expecteds: expected dict 列表（与 states 一一对应）

        Returns:
            AssertionResult 列表（与输入顺序一致）

        Raises:
            EvaluationError: 两个列表长度不一致
        """
        if len(states) != len(expecteds):
            raise EvaluationError(
                "states 与 expecteds 长度不一致",
                details={
                    "states_len": len(states),
                    "expecteds_len": len(expecteds),
                },
            )

        results: list[AssertionResult] = []
        for state, expected in zip(states, expecteds):
            result = self.check(state, expected)
            results.append(result)

        passed_count = sum(1 for r in results if r.passed)
        logger.info(
            "批量断言完成",
            total=len(results),
            passed=passed_count,
            failed=len(results) - passed_count,
        )
        return results

    # ============================================================
    # 内部辅助方法
    # ============================================================

    def _collect_metric_levels(
        self,
        metrics: ConversationMetrics,
        metric_results: dict[str, tuple[float, str]],
    ) -> None:
        """
        收集各指标的值与等级，写入 metric_results。

        根据 config.evaluation.metrics 中的阈值评估等级。

        Args:
            metrics: 单轮对话指标
            metric_results: 输出参数，写入 {metric_name: (value, level)}
        """
        thresholds_map = {
            "intent_confidence": self.config.evaluation.metrics.intent_confidence,
            "plan_step_count": self.config.evaluation.metrics.plan_step_count,
            "replan_count": self.config.evaluation.metrics.replan_count,
            "tool_success_rate": self.config.evaluation.metrics.tool_success_rate,
            "answer_groundedness": self.config.evaluation.metrics.answer_groundedness,
            "critic_coherence_score": self.config.evaluation.metrics.critic_coherence_score,
            "answer_relevance": self.config.evaluation.metrics.answer_relevance,
            "e2e_latency_ms": self.config.evaluation.metrics.e2e_latency_ms,
        }

        # 指标名 → metrics 字段名映射
        field_map: dict[str, str] = {
            "intent_confidence": "intent_confidence",
            "plan_step_count": "plan_step_count",
            "replan_count": "replan_count",
            "tool_success_rate": "tool_success_rate",
            "answer_groundedness": "answer_groundedness",
            "critic_coherence_score": "critic_coherence_score",
            "answer_relevance": "answer_relevance",
            "e2e_latency_ms": "e2e_latency_ms",
        }

        for metric_name, threshold in thresholds_map.items():
            field_name = field_map[metric_name]
            if field_name not in metrics:
                continue
            try:
                value = float(metrics[field_name])
            except (TypeError, ValueError):
                continue
            level = self.metrics_collector.evaluate_threshold(
                metric_name, value, threshold
            )
            metric_results[metric_name] = (value, level)

        # tool_success_rate 与 e2e_latency_ms 等"越小越好"的指标等级由
        # evaluate_threshold 内部根据 good/warn 大小关系自动判定方向。
        # 对 tool_success_rate（越大越好）也由 evaluate_threshold 处理。
