"""职位采集结果缓存（JSON，14 天，最多 1000 条）。

按「用户 + 关键词 + 城市 + 薪资」为 key 缓存采集到的职位列表：
- 14 天内再次用相同条件采集时直接返回缓存，避免重复抓取；
- 刷新/删除单个职位时前端调用 ``/job/cache/save`` 同步覆盖缓存。

「搜索历史」相关（2026-09-15 新增）：
- 每次搜索有一个稳定的 ``search_id``（key 的 MD5 前 16 位），作为前后端之间
  引用「某一次搜索」的唯一标识（不含中文、URL 安全），**不面向用户展示**；
- **惰性清理**：调用 :func:`list_searches` 时，把超过 TTL 的条目清空 ``jobs``
  但**保留条目与 count**（前端打「已过期」角标）；过期条目的报告由上层路由清理。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

_FILE = Path("data/job_cache.json")
_TTL_SECONDS = 14 * 24 * 3600  # 14 天
_MAX_JOBS = 1000  # 单 key 最多缓存 1000 条
_MAX_ENTRIES = 200  # 最多缓存 200 个 key（按 ts 淘汰最旧），防止缓存文件无限增长


# ============================================================
# key / search_id
# ============================================================

def cache_key(
    user_id: str,
    keyword: str,
    city: str,
    min_salary_k: int,
    page: int = 0,
    limit: int = 20,
) -> str:
    """存储键（含中文，仅作 JSON map key 使用）。

    page/limit 仅在**偏离默认值**时才并入 key：这样既修复「第 2 页结果覆盖第 1 页」，
    又保证默认分页（page=0, limit=20）的 key 与历史缓存完全一致（不打断既有 search_id）。
    """
    base = f"{user_id}|{keyword}|{city}|{min_salary_k}"
    if page or limit != 20:
        return f"{base}|p{page}|l{limit}"
    return base


def make_search_id(key: str) -> str:
    """由存储键派生稳定的 search_id（MD5 前 16 位，URL 安全）。"""
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


def parse_key(key: str) -> dict[str, Any]:
    """从存储键解析出 user_id / keyword / city / min_salary_k（宽松，不报错）。"""
    seg = key.split("|")
    try:
        min_salary_k = int(seg[3]) if len(seg) > 3 and seg[3].lstrip("-").isdigit() else 0
    except (ValueError, IndexError):
        min_salary_k = 0
    return {
        "user_id": seg[0] if len(seg) > 0 else "",
        "keyword": seg[1] if len(seg) > 1 else "",
        "city": seg[2] if len(seg) > 2 else "",
        "min_salary_k": min_salary_k,
    }


# ============================================================
# 读写
# ============================================================

def _load() -> dict[str, Any]:
    if not _FILE.exists():
        return {}
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    # 原子写：先写临时文件再 os.replace，避免崩溃截断后下次 _load 静默清空缓存
    tmp = _FILE.with_name(_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_FILE)


def is_expired(entry: dict[str, Any], now: float | None = None) -> bool:
    """条目是否超过 TTL（14 天）。"""
    now = now if now is not None else time.time()
    return (now - (entry.get("ts", 0) or 0)) > _TTL_SECONDS


def save_cached_jobs(key: str, jobs: list[dict[str, Any]]) -> None:
    """保存（覆盖）某 key 的职位缓存，最多 1000 条（同时刷新 ts / search_id / count）。

    超出 `_MAX_ENTRIES` 个 key 时按 `ts` 淘汰最旧的条目——此前只限单 key 条数，
    key 数量无上限，缓存文件会随搜索次数无限增长。
    """
    data = _load()
    trimmed = jobs[:_MAX_JOBS]
    data[key] = {
        "ts": time.time(),
        "search_id": make_search_id(key),
        "count": len(trimmed),
        "jobs": trimmed,
    }
    if len(data) > _MAX_ENTRIES:
        # 按最后写入时间淘汰最旧的 (len(data) - _MAX_ENTRIES) 条
        keep = sorted(data.items(), key=lambda kv: (kv[1] or {}).get("ts", 0), reverse=True)
        data = dict(keep[:_MAX_ENTRIES])
    _save(data)


def get_cached_jobs(key: str) -> list[dict[str, Any]] | None:
    """返回某 key 的缓存职位；过期、不存在或已清空返回 None。"""
    entry = _load().get(key)
    if not entry or is_expired(entry):
        return None
    jobs = entry.get("jobs")
    return jobs if jobs else None


# ============================================================
# 搜索历史（含惰性清理）
# ============================================================

def list_searches(user_id: str) -> list[dict[str, Any]]:
    """列出某用户的搜索历史（按 ts 倒序），并对过期条目做**惰性清理**。

    惰性清理语义：超过 TTL 的条目 → 清空 ``jobs``（释放空间），**保留条目与 count**
    并标记 ``expired=True``，供前端打「已过期」角标。

    Returns:
        ``[{"search_id","keyword","city","min_salary_k","count","ts","expired",
        "has_jobs"}, ...]``（**不含 jobs**，避免响应过大；需要职位列表请用
        :func:`get_by_search_id`）。
    """
    data = _load()
    prefix = f"{user_id}|"
    now = time.time()
    out: list[dict[str, Any]] = []
    dirty = False

    for key, entry in data.items():
        if not key.startswith(prefix):
            continue
        meta = parse_key(key)
        expired = is_expired(entry, now)
        jobs = entry.get("jobs") or []

        if expired and jobs:
            # 惰性清理：清空 jobs，保留 count / ts / search_id
            entry["jobs"] = []
            entry["count"] = entry.get("count") or len(jobs)
            dirty = True
            jobs = []

        out.append({
            "search_id": entry.get("search_id") or make_search_id(key),
            "keyword": meta["keyword"],
            "city": meta["city"],
            "min_salary_k": meta["min_salary_k"],
            "count": entry.get("count", len(jobs)),
            "ts": entry.get("ts", 0) or 0,
            "expired": expired,
            "has_jobs": bool(jobs),
        })

    if dirty:
        _save(data)

    out.sort(key=lambda x: -x["ts"])
    return out


def get_by_search_id(user_id: str, sid: str) -> dict[str, Any] | None:
    """按 search_id 取某次搜索的职位列表（含元数据）；不存在返回 None。

    过期条目仍会返回元数据，但 ``jobs`` 为空、``expired=True``。
    """
    data = _load()
    prefix = f"{user_id}|"
    for key, entry in data.items():
        if not key.startswith(prefix):
            continue
        if (entry.get("search_id") or make_search_id(key)) != sid:
            continue
        meta = parse_key(key)
        jobs = entry.get("jobs") or []
        expired = is_expired(entry)
        return {
            "search_id": sid,
            "keyword": meta["keyword"],
            "city": meta["city"],
            "min_salary_k": meta["min_salary_k"],
            "count": entry.get("count", len(jobs)),
            "ts": entry.get("ts", 0) or 0,
            "expired": expired,
            "jobs": [] if expired else jobs,
        }
    return None


def expired_search_ids(user_id: str) -> list[str]:
    """返回该用户已过期条目的 search_id（供上层清理对应报告）。"""
    data = _load()
    prefix = f"{user_id}|"
    now = time.time()
    return [
        (entry.get("search_id") or make_search_id(key))
        for key, entry in data.items()
        if key.startswith(prefix) and is_expired(entry, now)
    ]


# ============================================================
# 兼容旧接口
# ============================================================

def get_latest_cached(user_id: str) -> dict[str, Any] | None:
    """返回某用户最后一次（ts 最新）**未过期**的缓存职位 + 筛选条件。

    返回：``{"keyword","city","min_salary_k","search_id","jobs"}``；
    无或全部过期/清空返回 None。
    """
    data = _load()
    prefix = f"{user_id}|"
    best_ts = 0.0
    best_key = ""
    best_entry: dict[str, Any] | None = None
    now = time.time()

    for key, entry in data.items():
        if not key.startswith(prefix):
            continue
        ts = entry.get("ts", 0) or 0
        if is_expired(entry, now):
            continue
        if not (entry.get("jobs") or []):
            continue
        if ts > best_ts:
            best_ts = ts
            best_key = key
            best_entry = entry

    if best_entry is None:
        return None

    meta = parse_key(best_key)
    return {
        "keyword": meta["keyword"],
        "city": meta["city"],
        "min_salary_k": meta["min_salary_k"],
        "search_id": best_entry.get("search_id") or make_search_id(best_key),
        "jobs": best_entry.get("jobs") or [],
    }


def list_all_cached(user_id: str) -> list[dict[str, Any]]:
    """返回某用户所有**未过期且有职位**的缓存集合（供「投递计划」选填职位）。"""
    data = _load()
    prefix = f"{user_id}|"
    now = time.time()
    out: list[dict[str, Any]] = []

    for key, entry in data.items():
        if not key.startswith(prefix):
            continue
        if is_expired(entry, now):
            continue
        jobs = entry.get("jobs") or []
        if not jobs:
            continue
        meta = parse_key(key)
        out.append({
            "search_id": entry.get("search_id") or make_search_id(key),
            "keyword": meta["keyword"],
            "city": meta["city"],
            "min_salary_k": meta["min_salary_k"],
            "count": len(jobs),
            "jobs": jobs,
            "ts": entry.get("ts", 0) or 0,
        })

    out.sort(key=lambda x: -x["ts"])
    return out
