"""
用户模型（Phase 3）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class User(BaseModel):
    """用户模型"""
    user_id: str = Field(default_factory=lambda: f"user_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}")
    email: str
    password_hash: str
    name: str = ""
    avatar_url: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True
    settings: dict[str, Any] = Field(default_factory=lambda: {
        "model": "deepseek-chat",
        "temperature": 0.7,
        "max_tokens": 4096,
    })


class UserPublic(BaseModel):
    """用户公开信息（不含密码）"""
    user_id: str
    email: str
    name: str
    avatar_url: str
    created_at: datetime
    is_active: bool
    settings: dict[str, Any]
    # 访问级别：full（完整功能）| preview（预览：仅功能说明+资讯只读）
    access_level: str = "full"


class RegisterRequest(BaseModel):
    """注册请求"""
    email: str = Field(..., pattern=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
    password: str = Field(..., min_length=8, max_length=128)
    name: str = Field(default="", max_length=50)


class LoginRequest(BaseModel):
    """登录请求"""
    email: str
    password: str


class LoginResponse(BaseModel):
    """登录响应"""
    user: UserPublic
    token: str