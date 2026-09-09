"""用户画像偏好抽取服务（WP2/WP3 从 chat.py 画像子系统解耦）。

原「聊天偏好 → 用户画像」反馈闭环从聊天路由下沉至此：
- 方案 A：从回复末尾 ``<PREF>...</PREF>`` 标签提取（extract_and_update_profile）
- 方案 B：记录员 LLM 后台独立抽取（record_preferences_task / schedule_preference_extraction）
- 共享：偏好 patch 构建（_build_pref_patch）与画像 upsert（_upsert_profile），
  消除原先 _extract_and_update_profile 与 _apply_pref_to_profile 的重复逻辑

说明：方案 B 原由「应聘助手」skill 触发，本轮随 skill 删除而解耦；
后续待「求职意图」识别落地后按意图重新触发（见 11-EVOLUTION）。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.bootstrap import AppContext
from app.core.logging import get_logger

logger = get_logger(__name__)

# 求职偏好结构化标签（方案 A：应聘助手在回复末尾输出，系统提取后更新画像）
PREF_RE = re.compile(r"<PREF>(.*?)</PREF>", re.DOTALL)

# 记录员 LLM 提示词（方案 B）：不依赖主 LLM 输出标签，后台独立抽取求职偏好
PREF_EXTRACT_PROMPT = """你是一个「用户求职偏好记录员」。请根据下面的用户与求职顾问的对话，
提取用户**明确表达或强烈暗示**的求职偏好。只输出一个 JSON 对象，不要输出任何其他文字，不要用代码围栏。
字段可省略：
{{
  "target_roles": ["目标岗位方向，如 AI 应用开发工程师"],
  "target_cities": ["意向城市"],
  "min_salary_k": 30,
  "keywords": ["关注的技术栈/技能/关键词"],
  "career_goal": "简短职业目标（一句）",
  "skills": ["已有技能"]
}}
如果用户没表达任何新偏好，输出空对象 {{}}。

【对话记录】
{context}"""

# 后台偏好抽取任务引用（防止被 GC，完成后自动移除）
_background_extract_tasks: set[Any] = set()


def _build_pref_patch(data: dict[str, Any]) -> dict[str, Any]:
    """把抽取结果整理为画像 patch（job_preferences / skills / career_goal）。"""
    patch: dict[str, Any] = {}
    job: dict[str, Any] = {}
    for key in ("target_roles", "target_cities", "min_salary_k", "keywords"):
        if key in data:
            job[key] = data[key]
    if job:
        patch["job_preferences"] = job  # upsert_update 深合并，不覆盖未传字段
    if data.get("skills"):
        patch["skills"] = data["skills"]
    if data.get("career_goal"):
        patch["career_goal"] = data["career_goal"]
    return patch


async def _upsert_profile(user_id: str, patch: dict[str, Any]) -> None:
    """写入用户画像（按 user_id 隔离）。"""
    from app.storage.profile_storage import ProfileStorage

    await ProfileStorage("data/profile").upsert_update(user_id, patch)
    logger.info("已从聊天更新用户画像", user_id=user_id, fields=list(patch.keys()))


async def extract_and_update_profile(user_id: str, answer: str) -> str:
    """（方案 A）提取回复末尾 <PREF> 求职偏好并写入画像，返回去掉标签的干净回复。"""
    m = PREF_RE.search(answer)
    if not m:
        return answer
    try:
        data = json.loads(m.group(1).strip())
        patch = _build_pref_patch(data)
        if patch:
            await _upsert_profile(user_id, patch)
    except Exception as e:
        logger.warning("解析/更新求职偏好失败", user_id=user_id, error=str(e))
    return PREF_RE.sub("", answer).strip()


def extract_json_from_llm(text: str) -> dict[str, Any] | None:
    """从 LLM 输出中稳健地提取 JSON 对象（兼容 ```json 围栏与前后说明文字）。"""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
    except Exception:
        pass
    try:
        return json.loads(text.strip())
    except Exception:
        return None


async def record_preferences_task(
    ctx: AppContext, conv_id: str, user_id: str, user_input: str, answer: str
) -> None:
    """（方案 B）记录员 LLM 后台分析对话，抽取求职偏好写入画像。"""
    try:
        # 读取最近对话作为上下文（来自短期记忆）
        context_lines: list[str] = []
        try:
            history = await ctx.memory.get_messages(user_id, conv_id)
            for msg in (history or [])[-10:]:
                role = "用户" if getattr(msg, "type", "") == "human" else "助手"
                content = getattr(msg, "content", "") or ""
                if content:
                    context_lines.append(f"{role}: {str(content)[:300]}")
        except Exception:
            pass
        if user_input:
            context_lines.append(f"用户: {user_input[:300]}")
        if answer:
            context_lines.append(f"助手: {answer[:300]}")
        context_text = "\n".join(context_lines)

        messages = [
            SystemMessage(content="你是求职偏好记录员，只输出结构化 JSON。"),
            HumanMessage(content=PREF_EXTRACT_PROMPT.format(context=context_text)),
        ]
        response = await ctx.llm_factory.ainvoke_with_stats("chat_simple", messages)
        text = response.content if hasattr(response, "content") else str(response)
        data = extract_json_from_llm(text)
        if data is None:
            logger.info("记录员 LLM 未输出有效 JSON，跳过画像更新")
            return
        patch = _build_pref_patch(data)
        if patch:
            await _upsert_profile(user_id, patch)
            logger.info("记录员已提取偏好写入画像")
    except Exception as e:
        logger.warning("记录员抽取偏好失败", user_id=user_id, error=str(e), exc_info=True)


def schedule_preference_extraction(
    ctx: AppContext, conv_id: str, user_id: str, user_input: str, answer: str
) -> None:
    """调度后台偏好抽取任务（方案 B，不阻塞主回复）。"""
    try:
        task = asyncio.create_task(
            record_preferences_task(ctx, conv_id, user_id, user_input, answer)
        )
        _background_extract_tasks.add(task)
        task.add_done_callback(_background_extract_tasks.discard)
    except Exception as e:
        logger.warning("调度偏好抽取任务失败", user_id=user_id, error=str(e))
