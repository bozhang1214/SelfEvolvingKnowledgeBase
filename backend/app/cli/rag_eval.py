"""RAG 检索质量评测 CLI。

驱动 ``RagEvalRunner``：检索 → 生成 → 四指标 LLM 裁判 → Markdown 报告。

使用方式：
    from app.cli.rag_eval import run_rag_eval

    await run_rag_eval("app/eval/datasets/rag_golden.json",
                       "tests/reports/rag_eval_report.md", ctx)
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from app.core.bootstrap import AppContext
from app.core.exceptions import SEKBError
from app.core.logging import get_logger
from app.eval.rag_runner import RagEvalRunner

logger = get_logger(__name__)
console = Console()


async def run_rag_eval(
    dataset_path: str,
    output_path: str,
    ctx: AppContext,
) -> None:
    """运行 RAG 检索质量评测并保存 Markdown 报告。"""
    console.print(
        Panel(
            f"[bold]数据集:[/bold] {dataset_path}\n[bold]报告路径:[/bold] {output_path}",
            title="RAG 检索质量评测",
            border_style="blue",
        )
    )

    if ctx.vector_store is None:
        console.print("[yellow]L3 知识库未启用，评测将跳过检索环节。[/yellow]")

    runner = RagEvalRunner(
        vector_store=ctx.vector_store,
        llm_factory=ctx.llm_factory,
        config=ctx.config,
    )

    try:
        with console.status("[bold green]正在评测检索质量...[/bold green]"):
            report = await runner.run_dataset(dataset_path)
    except SEKBError as e:
        console.print(f"[red]评测失败: {e.message}[/red]")
        logger.error("RAG 评测运行失败", error=str(e), exc_info=True)
        raise
    except Exception as e:
        console.print(f"[red]评测意外错误: {e}[/red]")
        logger.error("RAG 评测意外异常", error=str(e), exc_info=True)
        raise

    markdown = report.to_markdown()
    try:
        from pathlib import Path

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
    except OSError as e:
        raise SEKBError(f"报告保存失败: {e}") from e

    console.print(f"[green]RAG 评测报告已保存到:[/green] {output_path}")
    if report.aggregated:
        console.print("[bold]聚合均值:[/bold]")
        for name, v in report.aggregated.items():
            console.print(f"  {name}: {v:.3f}")
