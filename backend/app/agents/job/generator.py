"""面试分析生成模块（SEKB 侧薄封装）。

**P0 起分析逻辑已抽到 ``jobcopilot`` 包**，本模块只保留两件事：

1. ``_resolve_prompt_dir``：定位 SEKB 自己的 ``prompt/job`` 目录
   （现网是 bind mount，运营可直接改提示词，不用重建镜像）；
2. ``JobAnalysisGenerator``：把 ``LLMFactory`` 适配成内核的 ``LLMPort``，
   保持 SEKB 原有调用签名不变。

流水线编排、提示词占位符替换、JSON 稳健解析、逐步降级全部在内核里实现。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jobcopilot import SingleJobAnalyzer

from app.agents.job.llm_adapter import SekbLLMAdapter


def _resolve_prompt_dir() -> Path | None:
    """定位提示词目录（job 子目录）：优先本地仓库根，其次容器内 /app。"""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[4] / "prompt",  # 本地：backend/app/agents/job/ → 仓库根
        here.parents[3] / "prompt",  # 容器：/app/app/agents/job/ → /app
        Path.cwd() / "prompt",
    ]
    for d in candidates:
        p = d / "job"
        if p.exists():
            return p
    return None


class JobAnalysisGenerator:
    """基于 LLM 的职位分析流水线（多步骤，逐步降级）。

    保留本类是为了不破坏 SEKB 既有调用方（``JobAgent`` 等）；
    实际实现委托给 :class:`jobcopilot.SingleJobAnalyzer`。
    """

    def __init__(self, llm_factory: Any, prompt_dir: str | None = None) -> None:
        resolved = Path(prompt_dir) if prompt_dir else _resolve_prompt_dir()
        self._inner = SingleJobAnalyzer(
            SekbLLMAdapter(llm_factory),
            prompt_dir=str(resolved) if resolved else None,
        )

    @property
    def analyzer(self) -> SingleJobAnalyzer:
        """底层内核分析器（便于测试/观测已加载的步骤）。"""
        return self._inner

    async def analyze(
        self,
        jd_text: str,
        job_meta: dict | None = None,
        user_profile: str = "",
        role: str = "job_analysis",
        resume_summary: str = "",
        existing_projects: str = "",
    ) -> dict:
        """对单个职位 JD 执行全流程分析（签名与抽取前完全一致）。"""
        return await self._inner.analyze(
            jd_text=jd_text,
            job_meta=job_meta,
            user_profile=user_profile,
            role=role,
            resume_summary=resume_summary,
            existing_projects=existing_projects,
        )
