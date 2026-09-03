"""
职位分析生成模块（多步骤流水线）。

单个 JD 的流程：
    02 深度分析 → 03 知识点优先级 + 05 差距分析（并行）
        → 04 面试 Q&A + 06 简历建议 + 07 学习计划 + 08 项目迭代 + 09 求职策略（并行）

每一步独立降级：LLM 调用异常或 JSON 解析失败时记录日志并返回空结构，
不让整个分析崩溃（下游步骤用空结构继续执行）。

01 职位筛选针对「职位列表」输入，单个 JD 深度分析不适用，故跳过；
后续如需批量筛选，可新增 ``analyze_jobs(job_list)`` 复用 ``01_job_filter.md``。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

# 各步骤 → 提示词文件名
_STEP_PROMPT_FILES = {
    "job_analysis": "02_job_analysis.md",
    "knowledge_priority": "03_knowledge_priority.md",
    "interview_qa": "04_interview_qa.md",
    "gap_analysis": "05_gap_analysis.md",
    "resume_advice": "06_resume_advice.md",
    "learning_plan": "07_learning_plan.md",
    "project_iteration": "08_project_iteration.md",
    "job_strategy": "09_job_strategy.md",
}


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
    """基于 LLM 的职位分析流水线（多步骤，逐步降级）。"""

    def __init__(self, llm_factory: Any, prompt_dir: str | None = None) -> None:
        self._llm_factory = llm_factory
        self._prompts: dict[str, str] = {}
        base = Path(prompt_dir) if prompt_dir else _resolve_prompt_dir()
        if base:
            for step, filename in _STEP_PROMPT_FILES.items():
                p = base / filename
                if p.exists():
                    self._prompts[step] = p.read_text(encoding="utf-8")
                else:
                    logger.warning("职位分析提示词缺失，该步骤将跳过", step=step, path=str(p))
        if not self._prompts:
            logger.warning("未找到任何职位分析提示词文件", dir=str(base) if base else "None")

    # ---------- 编排 ----------

    async def analyze(
        self,
        jd_text: str,
        job_meta: dict | None = None,
        user_profile: str = "",
        role: str = "job_analysis",
        resume_summary: str = "",
        existing_projects: str = "",
    ) -> dict:
        """对单个职位 JD 执行全流程分析，返回聚合的各步结构化结果。"""
        jd_text = (jd_text or "").strip()
        if not jd_text:
            raise ValueError("jd_text 不能为空")
        meta_json = json.dumps(job_meta, ensure_ascii=False) if job_meta else "无"

        # 1) 深度分析：后续所有步骤的输入源
        job_analysis = await self._step_job_analysis(jd_text, meta_json, role)

        # 2) 并行：知识点优先级（依赖 02）+ 差距分析（依赖 02）
        priorities, gap = await asyncio.gather(
            self._step_knowledge_priority(job_analysis, user_profile, role),
            self._step_gap_analysis(job_analysis, user_profile, role),
        )

        # 3) 并行：面试 Q&A（依赖 03）+ 简历建议/学习计划/项目迭代（依赖 05）+ 求职策略（依赖 02）
        interview, resume, learning, project, strategy = await asyncio.gather(
            self._step_interview_qa(job_analysis, priorities, user_profile, role),
            self._step_resume_advice(job_analysis, gap, user_profile, role, resume_summary),
            self._step_learning_plan(gap, user_profile, role),
            self._step_project_iteration(gap, user_profile, role, existing_projects),
            self._step_job_strategy(job_analysis, user_profile, role),
        )

        return {
            "job_analysis": job_analysis,
            "knowledge_priority": priorities,
            "interview_qa": interview,
            "gap_analysis": gap,
            "resume_advice": resume,
            "learning_plan": learning,
            "project_iteration": project,
            "job_strategy": strategy,
        }

    # ---------- 各步骤 ----------

    async def _step_job_analysis(self, jd_text: str, meta_json: str, role: str) -> dict:
        return await self._call_step(
            "job_analysis", {"jd_text": jd_text, "job_meta": meta_json}, role
        )

    async def _step_knowledge_priority(
        self, job_analysis: dict, user_profile: str, role: str
    ) -> dict:
        return await self._call_step(
            "knowledge_priority",
            {"job_analyses": self._dumps([job_analysis]), "user_profile": user_profile},
            role,
        )

    async def _step_interview_qa(
        self, job_analysis: dict, priorities: dict, user_profile: str, role: str
    ) -> dict:
        return await self._call_step(
            "interview_qa",
            {
                "job_analyses": self._dumps([job_analysis]),
                "knowledge_priority": self._dumps(priorities),
                "user_profile": user_profile,
            },
            role,
        )

    async def _step_gap_analysis(
        self, job_analysis: dict, user_profile: str, role: str
    ) -> dict:
        return await self._call_step(
            "gap_analysis",
            {"user_profile": user_profile, "jd_requirements": self._dumps(job_analysis)},
            role,
        )

    async def _step_resume_advice(
        self,
        job_analysis: dict,
        gap: dict,
        user_profile: str,
        role: str,
        resume_summary: str,
    ) -> dict:
        return await self._call_step(
            "resume_advice",
            {
                "user_profile": user_profile,
                "jd_requirements": self._dumps(job_analysis),
                "gap_analysis": self._dumps(gap),
                "resume_summary": resume_summary or "无",
            },
            role,
        )

    async def _step_learning_plan(self, gap: dict, user_profile: str, role: str) -> dict:
        return await self._call_step(
            "learning_plan",
            {"gap_analysis": self._dumps(gap), "user_profile": user_profile},
            role,
        )

    async def _step_project_iteration(
        self, gap: dict, user_profile: str, role: str, existing_projects: str
    ) -> dict:
        return await self._call_step(
            "project_iteration",
            {
                "gap_analysis": self._dumps(gap),
                "user_profile": user_profile,
                "existing_projects": existing_projects or "无",
            },
            role,
        )

    async def _step_job_strategy(
        self, job_analysis: dict, user_profile: str, role: str
    ) -> dict:
        return await self._call_step(
            "job_strategy",
            {"job_analyses": self._dumps([job_analysis]), "user_profile": user_profile},
            role,
        )

    # ---------- 底层调用 ----------

    async def _call_step(self, step: str, replacements: dict[str, str], role: str) -> dict:
        """用占位符替换后的提示词调用 LLM，稳健解析 JSON；失败降级为空结构。"""
        prompt = self._prompts.get(step)
        if not prompt:
            logger.warning("步骤提示词缺失，跳过", step=step)
            return {}
        for key, value in replacements.items():
            prompt = prompt.replace("{{" + key + "}}", value)
        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content="请严格按输出格式返回 JSON，不要输出任何多余文字。"),
        ]
        try:
            llm = self._llm_factory.get(role)
            resp = await llm.ainvoke(messages)
            raw = resp.content if hasattr(resp, "content") else str(resp)
            return self._parse_json(raw)
        except Exception as e:  # noqa: BLE001
            logger.error("职位分析步骤失败，降级为空结构", step=step, error=str(e)[:200])
            return {}

    @staticmethod
    def _dumps(data: Any) -> str:
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _parse_json(raw: Any) -> dict:
        """从 LLM 响应中稳健提取 JSON 对象，失败返回空结构。"""
        text = raw if isinstance(raw, str) else str(raw)
        text = re.sub(r"```(?:json)?\s*", "", text).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            logger.error("职位分析结果非 JSON", raw=text[:200])
            return {}
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            logger.error("职位分析 JSON 解析失败", error=str(e), raw=text[:200])
            return {}
