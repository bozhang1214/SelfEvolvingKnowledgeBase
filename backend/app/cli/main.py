"""
CLI 主入口模块

基于 Typer 框架定义命令组，提供以下子命令：
    - chat：启动交互式聊天
    - eval：运行评估测试
    - rag-eval：运行 RAG 检索质量评测
    - news-backfill：回填指定日期的科技资讯日报
    - health：健康检查

每个命令都通过 app.core.bootstrap.initialize_app 装配依赖，
执行完毕后调用 shutdown_app 释放资源。

入口点（见 pyproject.toml）：
    sekb = "app.cli.main:app"

使用方式：
    sekb chat
    sekb chat --conv-id <uuid> --config config.yaml
    sekb eval --dataset app/eval/datasets/golden_qa.json \\
              --output tests/reports/eval_report.md
    sekb news-backfill 2026-09-17
    sekb health
"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.core.bootstrap import AppContext, initialize_app, shutdown_app
from app.core.exceptions import SEKBError
from app.core.logging import get_logger

logger = get_logger(__name__)
console = Console()

app = typer.Typer(
    name="sekb",
    help="自迭代个人知识库 Agent - 基于 LangGraph 的多智能体知识助手",
    no_args_is_help=True,
    add_completion=False,
)


# ============================================================
# 子命令：chat
# ============================================================

@app.command()
def chat(
    conversation_id: Optional[str] = typer.Option(
        None, "--conv-id", "-c", help="会话 ID（不指定则自动创建新会话）"
    ),
    config_path: str = typer.Option(
        "config.yaml", "--config", help="配置文件路径"
    ),
) -> None:
    """启动交互式聊天。"""
    from app.cli.chat import ChatSession

    async def _run() -> None:
        try:
            ctx = await initialize_app(config_path)
        except SEKBError as e:
            console.print(f"[red]初始化失败: {e.message}[/red]")
            raise typer.Exit(code=1)
        try:
            session = ChatSession(
                config=ctx.config,
                llm_factory=ctx.llm_factory,
                tool_registry=ctx.tool_registry,
                memory=ctx.memory,
                storage=ctx.storage,
                reflection_strategy=ctx.reflection_strategy,
                knowledge_ingester=ctx.knowledge_ingester,
                knowledge_base=ctx.knowledge_base,
            )
            await session.initialize()
            await session.run_interactive(initial_conv_id=conversation_id)
        finally:
            await shutdown_app(ctx)

    asyncio.run(_run())


# ============================================================
# 子命令：eval
# ============================================================

@app.command()
def eval(
    dataset: str = typer.Option(
        "app/eval/datasets/golden_qa.json",
        "--dataset",
        "-d",
        help="测试数据集 JSON 文件路径",
    ),
    output: str = typer.Option(
        "tests/reports/eval_report.md",
        "--output",
        "-o",
        help="评估报告输出路径（Markdown）",
    ),
    config_path: str = typer.Option(
        "config.yaml", "--config", help="配置文件路径"
    ),
) -> None:
    """运行评估测试。"""
    from app.cli.eval import run_eval

    async def _run() -> None:
        try:
            ctx = await initialize_app(config_path)
        except SEKBError as e:
            console.print(f"[red]初始化失败: {e.message}[/red]")
            raise typer.Exit(code=1)
        try:
            await run_eval(dataset, output, ctx)
        except SEKBError as e:
            console.print(f"[red]评估失败: {e.message}[/red]")
            raise typer.Exit(code=1)
        except Exception as e:
            console.print(f"[red]评估意外错误: {e}[/red]")
            raise typer.Exit(code=1)
        finally:
            await shutdown_app(ctx)

    asyncio.run(_run())


# ============================================================
# 子命令：rag-eval
# ============================================================

@app.command()
def rag_eval(
    dataset: str = typer.Option(
        "app/eval/datasets/rag_golden.json",
        "--dataset",
        "-d",
        help="RAG 检索黄金数据集 JSON 文件路径",
    ),
    output: str = typer.Option(
        "tests/reports/rag_eval_report.md",
        "--output",
        "-o",
        help="评测报告输出路径（Markdown）",
    ),
    config_path: str = typer.Option(
        "config.yaml", "--config", help="配置文件路径"
    ),
) -> None:
    """运行 RAG 检索质量评测（context recall/precision、faithfulness、answer relevance）。"""
    from app.cli.rag_eval import run_rag_eval

    async def _run() -> None:
        try:
            ctx = await initialize_app(config_path)
        except SEKBError as e:
            console.print(f"[red]初始化失败: {e.message}[/red]")
            raise typer.Exit(code=1)
        try:
            await run_rag_eval(dataset, output, ctx)
        except SEKBError as e:
            console.print(f"[red]评测失败: {e.message}[/red]")
            raise typer.Exit(code=1)
        except Exception as e:
            console.print(f"[red]评测意外错误: {e}[/red]")
            raise typer.Exit(code=1)
        finally:
            await shutdown_app(ctx)

    asyncio.run(_run())


# ============================================================
# 子命令：news-backfill
# ============================================================

@app.command(name="news-backfill")
def news_backfill(
    dates: list[str] = typer.Argument(
        ..., help="要回填的日报日期，可多个（YYYY-MM-DD）"
    ),
    force: bool = typer.Option(
        True, "--force/--no-force", help="该日已有日报时是否覆盖重建"
    ),
    config_path: str = typer.Option(
        "config.yaml", "--config", help="配置文件路径"
    ),
) -> None:
    """回填指定日期的科技资讯日报（内容窗口取该自然日）。

    为什么需要它：``refresh()`` 的默认目标是「今天」，启动补跑也只补今天，所以
    **历史上缺失的日报无法自动恢复**（2026-09-17 因定时任务空转而永久丢失）。
    用法示例：

        sekb news-backfill 2026-09-17
        sekb news-backfill 2026-09-17 2026-09-18 --no-force
    """
    async def _run() -> None:
        try:
            ctx = await initialize_app(config_path)
        except SEKBError as e:
            console.print(f"[red]初始化失败: {e.message}[/red]")
            raise typer.Exit(code=1)
        try:
            agent = ctx.news_agent
            if agent is None:
                console.print("[red]科技资讯未启用（news.enabled=false），无法回填[/red]")
                raise typer.Exit(code=1)
            failed = 0
            for day in dates:
                try:
                    result = await agent.refresh(force=force, day=day)
                except ValueError as e:  # 日期参数本身不合法
                    console.print(f"[red]{e}[/red]")
                    failed += 1
                    continue
                if result.get("skipped"):
                    console.print(f"[yellow]{day}：已存在，跳过（要覆盖请加 --force）[/yellow]")
                else:
                    console.print(
                        f"[green]{day}：回填完成[/green] "
                        f"（抓取 {result.get('fetched', 0)} 条 / 入选 {result.get('filtered', 0)} 条）"
                    )
            if failed:
                raise typer.Exit(code=1)
        except SEKBError as e:
            console.print(f"[red]回填失败: {e.message}[/red]")
            raise typer.Exit(code=1)
        finally:
            await shutdown_app(ctx)

    asyncio.run(_run())


# ============================================================
# 子命令：health
# ============================================================

@app.command()
def health(
    config_path: str = typer.Option(
        "config.yaml", "--config", help="配置文件路径"
    ),
) -> None:
    """健康检查：验证 LLM、工具、Graph 是否就绪。"""
    async def _run() -> None:
        try:
            ctx = await initialize_app(config_path)
        except SEKBError as e:
            console.print(f"[red]初始化失败: {e.message}[/red]")
            raise typer.Exit(code=1)
        try:
            await _run_health_check(ctx)
        finally:
            await shutdown_app(ctx)

    asyncio.run(_run())


async def _run_health_check(ctx: AppContext) -> None:
    """
    执行健康检查并打印结果。

    检查项：
        - LLM API 可达性（调用 health_check 发送 ping）
        - 工具注册表健康状态（MCP ping 或直接封装模式判断）
        - Graph 工作流是否已编译

    Args:
        ctx: 应用上下文
    """
    console.print(
        Panel(
            f"[bold]应用:[/bold] {ctx.config.app.name} "
            f"v{ctx.config.app.version}\n"
            f"[bold]环境:[/bold] {ctx.config.app.environment}\n"
            f"[bold]LLM Provider:[/bold] {ctx.config.llm.provider}",
            title="健康检查",
            border_style="blue",
        )
    )

    table = Table(title="检查项")
    table.add_column("组件", style="cyan", no_wrap=True)
    table.add_column("状态", justify="center")
    table.add_column("详情", style="dim")

    # 1. LLM 检查
    try:
        llm_ok = await ctx.llm_factory.health_check()
        status = "[green]✓ 健康[/green]" if llm_ok else "[red]✗ 异常[/red]"
        detail = "" if llm_ok else "LLM API 不可达，请检查 API Key 与网络"
    except Exception as e:
        llm_ok = False
        status = "[red]✗ 异常[/red]"
        detail = str(e)
    table.add_row("LLM", status, detail)

    # 2. 工具检查
    try:
        tools_health = await ctx.tool_registry.health_check()
        all_tools_ok = all(tools_health.values()) if tools_health else False
        status = "[green]✓ 健康[/green]" if all_tools_ok else "[yellow]! 降级[/yellow]"
        detail = ", ".join(
            f"{name}={'ok' if ok else 'fail'}"
            for name, ok in tools_health.items()
        ) or "无工具"
    except Exception as e:
        all_tools_ok = False
        status = "[red]✗ 异常[/red]"
        detail = str(e)
    table.add_row("工具", status, detail)

    # 3. Graph 检查
    graph_ok = ctx.graph is not None
    status = "[green]✓ 已编译[/green]" if graph_ok else "[red]✗ 未编译[/red]"
    table.add_row("Graph", status, "" if graph_ok else "工作流未构建")

    console.print(table)

    # 总体结论
    if llm_ok and graph_ok:
        console.print("[bold green]系统就绪[/bold green]")
    else:
        console.print("[bold red]系统存在异常，请检查上方详情[/bold red]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
