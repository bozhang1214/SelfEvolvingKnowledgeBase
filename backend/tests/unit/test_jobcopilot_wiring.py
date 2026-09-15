"""P0 接线测试：确认 SEKB 真的在用 jobcopilot 内核，而不是留着旧实现。

抽取类重构最容易出的问题是「新包装类写得漂漂亮亮，实际没人调用」。
这里用**身份断言**（is）而不是行为断言来锁死接线关系。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jobcopilot.core import logging as core_logging
from jobcopilot.core import stats as core_stats
from jobcopilot.core.analyzers import apply_plan as core_apply_plan
from jobcopilot.core.prompts import PromptResolver


def test_market_delegates_stats_to_core() -> None:
    """SEKB 的 compute_stats / classify_role 就是内核实现本身。"""
    from app.agents.job import market

    assert market.compute_stats is core_stats.compute_stats
    assert market.classify_role is core_stats.classify_role


def test_apply_plan_delegates_enrich_to_core() -> None:
    """投递计划的冷却期逻辑来自内核。"""
    from app.agents.job import apply_plan

    assert apply_plan.enrich is not core_apply_plan.enrich  # 薄封装（注入存储）
    assert apply_plan._add_months is core_apply_plan._add_months  # 纯函数直接转发


def test_generator_wraps_core_analyzer() -> None:
    """JobAnalysisGenerator 内部持有内核分析器，且加载了 7 个步骤。"""
    from app.agents.job.generator import JobAnalysisGenerator

    gen = JobAnalysisGenerator(_FakeFactory())
    assert gen.analyzer.available_steps == sorted(
        ["job_analysis", "knowledge_priority", "interview_qa", "gap_analysis",
         "resume_advice", "project_iteration", "job_strategy"]
    )


def test_sekb_prompt_dir_wins_over_bundled_base() -> None:
    """SEKB 现网 prompt/job 优先级高于包内 base（运营可热改提示词）。"""
    from app.agents.job.generator import _resolve_prompt_dir

    prompt_dir = _resolve_prompt_dir()
    assert prompt_dir is not None, "找不到 SEKB 的 prompt/job 目录"
    resolver = PromptResolver(local_dir=prompt_dir)
    assert resolver.source_of("批量职位分析.md") == "local"


def test_sekb_prompt_files_match_bundled_base() -> None:
    """SEKB 现网提示词与包内 base 逐字节一致（抽取只搬位置、不改内容）。"""
    from jobcopilot.core.prompts import base_dir

    from app.agents.job.generator import _resolve_prompt_dir

    prompt_dir = _resolve_prompt_dir()
    assert prompt_dir is not None

    mismatched = [
        name
        for name in (p.name for p in base_dir().glob("*.md"))
        if (prompt_dir / name).read_bytes() != (base_dir() / name).read_bytes()
    ]
    assert not mismatched, f"以下提示词与包内 base 不一致：{mismatched}"


def test_sekb_injects_its_logger_into_core() -> None:
    """SEKB 已把内核日志接进自家 structlog 管道（含脱敏处理器）。

    必须在 import ``app.agents.job`` 之后读取 ``core_logging._factory``——
    工厂是模块级可变量，早读会拿到默认值（这里刻意不缓存到模块顶层）。
    """
    import app.agents.job  # noqa: F401  (导入即完成注入)
    from app.core.logging import get_logger

    assert core_logging._factory is get_logger


def test_kernel_does_not_depend_on_sekb() -> None:
    """零循环依赖：内核源码里不得 **import** SEKB 的 ``app`` 包。

    注意这里是「导入」检查而不是子串检查：早先的实现只判断源码里是否出现
    ``"app."``，于是任何含该子串的**普通变量名**都会导致误报——例如内核 HTTP
    双传输里的 ``http_app.router`` / ``sse_app.routes``。那种误报会让人误以为
    内核反向依赖了 SEKB，实际只是命名巧合。改用正则匹配真正的导入语句。
    """
    import re

    import jobcopilot

    pkg = Path(jobcopilot.__file__).resolve().parent
    # 匹配 `from app...` / `import app...`（含缩进，覆盖函数内延迟导入）
    pattern = re.compile(r"^[ \t]*(?:from|import)[ \t]+app(?:[.\s]|$)", re.MULTILINE)
    offenders = [
        str(p.relative_to(pkg))
        for p in pkg.rglob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"内核反向依赖 SEKB：{offenders}"


class _FakeFactory:
    """最小 LLM 工厂替身（只为构造分析器，不实际调用）。"""

    async def ainvoke_with_stats(self, role: str, messages: list, **kw: object) -> object:
        raise AssertionError("本测试不应真正调用 LLM")


@pytest.mark.parametrize("name", ["market", "generator", "apply_plan", "llm_adapter"])
def test_sekb_modules_import_cleanly(name: str) -> None:
    """各接线模块可独立导入（防止循环导入）。"""
    __import__(f"app.agents.job.{name}")
