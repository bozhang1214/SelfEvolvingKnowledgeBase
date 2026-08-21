"""
ISSUE-003 + ISSUE-005 回归测试

覆盖登录/注册失败原因细分与 Prometheus 指标记录：

- TC-004-U：登录失败 - 用户不存在 → record_login_attempt("user_not_found")
- TC-005-U：登录失败 - 密码错误 → record_login_attempt("password_mismatch")
- TC-004 补充：登录成功 → record_login_attempt("success")
- TC-004 补充：注册失败 - 邮箱已存在 → record_register_attempt("email_exists")
- TC-004 补充：注册成功 → record_register_attempt("success")

设计要点：
- 不启动真实 FastAPI 服务，直接 await 路由函数，用 Mock 替代 _get_user_storage、
  verify_password、audit_log、create_jwt 等所有外部依赖
- Prometheus Counter 是全局单例，断言用 delta（after - before == 1）
- 对外响应始终为"邮箱或密码错误"，防用户枚举攻击
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

import pytest
from fastapi import HTTPException

# 指标读取工具
from prometheus_client import REGISTRY


def _get_counter_value(name: str, **labels) -> float:
    """读取 Prometheus Counter 当前值（按标签筛选）。

    prometheus_client 0.26 的 Counter 用 `_metrics` dict 存储按标签组合的
    子 Counter 实例，key 是按 `_labelnames` 顺序排列的 label values tuple，
    value 是子 Counter 对象，其 `_value.get()` 返回当前计数值。
    """
    metric = REGISTRY._names_to_collectors.get(name)
    if metric is None:
        return 0.0
    label_names = getattr(metric, "_labelnames", []) or []
    key = tuple(str(labels.get(str(ln), "")) for ln in label_names)
    internal = metric._metrics.get(key)
    if internal is None:
        return 0.0
    return internal._value.get()


def _delta(before: float, after: float) -> float:
    """计算 Counter delta。"""
    return after - before


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_user_storage():
    """Mock UserStorage 实例。"""
    from datetime import datetime, timezone
    from app.models.user import UserPublic

    # 构造真实 UserPublic 实例，避免 LoginResponse 类型校验失败
    public_user = UserPublic(
        user_id="u1",
        email="x@y.com",
        name="Test",
        avatar_url="",
        created_at=datetime.now(timezone.utc),
        is_active=True,
        settings={"model": "test"},
    )

    storage = MagicMock()
    storage.find_by_email = MagicMock(return_value=None)
    storage.create = MagicMock(return_value=None)
    storage.to_public = MagicMock(return_value=public_user)
    return storage


@pytest.fixture
def fake_request():
    """构造一个 Mock Request 对象。"""
    req = MagicMock()
    req.headers = {"user-agent": "test-agent"}
    req.url = MagicMock()
    req.url.path = "/api/v1/auth/login"
    return req


@pytest.fixture
def patch_user_storage(mock_user_storage):
    """Patch _get_user_storage 返回 mock 实例。"""
    with patch("app.api.routes.auth._get_user_storage", return_value=mock_user_storage):
        yield


@pytest.fixture
def patch_audit_log():
    """Patch audit_log 避免写真实文件。"""
    with patch("app.api.routes.auth.audit_log") as m:
        yield m


@pytest.fixture
def patch_create_jwt():
    """Patch create_jwt 返回固定 token。"""
    with patch("app.api.routes.auth.create_jwt", return_value="fake-jwt-token"):
        yield


# ============================================================
# TC-004-U：登录失败 - 用户不存在
# ============================================================

@pytest.mark.asyncio
async def test_login_user_not_found_records_metric(
    mock_user_storage, fake_request, patch_user_storage, patch_audit_log
):
    """登录时用户不存在，应记录 login_attempts_total{result="user_not_found"} +1。"""
    from app.api.routes.auth import login
    from app.models.user import LoginRequest

    mock_user_storage.find_by_email = MagicMock(return_value=None)

    body = LoginRequest(email="notexist@example.com", password="anypassword")

    before = _get_counter_value("sekb_login_attempts_total", result="user_not_found")

    # 期望抛出 401
    with pytest.raises(HTTPException) as exc_info:
        await login(body, fake_request)

    after = _get_counter_value("sekb_login_attempts_total", result="user_not_found")

    # 断言：响应 401 + 对外不暴露具体原因
    assert exc_info.value.status_code == 401
    assert "邮箱或密码错误" in exc_info.value.detail

    # 断言：指标 +1
    assert _delta(before, after) == 1

    # 断言：审计日志被调用，detail 中包含 reason=user_not_found
    patch_audit_log.assert_called_once()
    call_kwargs = patch_audit_log.call_args
    assert call_kwargs.kwargs.get("detail", {}).get("reason") == "user_not_found"


# ============================================================
# TC-005-U：登录失败 - 密码错误
# ============================================================

@pytest.mark.asyncio
async def test_login_password_mismatch_records_metric(
    mock_user_storage, fake_request, patch_user_storage, patch_audit_log
):
    """登录时密码错误，应记录 login_attempts_total{result="password_mismatch"} +1。"""
    from app.api.routes.auth import login
    from app.models.user import LoginRequest, User

    # 构造一个已注册用户
    fake_user = User(
        email="exist@example.com",
        password_hash="hashed-password",
        name="Test",
    )
    mock_user_storage.find_by_email = MagicMock(return_value=fake_user)

    body = LoginRequest(email="exist@example.com", password="wrongpassword")

    before = _get_counter_value("sekb_login_attempts_total", result="password_mismatch")

    # Mock verify_password 返回 False
    with patch("app.api.routes.auth.verify_password", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            await login(body, fake_request)

    after = _get_counter_value("sekb_login_attempts_total", result="password_mismatch")

    # 断言：响应 401 + 对外不暴露具体原因
    assert exc_info.value.status_code == 401
    assert "邮箱或密码错误" in exc_info.value.detail

    # 断言：指标 +1
    assert _delta(before, after) == 1

    # 断言：审计日志包含 reason=password_mismatch
    patch_audit_log.assert_called_once()
    call_kwargs = patch_audit_log.call_args
    assert call_kwargs.kwargs.get("detail", {}).get("reason") == "password_mismatch"


# ============================================================
# TC-004 补充：登录成功
# ============================================================

@pytest.mark.asyncio
async def test_login_success_records_metric(
    mock_user_storage, fake_request, patch_user_storage, patch_audit_log, patch_create_jwt
):
    """登录成功应记录 login_attempts_total{result="success"} +1。"""
    from app.api.routes.auth import login
    from app.models.user import LoginRequest, User

    fake_user = User(
        email="ok@example.com",
        password_hash="hashed-password",
        name="OK",
    )
    mock_user_storage.find_by_email = MagicMock(return_value=fake_user)

    body = LoginRequest(email="ok@example.com", password="rightpassword")

    before = _get_counter_value("sekb_login_attempts_total", result="success")

    # Mock verify_password 返回 True
    with patch("app.api.routes.auth.verify_password", return_value=True):
        result = await login(body, fake_request)

    after = _get_counter_value("sekb_login_attempts_total", result="success")

    # 断言：返回 LoginResponse
    assert result.token == "fake-jwt-token"

    # 断言：指标 +1
    assert _delta(before, after) == 1


# ============================================================
# TC-004 补充：注册失败 - 邮箱已存在
# ============================================================

@pytest.mark.asyncio
async def test_register_email_exists_records_metric(
    mock_user_storage, fake_request, patch_user_storage, patch_audit_log
):
    """注册邮箱已存在应记录 register_attempts_total{result="email_exists"} +1。"""
    from app.api.routes.auth import register
    from app.models.user import RegisterRequest, User

    existing_user = User(
        email="exists@example.com",
        password_hash="hashed",
        name="Existing",
    )
    mock_user_storage.find_by_email = MagicMock(return_value=existing_user)

    body = RegisterRequest(
        email="exists@example.com",
        password="anypassword",
        name="Test",
    )

    before = _get_counter_value("sekb_register_attempts_total", result="email_exists")

    with pytest.raises(HTTPException) as exc_info:
        await register(body, fake_request)

    after = _get_counter_value("sekb_register_attempts_total", result="email_exists")

    assert exc_info.value.status_code == 400
    assert "已被注册" in exc_info.value.detail
    assert _delta(before, after) == 1


# ============================================================
# TC-004 补充：注册成功
# ============================================================

@pytest.mark.asyncio
async def test_register_success_records_metric(
    mock_user_storage, fake_request, patch_user_storage, patch_audit_log, patch_create_jwt
):
    """注册成功应记录 register_attempts_total{result="success"} +1。"""
    from app.api.routes.auth import register
    from app.models.user import RegisterRequest

    # 邮箱未被注册
    mock_user_storage.find_by_email = MagicMock(return_value=None)

    body = RegisterRequest(
        email="newuser@example.com",
        password="anypassword",
        name="NewUser",
    )

    before = _get_counter_value("sekb_register_attempts_total", result="success")

    # Mock hash_password 避免真实计算
    with patch("app.api.routes.auth.hash_password", return_value="hashed"):
        result = await register(body, fake_request)

    after = _get_counter_value("sekb_register_attempts_total", result="success")

    assert result.token == "fake-jwt-token"
    assert _delta(before, after) == 1
