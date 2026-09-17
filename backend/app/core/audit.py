"""
审计日志模块（Phase 4 P0-9 安全加固）。

记录所有安全相关操作的审计事件：
- 用户认证（登录/注册/登出）
- 资源访问（知识库 CRUD、对话管理）
- 权限变更
- 敏感操作

审计日志独立于应用日志，写入单独的 JSONL 文件，便于合规审查。

使用方式：
    from app.core.audit import audit_log, AuditAction
    audit_log(AuditAction.LOGIN, user_id="default", success=True, ip="1.2.3.4")
"""

from __future__ import annotations

import json
import os
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class AuditAction(str, Enum):
    """审计动作类型。"""

    # 认证相关
    REGISTER = "register"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    TOKEN_REFRESH = "token_refresh"
    #: 设备接入/吊销（端云协同 S1）：设备凭证的生命周期必须留审计痕迹
    DEVICE_ENROLL = "device_enroll"
    DEVICE_REVOKE = "device_revoke"

    # 资源访问
    KNOWLEDGE_UPLOAD = "knowledge_upload"
    KNOWLEDGE_DELETE = "knowledge_delete"
    KNOWLEDGE_QUERY = "knowledge_query"

    # 对话
    CONVERSATION_CREATE = "conversation_create"
    CONVERSATION_DELETE = "conversation_delete"

    # 用户管理
    USER_UPDATE = "user_update"
    USER_DELETE = "user_delete"

    # 系统
    CONFIG_CHANGE = "config_change"
    DATA_EXPORT = "data_export"
    BACKUP_RESTORE = "backup_restore"


# 审计日志文件路径
_DEFAULT_AUDIT_DIR = os.getenv("SEKB_AUDIT_DIR", "data/audit")
_AUDIT_FILE = "audit.log"

# 线程安全写入锁
_write_lock = threading.Lock()


def _get_audit_path() -> Path:
    """获取审计日志文件路径，确保目录存在。"""
    audit_dir = Path(_DEFAULT_AUDIT_DIR)
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir / _AUDIT_FILE


def audit_log(
    action: AuditAction,
    user_id: str = "anonymous",
    success: bool = True,
    ip: str | None = None,
    resource: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """
    记录一条审计日志。

    日志以 JSONL 格式写入文件，每行一条 JSON 记录。

    Args:
        action: 审计动作类型
        user_id: 操作用户 ID
        success: 操作是否成功
        ip: 客户端 IP 地址
        resource: 受影响的资源标识（如 knowledge_id、conversation_id）
        detail: 额外详情（如修改的字段、查询参数）

    Example:
        >>> audit_log(AuditAction.LOGIN, user_id="default", success=True, ip="1.2.3.4")
    """
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.gmtime()),
        "action": action.value,
        "user_id": user_id,
        "success": success,
        "ip": ip,
        "resource": resource,
        "detail": detail or {},
    }

    try:
        with _write_lock:
            path = _get_audit_path()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # 审计日志失败不影响主流程，但需记录到应用日志
        logger.warning("审计日志写入失败", action=action.value, error=str(e))

    # 同时输出到应用日志（便于 Loki 采集）
    logger.info(
        "audit",
        action=action.value,
        user_id=user_id,
        success=success,
        ip=ip,
        resource=resource,
    )


def get_client_ip(request) -> str:
    """
    从请求对象中提取客户端真实 IP。

    优先使用 X-Forwarded-For（经 Nginx 反代），回退到 direct client IP。

    Args:
        request: FastAPI Request 对象

    Returns:
        客户端 IP 地址字符串
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # X-Forwarded-For 可能包含多个 IP，取第一个（最原始的客户端）
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
