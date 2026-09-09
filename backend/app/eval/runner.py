"""
测试运行器模块

定义评估体系的端到端测试运行逻辑，提供：
- EvalRunner：加载黄金数据集 → 构建 GraphState → 运行 LangGraph → 收集结果 → 断言检查
- EvalReport：测试运行报告数据类

运行流程：
    1. 加载黄金数据集 JSON 文件（list[dict]）
    2. 对每条用例：
       a. 构建 GraphState（基于 user_input）
       b. 运行 LangGraph 工作流
       c. 收集最终 state 与 ConversationMetrics
       d. 调用 AssertionEngine 进行断言检查
    3. 汇总生成 EvalReport

使用方式：
    from app.eval.runner import EvalRunner

    runner = EvalRunner(config, llm_factory, tool_registry, memory)
    report = await runner.run_dataset("app/eval/datasets/golden_qa.json")
    print(report.summary)
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import AppConfig
from app.core.exceptions import EvaluationError
from app.core.logging import get_logger
from app.core.utils import to_state_dict
from app.eval.assertion import AssertionEngine, AssertionResult
from app.eval.metrics import MetricsCollector
from app.graph.state import ConversationMetrics, GraphState, create_initial_state

logger = get_logger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class EvalReport:
    """
    测试运行报告。

    Attributes:
        total: 用例总数
        passed: 通过数
        failed: 失败数
        pass_rate: 通过率（0.0~1.0）
        results: 各用例的断言结果
        metrics_list: 各用例的指标
        aggregated: 聚合统计（均值、中位数、P95）
        total_latency_ms: 总耗时（毫秒）
        summary: 人类可读的摘要文本
    """

    total: int
    passed: int
    failed: int
    pass_rate: float
    results: list[AssertionResult] = field(default_factory=list)
    metrics_list: list[ConversationMetrics] = field(default_factory=list)
    aggregated: dict[str, dict[str, float]] = field(default_factory=dict)
    total_latency_ms: int = 0
    summary: str = ""


# ============================================================
# 测试运行器
# ============================================================

class EvalRunner:
    """
    测试运行器。

    加载黄金数据集，对每条用例运行 LangGraph 工作流，收集指标，
    执行断言检查，并生成 EvalReport。

    Attributes:
        config: 应用全局配置
        llm_factory: LLM 工厂实例
        tool_registry: 工具注册表
        memory: 短期记忆实例
        assertion_engine: 断言引擎
        metrics_collector: 指标收集器
    """

    def __init__(
        self,
        config: AppConfig,
        llm_factory: Any,
        tool_registry: Any,
        memory: Any,
    ) -> None:
        """
        初始化测试运行器。

        Args:
            config: 应用全局配置
            llm_factory: LLM 工厂实例
            tool_registry: 工具注册表
            memory: 短期记忆实例
        """
        self.config: AppConfig = config
        self.llm_factory: Any = llm_factory
        self.tool_registry: Any = tool_registry
        self.memory: Any = memory
        self.assertion_engine: AssertionEngine = AssertionEngine(config)
        self.metrics_collector: MetricsCollector = MetricsCollector()
        # 延迟构建图（仅在首次运行时构建）
        self._graph: Any = None

    async def run_dataset(self, dataset_path: str) -> EvalReport:
        """
        运行黄金数据集。

        Args:
            dataset_path: 黄金数据集 JSON 文件路径

        Returns:
            EvalReport 报告

        Raises:
            EvaluationError: 数据集加载失败
        """
        logger.info("评估数据集运行开始", dataset_path=dataset_path)

        # 加载数据集
        test_cases = self._load_dataset(dataset_path)
        if not test_cases:
            raise EvaluationError(
                "黄金数据集为空",
                details={"dataset_path": dataset_path},
            )

        # 构建图
        graph = self._get_graph()

        # 顺序执行每条用例
        results: list[AssertionResult] = []
        metrics_list: list[ConversationMetrics] = []
        start_time = time.time()

        for case in test_cases:
            test_id = str(case.get("test_id", "unknown"))
            try:
                result, metrics = await self._run_single_case(graph, case)
                results.append(result)
                if metrics:
                    metrics_list.append(metrics)

                logger.info(
                    "用例执行完成",
                    test_id=test_id,
                    passed=result.passed,
                )
            except Exception as e:
                logger.error(
                    "用例执行失败",
                    test_id=test_id,
                    error=str(e),
                    exc_info=True,
                )
                results.append(
                    AssertionResult(
                        test_id=test_id,
                        passed=False,
                        error=f"{type(e).__name__}: {e}",
                    )
                )

        total_latency_ms = int((time.time() - start_time) * 1000)
        passed_count = sum(1 for r in results if r.passed)
        total = len(results)
        pass_rate = passed_count / total if total > 0 else 0.0

        # 聚合统计
        aggregated = self.metrics_collector.aggregate(metrics_list)

        summary = (
            f"评估完成: {passed_count}/{total} 通过 "
            f"(通过率 {pass_rate:.1%}), 总耗时 {total_latency_ms}ms"
        )

        logger.info(
            "评估数据集运行完成",
            total=total,
            passed=passed_count,
            failed=total - passed_count,
            pass_rate=pass_rate,
            total_latency_ms=total_latency_ms,
        )

        return EvalReport(
            total=total,
            passed=passed_count,
            failed=total - passed_count,
            pass_rate=pass_rate,
            results=results,
            metrics_list=metrics_list,
            aggregated=aggregated,
            total_latency_ms=total_latency_ms,
            summary=summary,
        )

    async def run_single(self, test_case: dict) -> AssertionResult:
        """
        运行单条测试用例。

        Args:
            test_case: 测试用例字典，需包含 input 和 expected 字段

        Returns:
            AssertionResult 断言结果
        """
        graph = self._get_graph()
        result, _ = await self._run_single_case(graph, test_case)
        return result

    # ============================================================
    # 内部辅助方法
    # ============================================================

    def _load_dataset(self, dataset_path: str) -> list[dict]:
        """
        加载黄金数据集 JSON 文件。

        Args:
            dataset_path: JSON 文件路径

        Returns:
            测试用例列表

        Raises:
            EvaluationError: 文件不存在或解析失败
        """
        path = Path(dataset_path)
        if not path.exists():
            raise EvaluationError(
                f"黄金数据集文件不存在: {dataset_path}",
                details={"path": dataset_path},
            )

        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise EvaluationError(
                f"黄金数据集 JSON 解析失败: {e}",
                details={"path": dataset_path},
            ) from e

        if not isinstance(data, list):
            raise EvaluationError(
                f"黄金数据集应为 JSON 数组，实际类型: {type(data).__name__}",
                details={"path": dataset_path},
            )

        return data

    def _get_graph(self) -> Any:
        """
        构建（或返回缓存的）LangGraph 工作流。

        Returns:
            编译后的 LangGraph 可执行图
        """
        if self._graph is not None:
            return self._graph

        # 延迟导入以避免循环依赖
        from app.agents.strategies.factory import create_reflection_strategy
        from app.graph.builder import GraphBuilder

        reflection_strategy = create_reflection_strategy(self.config)
        builder = GraphBuilder(
            config=self.config,
            llm_factory=self.llm_factory,
            tool_registry=self.tool_registry,
            memory=self.memory,
            reflection_strategy=reflection_strategy,
        )
        self._graph = builder.build()
        logger.info("LangGraph 工作流已构建并缓存")
        return self._graph

    async def _run_single_case(
        self,
        graph: Any,
        test_case: dict,
    ) -> tuple[AssertionResult, ConversationMetrics | None]:
        """
        运行单条测试用例的内部实现。

        流程：
            1. 构建 GraphState
            2. 记录开始时间
            3. 运行 LangGraph
            4. 计算端到端延迟并写入 metrics
            5. 提取 ConversationMetrics
            6. 执行断言检查

        Args:
            graph: 已编译的 LangGraph 工作流
            test_case: 测试用例字典

        Returns:
            二元组 (AssertionResult, ConversationMetrics | None)，
            metrics 在 LangGraph 执行失败时为 None
        """
        test_id = str(test_case.get("test_id", "unknown"))
        user_input = str(test_case.get("input", ""))
        expected = test_case.get("expected", {})
        if not isinstance(expected, dict):
            expected = {}

        if not user_input:
            raise EvaluationError(
                f"测试用例缺少 input 字段: {test_id}",
                details={"test_id": test_id},
            )

        # 构建 GraphState
        conversation_id = f"eval-{test_id}-{uuid.uuid4().hex[:8]}"
        state = create_initial_state(
            user_input=user_input,
            conversation_id=conversation_id,
            trace_id=conversation_id,
        )

        # P0-1 修复：注入 per-request 快照，确保评估场景各用例指标相互隔离
        state["llm_stats_snapshot"] = self.llm_factory.snapshot_stats()

        # 记录开始时间
        start_time = time.time()

        # 运行 LangGraph
        try:
            final_state = await graph.ainvoke(state)
        except Exception as e:
            raise EvaluationError(
                f"LangGraph 执行失败: {e}",
                details={"test_id": test_id, "error": str(e)},
            ) from e

        # 计算端到端延迟
        latency_ms = int((time.time() - start_time) * 1000)

        # 补充延迟到 metrics（final_state 可能是 dict 或包含 state 的对象）
        final_state_dict = self._extract_state_dict(final_state)
        existing_metrics = final_state_dict.get("metrics") or {}
        existing_metrics["e2e_latency_ms"] = latency_ms
        final_state_dict["metrics"] = existing_metrics

        # 提取 ConversationMetrics
        metrics = self.metrics_collector.from_state(final_state_dict)

        # 在 expected 中注入 test_id 以便 AssertionResult 使用
        expected_with_id = {**expected, "test_id": test_id}

        # 执行断言
        result = self.assertion_engine.check(final_state_dict, expected_with_id)
        return result, metrics

    def _extract_state_dict(self, final_state: Any) -> GraphState:
        """
        从 LangGraph 调用结果中提取 GraphState dict。

        实现收敛至 core.utils.to_state_dict（strict=True），失败抛 EvaluationError。
        """
        if isinstance(final_state, dict):
            return final_state
        try:
            return to_state_dict(final_state, strict=True)  # type: ignore[return-value]
        except ValueError as e:
            raise EvaluationError(
                f"无法从 LangGraph 返回值提取 state: {e}",
                details={"type": type(final_state).__name__},
            ) from e
