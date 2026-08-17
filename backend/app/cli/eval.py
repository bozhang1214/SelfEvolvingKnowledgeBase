"""
评估模式模块

实现 run_eval 函数，驱动端到端评估流程：
    1. 创建 EvalRunner（复用 AppContext 中已初始化的组件）
    2. 运行黄金数据集
    3. 使用 EvalReporter 生成 Markdown 报告
    4. 保存报告到指定路径
    5. 打印摘要到控制台（rich 美化）

使用方式：
    from app.cli.eval import run_eval
    from app.core.bootstrap import initialize_app

    ctx = await initialize_app("config.yaml")
    await run_eval("app/eval/datasets/golden_qa.json",
                   "tests/reports/eval_report.md", ctx)
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.core.bootstrap import AppContext
from app.core.exceptions import SEKBError
from app.core.logging import get_logger
from app.eval.reporter import EvalReporter
from app.eval.runner import EvalReport, EvalRunner

logger = get_logger(__name__)
console = Console()


async def run_eval(
    dataset_path: str,
    output_path: str,
    ctx: AppContext,
) -> EvalReport:
    """
    运行评估流程并生成报告。

    流程：
        1. 创建 EvalRunner（注入 LLM 工厂、工具注册表、短期记忆）
        2. 调用 run_dataset 顺序执行所有用例
        3. 用 EvalReporter 生成 Markdown 报告
        4. 保存报告到 output_path
        5. 在控制台打印摘要

    Args:
        dataset_path: 黄金数据集 JSON 文件路径
        output_path: 报告输出路径（Markdown）
        ctx: 应用上下文（提供所有已初始化的组件）

    Returns:
        EvalReport：评估报告对象

    Raises:
        EvaluationError: 数据集加载或运行失败
        SEKBError: 其他业务异常
    """
    console.print(
        Panel(
            f"[bold]数据集:[/bold] {dataset_path}\n"
            f"[bold]报告路径:[/bold] {output_path}",
            title="评估任务",
            border_style="blue",
        )
    )

    # 1. 创建 EvalRunner
    runner = EvalRunner(
        config=ctx.config,
        llm_factory=ctx.llm_factory,
        tool_registry=ctx.tool_registry,
        memory=ctx.memory,
    )

    # 2. 运行数据集
    try:
        with console.status("[bold green]正在运行评估用例...[/bold green]"):
            report = await runner.run_dataset(dataset_path)
    except SEKBError as e:
        console.print(f"[red]评估失败: {e.message}[/red]")
        logger.error("评估运行失败", error=str(e), exc_info=True)
        raise
    except Exception as e:
        console.print(f"[red]评估意外错误: {e}[/red]")
        logger.error("评估意外异常", error=str(e), exc_info=True)
        raise

    # 3. 生成 Markdown 报告
    reporter = EvalReporter()
    markdown_body = reporter.generate_report(report.results, report.metrics_list)
    full_report = _build_full_report(markdown_body, report, ctx)

    # 4. 保存报告
    try:
        reporter.save_report(full_report, output_path)
    except SEKBError as e:
        console.print(f"[red]报告保存失败: {e.message}[/red]")
        raise

    console.print(f"[green]评估报告已保存到:[/green] {output_path}")

    # 5. 打印摘要
    _print_summary(report, output_path)

    return report


# ============================================================
# 内部辅助函数
# ============================================================

def _build_full_report(
    markdown_body: str,
    report: EvalReport,
    ctx: AppContext,
) -> str:
    """
    构建完整的 Markdown 报告。

    在 EvalReporter 生成的内容前添加运行环境元信息，
    在末尾追加 EvalRunner 摘要。

    Args:
        markdown_body: EvalReporter 生成的 Markdown 主体
        report: EvalReport 对象
        ctx: 应用上下文

    Returns:
        完整的 Markdown 报告字符串
    """
    header = (
        "# 自迭代个人知识库 Agent 评估报告\n\n"
        "## 运行环境\n\n"
        f"- 应用版本: {ctx.config.app.version}\n"
        f"- 环境: {ctx.config.app.environment}\n"
        f"- LLM Provider: {ctx.config.llm.provider}\n"
        f"- 反思策略: {ctx.config.reflection.policy}\n"
        f"- 数据集: 见下方用例详情\n\n"
        f"## 摘要\n\n"
        f"{report.summary}\n\n"
        f"- 总耗时: {report.total_latency_ms} ms\n"
        f"- 通过率: {report.pass_rate:.1%}\n\n"
    )
    return header + markdown_body


def _print_summary(report: EvalReport, output_path: str) -> None:
    """
    在控制台打印评估摘要。

    使用 rich 表格美化输出通过率、用例数、延迟等关键指标。

    Args:
        report: 评估报告对象
        output_path: 报告保存路径（用于提示用户）
    """
    # 顶部摘要
    console.print()
    console.print(
        Panel(
            f"[bold]{report.summary}[/bold]\n\n"
            f"报告已保存: [cyan]{output_path}[/cyan]",
            title="评估完成",
            border_style="green" if report.pass_rate >= 0.8 else "yellow",
        )
    )

    # 关键指标表
    table = Table(title="关键指标", show_lines=False)
    table.add_column("指标", style="cyan", no_wrap=True)
    table.add_column("值", style="green")

    table.add_row("用例总数", str(report.total))
    table.add_row("通过数", str(report.passed))
    table.add_row("失败数", str(report.failed))
    table.add_row("通过率", f"{report.pass_rate:.1%}")
    table.add_row("总耗时(ms)", str(report.total_latency_ms))
    console.print(table)

    # 失败用例列表
    failed_results = [r for r in report.results if not r.passed]
    if failed_results:
        fail_table = Table(title="失败用例")
        fail_table.add_column("Test ID", style="cyan")
        fail_table.add_column("错误信息", style="red")
        for r in failed_results:
            error_text = r.error or "(无详情)"
            fail_table.add_row(r.test_id, error_text)
        console.print(fail_table)

    # 聚合指标
    if report.aggregated:
        agg_table = Table(title="聚合指标统计")
        agg_table.add_column("指标", style="cyan")
        agg_table.add_column("均值", justify="right")
        agg_table.add_column("中位数", justify="right")
        agg_table.add_column("P95", justify="right")
        for name, stats in report.aggregated.items():
            agg_table.add_row(
                name,
                f"{stats.get('mean', 0):.4f}",
                f"{stats.get('median', 0):.4f}",
                f"{stats.get('p95', 0):.4f}",
            )
        console.print(agg_table)

    console.print()
