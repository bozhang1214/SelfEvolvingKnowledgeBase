"""
报告生成器模块

定义评估体系的报告生成与对比逻辑，提供：
- 生成 Markdown 格式的评估报告（总体通过率、各项指标统计、失败用例详情）
- 保存报告到指定路径
- 与基线（baseline）对比，检测回归（通过率下降超 5% 标记为高危）

使用方式：
    from app.eval.reporter import EvalReporter
    from app.eval.metrics import MetricsCollector

    reporter = EvalReporter()
    report = reporter.generate_report(results, metrics_list)
    reporter.save_report(report, "data/eval_reports/report.md")
    diff = reporter.compare_with_baseline(current, baseline)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.exceptions import EvaluationError
from app.core.logging import get_logger
from app.eval.assertion import AssertionResult
from app.eval.metrics import MetricsCollector

logger = get_logger(__name__)


# ============================================================
# 报告生成器
# ============================================================

class EvalReporter:
    """
    评估报告生成器。

    生成 Markdown 格式的评估报告，支持与基线对比检测回归。

    报告包含：
    - 总体通过率
    - 各项指标的均值、中位数、P95
    - 失败用例详情
    - 与基线的回归检测（通过率下降超 5% 标记为高危）
    """

    # 通过率下降阈值（5%），超过此值标记为高危回归
    PASS_RATE_DROP_THRESHOLD: float = 0.05

    def __init__(self) -> None:
        """初始化报告生成器。"""
        self.metrics_collector: MetricsCollector = MetricsCollector()

    def generate_report(
        self,
        results: list[AssertionResult],
        metrics_list: list[Any] | None = None,
    ) -> str:
        """
        生成 Markdown 格式的评估报告。

        Args:
            results: 断言结果列表
            metrics_list: 指标列表（可选，用于聚合统计）

        Returns:
            Markdown 格式的报告字符串
        """
        if not results:
            return "# 评估报告\n\n无评估结果。\n"

        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed
        pass_rate = passed / total if total > 0 else 0.0

        lines: list[str] = []
        lines.append("# 评估报告")
        lines.append("")
        lines.append("## 总体概览")
        lines.append("")
        lines.append(f"- 用例总数: **{total}**")
        lines.append(f"- 通过数: **{passed}**")
        lines.append(f"- 失败数: **{failed}**")
        lines.append(f"- 通过率: **{pass_rate:.1%}**")
        lines.append("")

        # 聚合指标统计
        if metrics_list:
            aggregated = self.metrics_collector.aggregate(metrics_list)
            if aggregated:
                lines.append("## 指标统计")
                lines.append("")
                lines.append("| 指标 | 均值 | 中位数 | P95 |")
                lines.append("|------|------|--------|-----|")
                # 按固定顺序展示
                metric_order = [
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
                ]
                for name in metric_order:
                    if name in aggregated:
                        stats = aggregated[name]
                        lines.append(
                            f"| {name} | {stats['mean']:.4f} "
                            f"| {stats['median']:.4f} | {stats['p95']:.4f} |"
                        )
                # 其他未在 metric_order 中的指标
                for name, stats in aggregated.items():
                    if name in metric_order:
                        continue
                    lines.append(
                        f"| {name} | {stats['mean']:.4f} "
                        f"| {stats['median']:.4f} | {stats['p95']:.4f} |"
                    )
                lines.append("")

        # 失败用例详情
        failed_results = [r for r in results if not r.passed]
        if failed_results:
            lines.append("## 失败用例详情")
            lines.append("")
            for r in failed_results:
                lines.append(f"### {r.test_id}")
                lines.append("")
                if r.error:
                    lines.append(f"**错误信息**: {r.error}")
                    lines.append("")
                if r.metric_results:
                    lines.append("**指标结果**:")
                    lines.append("")
                    lines.append("| 指标 | 值 | 等级 |")
                    lines.append("|------|----|------|")
                    for metric_name, (value, level) in r.metric_results.items():
                        lines.append(f"| {metric_name} | {value:.4f} | {level} |")
                    lines.append("")
        else:
            lines.append("## 失败用例详情")
            lines.append("")
            lines.append("无失败用例 🎉")
            lines.append("")

        return "\n".join(lines)

    def save_report(self, report: str, path: str) -> None:
        """
        保存报告到指定路径。

        自动创建父目录。若文件已存在则覆盖。

        Args:
            report: Markdown 格式的报告内容
            path: 目标文件路径

        Raises:
            EvaluationError: 写入失败
        """
        try:
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(report, encoding="utf-8")
            logger.info("评估报告已保存", path=path, size=len(report))
        except OSError as e:
            raise EvaluationError(
                f"评估报告保存失败: {e}",
                details={"path": path},
            ) from e

    def compare_with_baseline(
        self,
        current: list[AssertionResult],
        baseline: list[AssertionResult],
    ) -> dict[str, Any]:
        """
        与基线对比，检测回归。

        对比维度：
        - 通过率：当前 vs 基线，下降超过 5% 标记为高危回归
        - 各用例通过状态变化

        Args:
            current: 当前运行的断言结果列表
            baseline: 基线运行的断言结果列表

        Returns:
            对比结果字典，结构为：
            {
                "current_pass_rate": float,
                "baseline_pass_rate": float,
                "pass_rate_drop": float,
                "is_high_risk": bool,
                "regressed_cases": list[str],   # 由 pass → fail 的用例 ID
                "improved_cases": list[str],    # 由 fail → pass 的用例 ID
                "summary": str,
            }
        """
        current_total = len(current)
        baseline_total = len(baseline)

        current_passed = sum(1 for r in current if r.passed)
        baseline_passed = sum(1 for r in baseline if r.passed)

        current_pass_rate = (
            current_passed / current_total if current_total > 0 else 0.0
        )
        baseline_pass_rate = (
            baseline_passed / baseline_total if baseline_total > 0 else 0.0
        )

        pass_rate_drop = baseline_pass_rate - current_pass_rate

        # 高危判定：通过率下降超过 5%
        is_high_risk = pass_rate_drop > self.PASS_RATE_DROP_THRESHOLD

        # 用例级回归分析（按 test_id 配对）
        baseline_map: dict[str, bool] = {
            r.test_id: r.passed for r in baseline
        }
        current_map: dict[str, bool] = {
            r.test_id: r.passed for r in current
        }

        regressed_cases: list[str] = []
        improved_cases: list[str] = []
        for test_id, current_passed_flag in current_map.items():
            baseline_passed_flag = baseline_map.get(test_id)
            if baseline_passed_flag is None:
                continue
            if baseline_passed_flag and not current_passed_flag:
                regressed_cases.append(test_id)
            elif not baseline_passed_flag and current_passed_flag:
                improved_cases.append(test_id)

        summary = (
            f"通过率: {current_pass_rate:.1%} (基线 {baseline_pass_rate:.1%}), "
            f"变化 {pass_rate_drop:+.1%}, "
            f"{'⚠️ 高危回归' if is_high_risk else '✅ 无高危回归'}, "
            f"回归用例 {len(regressed_cases)}, 改善用例 {len(improved_cases)}"
        )

        logger.info(
            "基线对比完成",
            current_pass_rate=current_pass_rate,
            baseline_pass_rate=baseline_pass_rate,
            pass_rate_drop=pass_rate_drop,
            is_high_risk=is_high_risk,
            regressed_count=len(regressed_cases),
            improved_count=len(improved_cases),
        )

        return {
            "current_pass_rate": round(current_pass_rate, 6),
            "baseline_pass_rate": round(baseline_pass_rate, 6),
            "pass_rate_drop": round(pass_rate_drop, 6),
            "is_high_risk": is_high_risk,
            "regressed_cases": regressed_cases,
            "improved_cases": improved_cases,
            "summary": summary,
        }
