"""招聘分析生成模块（SEKB 侧接线，P4 起默认走 MCP）。

**P4：分析链路改为** ``MCPClient → stdio → jobcopilot-mcp``。SEKB 不再直接 import
内核的分析实现，成为纯粹的**客户端**——内核可独立发版与部署。

两种传输（``job.transport``）：

- ``mcp``（默认）：走 ``jobcopilot-mcp`` 子进程，见 :mod:`app.agents.job.mcp_client`；
- ``direct``：进程内直接调用 jobcopilot 包（**紧急回滚开关**，保留旧路径）。

无论哪种方式，``_resolve_prompt_dir()`` 定位的 SEKB ``prompt/job`` 都会传给内核，
因此**现网提示词热改（bind mount）继续生效**，行为与直连时期一致。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


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


def _kernel_llm_env(config: Any) -> dict[str, str]:
    """把 SEKB 的模型配置翻译成内核子进程的环境变量（BYOK）。

    内核是独立进程，它自己调 LLM —— 所以**必须**把 Key 传过去，否则内核会
    以「LLM 不可用」失败（而 SEKB 的 LLMFactory 用不上）。
    """
    env: dict[str, str] = {}
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        env["JOBCOPILOT_LLM_PROVIDER"] = "deepseek"
        env["JOBCOPILOT_LLM_API_KEY"] = key
    else:
        logger.error("未找到 DEEPSEEK_API_KEY，内核子进程将无法调用 LLM")
    return env


class JobAnalysisGenerator:
    """职位分析流水线（SEKB 侧接线）。

    签名与历史版本保持一致，调用方（``JobAgent`` 等）无需改动。
    """

    def __init__(self, llm_factory: Any, prompt_dir: str | None = None, config: Any = None) -> None:
        self._llm_factory = llm_factory
        self._config = config
        resolved = Path(prompt_dir) if prompt_dir else _resolve_prompt_dir()
        self._prompt_dir = resolved
        self._transport = getattr(config, "transport", "mcp") if config else "direct"
        self._mcp: Any = None
        self._inner: Any = None

        if self._transport == "mcp":
            # 用**共享**连接：单职位与批量分析共用同一条内核子进程
            from app.agents.job.mcp_client import get_shared_kernel

            self._mcp = get_shared_kernel(config)
            logger.info(
                "职位分析走 MCP 内核", command=getattr(config, "mcp_command", "jobcopilot-mcp")
            )
        else:
            self._init_direct(llm_factory, resolved)

    def _init_direct(self, llm_factory: Any, resolved: Path | None) -> None:
        """旧路径：进程内直接调用 jobcopilot（回滚用）。"""
        from jobcopilot import SingleJobAnalyzer

        from app.agents.job.llm_adapter import SekbLLMAdapter

        self._inner = SingleJobAnalyzer(
            SekbLLMAdapter(llm_factory),
            prompt_dir=str(resolved) if resolved else None,
        )
        logger.warning("职位分析走 direct 直连（非默认路径，仅用于回滚）")

    @property
    def analyzer(self) -> Any:
        """底层直连分析器（仅 direct 模式有；便于测试与观测）。"""
        return self._inner

    @property
    def transport(self) -> str:
        """当前传输方式。"""
        return self._transport

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
        if self._transport != "mcp":
            return await self._inner.analyze(
                jd_text=jd_text,
                job_meta=job_meta,
                user_profile=user_profile,
                role=role,
                resume_summary=resume_summary,
                existing_projects=existing_projects,
            )

        payload = await self._mcp.call(
            "analyze_job",
            {
                "jd_text": jd_text,
                "user_profile": user_profile,
                **({"job_meta": job_meta} if job_meta else {}),
            },
        )
        # 内核自己调 LLM，SEKB 的 LLMFactory 看不到——把用量落到结构化日志，
        # 否则招聘分析的费用会从成本统计里消失。
        usage = payload.pop("usage", None)
        if isinstance(usage, dict) and usage.get("calls"):
            logger.info(
                "内核分析用量",
                scene="单职位分析",
                calls=usage.get("calls"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            )
        payload.pop("prompt_meta", None)  # 内核元数据不进 SEKB 的对外结果（历史契约不变）
        return payload

    async def close(self) -> None:
        """释放内核连接（共享连接由应用统一关闭，这里只解除本对象引用）。"""
        self._mcp = None
