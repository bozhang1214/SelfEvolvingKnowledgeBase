"""招聘分析 Agent 包（Phase 2）。

**P0 起内核已抽到独立的 ``jobcopilot`` 包**，本包只保留 SEKB 特有部分：
爬虫采集（collector / sources / fetcher）、缓存（job_cache / analysis_cache）、
历史存档（archive）、用户画像与存储接线（profile）、以及 LLM 适配层
（llm_adapter）。
"""
from jobcopilot.core.logging import set_logger_factory

from app.agents.job.generator import JobAnalysisGenerator
from app.agents.job.profile import load_user_profile
from app.agents.job.service import JobAgent
from app.core.logging import get_logger

# 把内核日志接进 SEKB 的结构化管道（JSON 格式 + 敏感字段脱敏 + trace_id）。
# 内核默认用标准库 logging；不接管的话，包含 LLM 原文片段的日志会绕过脱敏处理器。
set_logger_factory(get_logger)

__all__ = [
    "JobAgent",
    "JobAnalysisGenerator",
    "load_user_profile",
]
