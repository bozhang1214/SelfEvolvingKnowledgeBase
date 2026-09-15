"""
单职位深度分析结果缓存（14 天）。

同一个 JD（按规范化文本哈希）的分析结果在 14 天内直接复用，
避免重复调用 8 步 LLM 流水线。缓存按 user_id 隔离，持久化在
``data/job_analysis_cache.json``。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

_CACHE_FILE = Path("data/job_analysis_cache.json")
_TTL_SECONDS = 14 * 24 * 3600  # 14 天


def _cache_key(user_id: str, jd_text: str) -> str:
    digest = hashlib.md5((jd_text or "").strip()[:3000].encode("utf-8")).hexdigest()
    return f"{user_id}:{digest}"


def _load() -> dict[str, Any]:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 原子写：先写临时文件再 replace，避免崩溃截断后静默清空缓存
    tmp = _CACHE_FILE.with_name(_CACHE_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_CACHE_FILE)


def get_cached_analysis(user_id: str, jd_text: str) -> dict[str, Any] | None:
    """返回缓存的单职位分析结果；过期或不存在返回 None。"""
    entry = _load().get(_cache_key(user_id, jd_text))
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > _TTL_SECONDS:
        return None
    return entry.get("result")


def save_analysis(user_id: str, jd_text: str, result: dict[str, Any]) -> None:
    """保存单职位分析结果。"""
    data = _load()
    data[_cache_key(user_id, jd_text)] = {"ts": time.time(), "result": result}
    _save(data)
