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

# 内置兜底画像：与 prompt/job/README.md 的 yaml 段保持一致（2026-09-23 修订）
_FALLBACK_PROFILE = """\
name: 张博
age: 41
years_of_experience: 15
current_role: 独立开发者（AI Agent 方向，2026.04 起全职）
prior_role: 技术经理（爱奇艺，2015.10-2026.03）
target_role: AI Agent 解决方案与交付（FDE）
city: 北京
min_salary: "面议（不低于上一份）"
company_priority: [大厂, AI公司, AI创业公司, 国央企]
background:
  - 爱奇艺 10 年（技术经理，2015.10-2026.03）：播放器业务线架构设计与团队管理
  - ToB 方案与客户交付（爱奇艺任期内，约 3 年）：
    · 运营商 ToB 平台（2019-2021，项目总负责人）：需求挖掘 → 方案设计 → 客户对接 → 交付运维 → 方案沉淀全流程，
      累计落地 50+ 运营商客户、累积营收以亿计，作为唯一对外接口人对接 20+ 内外团队
    · 第二院线新业务（2018，项目组负责人）：KDM 加密播放从 0 到 1，覆盖方案选型、客户端架构、
      厂商适配、前后端联调、商用上线全流程（行业首创首页沉浸式播放）
  - 从 0 搭建核心指标体系与数据监控框架（指标 30+/维度 10+/离线报表 20+/实时报警 10+）
  - 早期 5 年：Android ROM 开发与智能电视移植（松下/飞利浦/熊猫）
  - 团队 AI 化：主导团队 AI 辅助研发全流程落地，AI 使用率 100%、代码提交率 90%+
  - 近 1 年独立开发：从 0 设计并实现 SEKB 自进化知识库 Agent
    （LangGraph 五 Agent 编排 + 混合检索 RAG + MCP + 内核化），已开源 + 线上 Demo + 技术博客；
    并实现 Android 端侧 Agent 宿主（端云协同 + 端侧工具权限 + 端侧 RAG，182 单测）
skill_stack:
  strong:
    - 端侧系统（Android ROM/播放器架构/多屏投屏/插件化/性能与稳定性治理）
    - ToB 方案设计与客户交付（50+ 客户、唯一对外接口人、方案与规范沉淀）
    - 技术管理与跨团队协调（带团队、协调 20+ 内外团队）
    - 数据指标与质量体系
  medium:
    - Agent 工程化（LangGraph 多智能体编排、反思重规划、三层记忆、评测体系）
    - RAG 工程（混合检索 BM25+向量+RRF、Rerank、RAGAS 式评测）
    - LLM 应用后端（FastAPI + asyncio + SSE 流式 + Redis）
    - MCP 协议与工具生态（stdio/SSE、MCP Server 化内核）
    - 工程质量（ruff/mypy 回归门禁 + 909 自动化测试 + CI）
    - 安全加固（JWT 吊销、内部服务鉴权、审计日志、Prompt 注入防护）
    - 端云协同与模型路由（端侧宿主 + 云端内核 + MCP 契约）
  weak:
    - 底层推理优化（CUDA / vLLM / TensorRT / 算子 / 端侧 NPU）
    - 模型训练与微调（SFT / LoRA / RLHF 实操）
    - 垂直行业 know-how（金融 / 医疗 / 汽车）
gap_summary:
  - 复合型：15 年端侧系统（爱奇艺 10 年 + 早期 ROM 5 年）+ 3 年 ToB 客户交付 + 1 年 Agent 工程化
    ——不是"需补 Agent 基本功"的阶段（"3 年 ToB 交付"发生在爱奇艺任期内，不是额外 3 年）
  - 真正短板：底层推理/训练（非应用侧目标，可放弃）、垂直行业 know-how（优先政企/运营商/内容相邻赛道）
  - 求职定位：主线为企业级 AI Agent 解决方案与交付（FDE）；画像只陈述能力事实，
    多方向由「搜索关键词 + 按方向定制的简历版本」承载，不在此并列
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


def _load_base_profile() -> str:
    """加载基础画像文本（README 的 yaml 段，缺失则用内置画像）。"""
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


def _profile_to_supplement_text(profile) -> str:
    """把按用户的 UserProfile 转成提示词友好的补充文本（空则返回空串）。"""
    lines: list[str] = []
    if getattr(profile, "bio", ""):
        lines.append(f"自我介绍: {profile.bio}")
    if getattr(profile, "career_goal", ""):
        lines.append(f"职业目标: {profile.career_goal}")
    if getattr(profile, "skills", None):
        lines.append(f"技能: {', '.join(profile.skills)}")
    jp = getattr(profile, "job_preferences", None)
    if jp:
        if jp.target_roles:
            lines.append(f"目标岗位: {', '.join(jp.target_roles)}")
        if jp.target_cities:
            lines.append(f"意向城市: {', '.join(jp.target_cities)}")
        if jp.min_salary_k:
            lines.append(f"最低薪资: {jp.min_salary_k}K×14")
        if jp.keywords:
            lines.append(f"关注方向: {', '.join(jp.keywords)}")
    return "\n".join(lines)


def load_user_profile(user_id: str | None = None) -> str:
    """
    加载用户画像文本：基础画像（README/内置） + 按用户实时画像补充。

    实现 D14「画像反向影响职位分析」：聊天中积累的求职偏好/技能/职业目标
    会作为补充注入职位分析提示词，让分析与用户的真实诉求更贴合。
    """
    base = _load_base_profile()
    if not user_id:
        return base
    try:
        from app.storage.profile_storage import ProfileStorage

        profile = ProfileStorage("data/profile").get_sync(user_id)
        if profile:
            supplement = _profile_to_supplement_text(profile)
            if supplement:
                return base + "\n\n【用户实时更新偏好】\n" + supplement
    except Exception as e:
        logger.warning("读取用户实时画像补充失败", error=str(e))
    return base
