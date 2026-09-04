"""
职位采集结果缓存（JSON，14 天，最多 1000 条）。

按「用户 + 关键词 + 城市 + 薪资」为 key 缓存采集到的职位列表。
14 天内再次用相同条件采集时直接返回缓存，避免重复抓取。
刷新/删除单个职位时前端调用 /job/cache/save 同步覆盖缓存。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_FILE = Path("data/job_cache.json")
_TTL_SECONDS = 14 * 24 * 3600  # 14 天
_MAX_JOBS = 1000  # 单 key 最多缓存 1000 条


def cache_key(user_id: str, keyword: str, city: str, min_salary_k: int) -> str:
    return f"{user_id}|{keyword}|{city}|{min_salary_k}"


def _load() -> dict[str, Any]:
    if not _FILE.exists():
        return {}
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_cached_jobs(key: str) -> list[dict[str, Any]] | None:
    """返回某 key 的缓存职位；过期或不存在返回 None。"""
    entry = _load().get(key)
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > _TTL_SECONDS:
        return None
    return entry.get("jobs")


def save_cached_jobs(key: str, jobs: list[dict[str, Any]]) -> None:
    """保存（覆盖）某 key 的职位缓存，最多 1000 条。"""
    data = _load()
    data[key] = {"ts": time.time(), "jobs": jobs[:_MAX_JOBS]}
    _save(data)
