"""投递作战计划（SEKB 侧存储接线）。

**P0 起领域逻辑（冷却期计算 / 字段规范化 / CRUD）已抽到 ``jobcopilot`` 包**，
本模块只负责把 SEKB 的存储路径注入内核。

``_FILE`` 保持模块级变量：现有单测通过 ``monkeypatch.setattr(apply_plan, "_FILE", ...)``
隔离存储，保留它可以让测试与线上行为都不变。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jobcopilot.core.analyzers import apply_plan as _core
from jobcopilot.storage.json_store import JsonFileStore

_FILE = Path("data/job_apply_plan.json")

# 私有工具转发：现有单测直接调 `apply_plan._add_months` 验证月末溢出，
# 保留同名引用即可让测试零改动通过。
_add_months = _core._add_months
_parse_date = _core._parse_date

# 状态/分层枚举（从内核转发，供调用方沿用旧导入路径）
STATUS_PLANNED = _core.STATUS_PLANNED
STATUS_APPLIED = _core.STATUS_APPLIED
STATUS_INTERVIEW = _core.STATUS_INTERVIEW
STATUS_REJECTED = _core.STATUS_REJECTED
STATUS_OFFER = _core.STATUS_OFFER
STATUSES = _core.STATUSES
TIERS = _core.TIERS

__all__ = [
    "list_plans",
    "compute_stats",
    "upsert_plan",
    "delete_plan",
    "enrich",
    "STATUSES",
    "TIERS",
]


def _store() -> JsonFileStore:
    """每次调用现取 ``_FILE``，保证 monkeypatch / 配置变更即时生效。"""
    return JsonFileStore(_FILE)


def enrich(item: dict[str, Any]) -> dict[str, Any]:
    """补齐冷却期派生字段（见内核实现）。"""
    return _core.enrich(item)


def list_plans(user_id: str) -> list[dict[str, Any]]:
    """返回某用户的投递计划列表（按创建时间倒序，已补冷却期字段）。"""
    return _core.list_plans(user_id, store=_store())


def compute_stats(items: list[dict[str, Any]]) -> dict[str, int]:
    """统计投递进度（供顶部卡片展示）。"""
    return _core.compute_stats(items)


def upsert_plan(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """新增或更新一条投递记录（payload 含 id 则更新，否则新增）。"""
    return _core.upsert_plan(user_id, payload, store=_store())


def delete_plan(user_id: str, plan_id: str) -> bool:
    """删除一条投递记录，返回是否真的删了。"""
    return _core.delete_plan(user_id, plan_id, store=_store())
