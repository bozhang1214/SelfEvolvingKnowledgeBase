"""通用工具函数（单一实现，供各层复用）。

本模块收敛了原先散落在多处的重复实现（见 REFACTORING-PLAN WP1）：
- ``now_iso``：原 `graph/state.py` 与 `storage/base.py` 双份
- ``fmt_dt``：原 `api/routes/{share,knowledge,chat_share}.py` 三份
- ``safe_float``：原 `agents/{critic,scribe}.py` 双份
- ``to_state_dict``：原 `api/routes/chat.py`、`cli/chat.py`、`eval/runner.py` 三份
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


def fmt_dt(value: Any) -> str:
    """时间字段统一序列化：兼容 None、str 与 datetime。

    - None → 空字符串
    - str → 原样返回（Chroma 元数据等场景）
    - 其他（datetime/date）→ ``isoformat()``
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.isoformat()


def safe_float(value: Any, min_val: float, max_val: float) -> float:
    """安全转换为 float 并裁剪到 [min_val, max_val] 区间。

    非数值（None/非数字字符串等）返回 0.0，避免上游异常。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(min_val, min(max_val, v))


def to_state_dict(final_state: Any, *, strict: bool = False) -> dict[str, Any]:
    """从 LangGraph ``ainvoke``/``astream_events`` 返回值中提取 GraphState 字典。

    LangGraph 不同版本可能返回 dict、带 ``values()`` 方法的对象或普通对象，
    统一转换为 dict 形式以便访问字段。

    Args:
        final_state: LangGraph 调用的返回值。
        strict: 为 True 时，全部转换失败抛 ``ValueError``；否则返回 ``{}``。

    Returns:
        GraphState 字典。
    """
    if isinstance(final_state, dict):
        return final_state
    if hasattr(final_state, "values"):
        try:
            return dict(final_state.values())
        except Exception:
            pass
    try:
        return dict(final_state)
    except Exception as exc:
        if strict:
            raise ValueError(f"无法从 LangGraph 返回值提取 state: {exc}") from exc
        return {}
