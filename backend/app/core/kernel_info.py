"""内核（jobcopilot）版本信息。

提供「运行时可见性」：任何一个运行中的实例都能立刻回答
**「我现在跑的是哪个内核版本、提示词齐不齐」**。

信息来源：
- ``jobcopilot.__version__`` —— 包版本（随包发布）
- ``JOBCOPILOT_COMMIT`` 环境变量 —— 内核 commit，构建镜像时由 deploy.sh 烧入
  （本地直接跑时没有这个变量，显示 unknown）

关于 commit 的用法：`git submodule status jobcopilot` 拿到 SEKB 钉住的 commit，
与 ``/health`` 里的 ``kernel.commit`` 比对，就能确认线上跑的是不是钉住的那份。
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 部署时由 deploy.sh 通过 docker build arg 注入
ENV_KERNEL_COMMIT = "JOBCOPILOT_COMMIT"


@lru_cache(maxsize=1)
def kernel_info() -> dict[str, Any]:
    """收集内核版本信息（结果缓存，进程生命周期内不变）。

    Returns:
        ``{"name", "version", "commit", "prompts", "steps", "isolated", "healthy"}``；
        内核整体不可导入时返回 ``{"healthy": False, "error": ...}``。
    """
    commit = os.environ.get(ENV_KERNEL_COMMIT, "unknown")
    try:
        import jobcopilot
        from jobcopilot.core.prompts import PromptResolver, base_dir

        bundled = sorted(p.name for p in base_dir().glob("*.md"))

        # 报告**实际生效**的提示词来源：SEKB 的 prompt/job 是 bind mount，
        # 优先级高于包内 base。延迟 import 避免 core → agents 的导入环。
        local_dir = None
        try:
            from app.agents.job.generator import _resolve_prompt_dir

            local_dir = _resolve_prompt_dir()
        except Exception:  # noqa: BLE001
            pass
        resolver = PromptResolver(local_dir=local_dir)

        return {
            "name": "jobcopilot",
            "version": getattr(jobcopilot, "__version__", "unknown"),
            "commit": commit,
            "prompts": len(bundled),
            "prompt_source": resolver.source_of("批量职位分析.md"),
            "prompt_dir": str(local_dir) if local_dir else "",
            "healthy": len(bundled) > 0,
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"内核信息采集失败 error={str(e)[:200]}")
        return {
            "name": "jobcopilot",
            "version": "unknown",
            "commit": commit,
            "healthy": False,
            "error": str(e)[:200],
        }


def log_kernel_info() -> None:
    """在启动日志里打一行内核版本，便于事后从日志追溯（Loki 可检索）。"""
    info = kernel_info()
    logger.info(
        f"内核查就绪 name={info.get('name')} version={info.get('version')} "
        f"commit={str(info.get('commit'))[:7]} prompts={info.get('prompts', 0)} "
        f"healthy={info.get('healthy')}"
    )
