"""
指标收集器模块

定义评估体系的量化指标收集与等级评估逻辑，提供：
- 从 GraphState 提取 ConversationMetrics
- 按配置阈值评估指标等级（good / warn / bad）
- 转为 JSONL 格式（一行 JSON）用于离线分析
- 聚合多轮对话的均值、中位数、P95 统计

使用方式：
    from app.eval.metrics import MetricsCollector, MetricLevel
    from app.graph.state import create_initial_state

    collector = MetricsCollector()
    metrics = collector.from_state(state)
    level = collector.evaluate_threshold("intent_confidence", 0.92, thresholds)
"""

from __future__ import annotations

import json
import math
import statistics
from enum import Enum
from typing import Any

from app.core.config import MetricThreshold
from app.core.exceptions import EvaluationError
from app.core.logging import get_logger
from app.graph.state import ConversationMetrics, GraphState

logger = get_logger(__name__)


# ============================================================
# 枚举定义
# ============================================================

class MetricLevel(str, Enum):
    """指标等级枚举"""

    GOOD = "good"
    WARN = "warn"
    BAD = "bad"


# ============================================================
# 指标收集器
# ============================================================

class MetricsCollector:
    """
    指标收集器。

    负责从 GraphState 提取量化指标、按阈值评估等级，
    以及对多轮对话指标进行聚合统计（均值、中位数、P95）。

    所有方法均为纯函数式（无副作用），可安全并发使用。
    """

    # 数值型指标字段名（用于聚合统计）
    NUMERIC_METRIC_FIELDS: tuple[str, ...] = (
        "intent_confidence",
        "plan_step_count",
        "replan_count",
        "tool_success_rate",
        "answer_groundedness",
        "critic_coherence_score",
        "answer_relevance",
        "e2e_latency_ms",
        "total_input_tokens",
        "total_output_tokens",
        "total_cost_usd",
    )

    def from_state(self, state: GraphState) -> ConversationMetrics:
        """
        从 GraphState 提取 ConversationMetrics。

        优先使用 state.metrics 字段（由 Scribe 写入），
        若缺失则从其他字段实时计算补充。

        Args:
            state: LangGraph 全局状态

        Returns:
            完整的 ConversationMetrics 字典

        Raises:
            EvaluationError: state 不包含必要字段
        """
        if not isinstance(state, dict):
            raise EvaluationError(
                "state 必须是 dict 类型",
                details={"state_type": type(state).__name__},
            )

        # 优先使用已存在的 metrics
        existing = state.get("metrics") or {}

        # 从其他字段补充
        evaluation = state.get("evaluation", {}) or {}
        tool_calls = state.get("tool_calls", []) or []

        if tool_calls:
            success_count = sum(
                1 for tc in tool_calls if tc.get("success")
            )
            tool_success_rate = success_count / len(tool_calls)
        else:
            tool_success_rate = existing.get("tool_success_rate", 1.0)

        metrics: ConversationMetrics = ConversationMetrics(
            intent_confidence=float(
                existing.get("intent_confidence", state.get("intent_confidence", 0.0))
            ),
            intent_type=str(
                existing.get("intent_type", state.get("intent", ""))
            ),
            plan_step_count=int(
                existing.get("plan_step_count", len(state.get("task_steps", [])))
            ),
            replan_count=int(
                existing.get("replan_count", state.get("replan_count", 0))
            ),
            tool_success_rate=float(tool_success_rate),
            answer_groundedness=float(
                existing.get(
                    "answer_groundedness",
                    evaluation.get("groundedness_score", 0.0),
                )
            ),
            critic_coherence_score=float(
                existing.get(
                    "critic_coherence_score",
                    evaluation.get("coherence_score", 0.0),
                )
            ),
            answer_relevance=float(
                existing.get(
                    "answer_relevance",
                    evaluation.get("relevance_score", 0.0),
                )
            ),
            e2e_latency_ms=int(existing.get("e2e_latency_ms", 0)),
            total_input_tokens=int(existing.get("total_input_tokens", 0)),
            total_output_tokens=int(existing.get("total_output_tokens", 0)),
            total_cost_usd=float(existing.get("total_cost_usd", 0.0)),
            model_used=list(existing.get("model_used", [])),
        )
        return metrics

    def evaluate_threshold(
        self,
        metric_name: str,
        value: float,
        thresholds: MetricThreshold | dict[str, Any] | None,
    ) -> str:
        """
        评估单个指标值所属等级。

        等级判定规则（适用于"越大越好"的指标，如置信度）：
        - value >= good → "good"
        - value >= warn → "warn"
        - 否则 → "bad"

        对于"越小越好"的指标（如延迟、重规划次数），调用方应在传入前
        翻转阈值（即 good < warn），或使用本方法返回值后自行解释。
        本实现统一采用 "value >= good → good; value >= warn → warn; else bad"。

        Args:
            metric_name: 指标名称（用于日志）
            value: 指标数值
            thresholds: 阈值配置，可为 MetricThreshold 或 dict（含 good/warn 字段）

        Returns:
            MetricLevel 枚举值（"good" / "warn" / "bad"）

        Raises:
            EvaluationError: 阈值配置非法
        """
        if thresholds is None:
            # 无阈值配置，默认视为 good
            return MetricLevel.GOOD.value

        # 兼容 MetricThreshold 与 dict
        if isinstance(thresholds, MetricThreshold):
            good = thresholds.good
            warn = thresholds.warn
        elif isinstance(thresholds, dict):
            good = thresholds.get("good")
            warn = thresholds.get("warn")
        else:
            raise EvaluationError(
                f"阈值配置类型非法: {type(thresholds).__name__}",
                details={"metric_name": metric_name},
            )

        if good is None or warn is None:
            raise EvaluationError(
                f"阈值配置缺少 good/warn 字段: {thresholds}",
                details={"metric_name": metric_name},
            )

        try:
            value_f = float(value)
            good_f = float(good)
            warn_f = float(warn)
        except (TypeError, ValueError) as e:
            raise EvaluationError(
                f"指标值或阈值无法转为 float: value={value}, good={good}, warn={warn}",
                details={"metric_name": metric_name},
            ) from e

        # 统一规则：越大越好
        # 对于"越小越好"的指标（如 latency），config 中通常 good < warn，
        # 此时 value <= good → good；value <= warn → warn；else bad
        if good_f <= warn_f:
            # 越小越好
            if value_f <= good_f:
                return MetricLevel.GOOD.value
            elif value_f <= warn_f:
                return MetricLevel.WARN.value
            else:
                return MetricLevel.BAD.value
        else:
            # 越大越好
            if value_f >= good_f:
                return MetricLevel.GOOD.value
            elif value_f >= warn_f:
                return MetricLevel.WARN.value
            else:
                return MetricLevel.BAD.value

    def to_jsonl(self, metrics: ConversationMetrics) -> str:
        """
        将单条 ConversationMetrics 转为 JSONL 格式（一行 JSON）。

        Args:
            metrics: 单轮对话指标

        Returns:
            JSON 字符串（单行，无换行符）
        """
        try:
            return json.dumps(metrics, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as e:
            raise EvaluationError(f"metrics 序列化为 JSON 失败: {e}") from e

    def aggregate(
        self, metrics_list: list[ConversationMetrics]
    ) -> dict[str, dict[str, float]]:
        """
        对多轮对话指标进行聚合统计。

        对每个数值型指标计算均值、中位数、P95。

        Args:
            metrics_list: 多轮对话的指标列表

        Returns:
            聚合统计字典，结构为：
            {
                "intent_confidence": {"mean": 0.85, "median": 0.88, "p95": 0.92},
                ...
            }
            空列表时返回空字典。
        """
        if not metrics_list:
            return {}

        result: dict[str, dict[str, float]] = {}

        for field in self.NUMERIC_METRIC_FIELDS:
            values: list[float] = []
            for m in metrics_list:
                if not isinstance(m, dict):
                    continue
                if field not in m:
                    continue
                try:
                    v = float(m[field])
                    values.append(v)
                except (TypeError, ValueError):
                    continue

            if not values:
                continue

            values_sorted = sorted(values)
            n = len(values_sorted)

            mean = sum(values_sorted) / n
            median = statistics.median(values_sorted)

            # P95 计算：使用 nearest-rank 方法
            if n == 1:
                p95 = values_sorted[0]
            else:
                # nearest-rank: ceil(0.95 * n) - 1，裁剪到 [0, n-1]
                rank = max(0, min(n - 1, math.ceil(0.95 * n) - 1))
                p95 = values_sorted[rank]

            result[field] = {
                "mean": round(mean, 6),
                "median": round(median, 6),
                "p95": round(p95, 6),
            }

        return result
