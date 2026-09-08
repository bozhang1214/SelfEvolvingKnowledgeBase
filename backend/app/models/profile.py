"""
用户画像模型（多功能联动地基）。

描述用户长期的偏好、技能、职业目标、资讯关注，用于：
- 「应聘助手」skill 结合求职偏好做深入沟通（虚拟面试、岗位深挖、知识补充）
- 「科技资讯助手」skill 结合关注大类做定向问答
- 招聘分析模块据此优化职位采集关键词/城市/薪资/公司类型
- 反馈闭环：聊天中表达的偏好回流，持续完善画像

按 user_id 隔离（``data/profile/{user_id}.json``）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class JobPreferences(BaseModel):
    """求职偏好（提供给招聘分析与「应聘助手」）。"""

    target_roles: list[str] = Field(default_factory=list, description="目标岗位方向")
    target_cities: list[str] = Field(default_factory=list, description="意向城市")
    min_salary_k: int | None = Field(None, description="最低月薪（千元）")
    company_types: list[str] = Field(default_factory=list, description="意向公司类型")
    keywords: list[str] = Field(default_factory=list, description="关注技术栈/关键词")


class UserProfile(BaseModel):
    """用户画像。"""

    user_id: str = Field(..., description="所属用户")
    bio: str = Field("", description="自我介绍")
    skills: list[str] = Field(default_factory=list, description="已有技能")
    career_goal: str = Field("", description="职业目标")
    job_preferences: JobPreferences = Field(default_factory=JobPreferences)
    news_interests: list[str] = Field(default_factory=list, description="关注的资讯大类")
    updated_at: datetime = Field(default_factory=_now_utc)


class ProfileUpdate(BaseModel):
    """画像更新请求（字段全可选，传入的字段合并/覆盖）。"""

    bio: str | None = None
    skills: list[str] | None = None
    career_goal: str | None = None
    job_preferences: JobPreferences | None = None
    news_interests: list[str] | None = None
