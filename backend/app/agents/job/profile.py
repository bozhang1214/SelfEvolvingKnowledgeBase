"""
用户画像加载模块。

优先从 ``prompt/job/README.md`` 的 yaml 代码块读取用户画像（便于单一维护源）；
文件缺失或解析失败时，回退到内置画像常量，保证职位分析不因画像缺失而崩溃。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# 内置兜底画像：与 prompt/job/README.md 的 yaml 段保持一致
_FALLBACK_PROFILE = """\
name: 张博
age: 41
years_of_experience: 15
current_role: 技术经理（爱奇艺）
target_role: Agent 开发（应用侧）/ 大模型应用工程师
city: 北京
min_salary: "50K×14"
company_priority: [大厂, AI公司, 国央企, 创业公司]
background:
  - 爱奇艺 10 年：播放器/Android 客户端、多屏互动(投屏)、技术管理、数据质量体系
  - 早期 5 年：Android ROM 开发（智能电视）
  - 技术管理经验丰富：带 20+ 团队、跨部门协调、项目全流程
  - 近 1 年：主导团队 AI 工具落地（Cursor），团队 AI 使用率 100%
skill_stack:
  strong: [技术管理, 项目管理, 客户端/播放器架构, 数据指标与质量体系, Android/Java]
  medium: [AI 工具应用与推广, 需求分析, 跨团队协调]
  weak:
    - LangGraph/LangChain 深度开发
    - RAG/向量库工程化
    - LLM 应用后端
    - Python 深度开发
    - MCP/Agent 框架开发
gap_summary:
  - AI/Agent 经验主要在"应用/推广"层，深度开发经验偏弱
  - 需补齐：Agent 框架工程化、RAG 工程、LLM 应用后端（Python/FastAPI）
"""


def _resolve_profile_path() -> Path | None:
    """定位用户画像 README：优先本地仓库根，其次容器内 /app。"""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[4] / "prompt",  # 本地：backend/app/agents/job/ → 仓库根
        here.parents[3] / "prompt",  # 容器：/app/app/agents/job/ → /app
        Path.cwd() / "prompt",
    ]
    for d in candidates:
        p = d / "job" / "README.md"
        if p.exists():
            return p
    return None


def load_user_profile() -> str:
    """加载用户画像文本（优先 README 的 yaml 段，缺失则用内置画像）。"""
    path = _resolve_profile_path()
    if path:
        try:
            text = path.read_text(encoding="utf-8")
            m = re.search(r"```yaml\s*\n(.*?)\n```", text, re.DOTALL)
            if m:
                profile = m.group(1).strip()
                if profile:
                    logger.info("已加载用户画像", path=str(path))
                    return profile
        except OSError as e:
            logger.warning("读取用户画像失败，使用内置画像", error=str(e))
    logger.warning("未找到用户画像文件，使用内置画像")
    return _FALLBACK_PROFILE
