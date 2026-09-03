"""
招聘分析（职位分析）Agent 服务编排。

对外暴露 ``analyze_job(jd_text, job_meta=None)``：把用户粘贴的职位 JD + 用户画像
注入，按多步骤流水线逐个调用 LLM，聚合返回结构化 JSON 结果。
"""
from __future__ import annotations

from typing import Any

from app.agents.job.generator import JobAnalysisGenerator
from app.agents.job.profile import load_user_profile
from app.core.logging import get_logger

logger = get_logger(__name__)


class JobAgent:
    """招聘分析 Agent。"""

    def __init__(self, config: Any, llm_factory: Any) -> None:
        self._config = config
        self._generator = JobAnalysisGenerator(llm_factory)
        self._user_profile = load_user_profile()
        logger.info("招聘分析 Agent 已初始化", llm_role=config.llm_role)

    async def analyze_job(self, jd_text: str, job_meta: dict | None = None) -> dict:
        """对单个职位 JD 做全流程分析，返回聚合的结构化结果。"""
        return await self._generator.analyze(
            jd_text=jd_text,
            job_meta=job_meta,
            user_profile=self._user_profile,
            role=self._config.llm_role,
        )
