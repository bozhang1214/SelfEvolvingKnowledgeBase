"""core.utils 工具函数单测（WP1 收敛后新增）。"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.core.utils import fmt_dt, now_iso, safe_float, to_state_dict


def test_now_iso_returns_utc_iso_string():
    s = now_iso()
    assert isinstance(s, str)
    assert s.endswith("+00:00")
    dt = datetime.fromisoformat(s)
    assert dt.tzinfo is not None


def test_fmt_dt_none_str_datetime():
    assert fmt_dt(None) == ""
    assert fmt_dt("2026-01-01") == "2026-01-01"
    dt = datetime(2026, 1, 2, 3, 4, 5)
    assert fmt_dt(dt) == dt.isoformat()


@pytest.mark.parametrize(
    "value,min_val,max_val,expected",
    [
        (0.5, 0.0, 1.0, 0.5),
        (-0.5, 0.0, 1.0, 0.0),
        (1.5, 0.0, 1.0, 1.0),
        ("0.8", 0.0, 1.0, 0.8),
        ("高", 0.0, 1.0, 0.0),
        (None, 0.0, 1.0, 0.0),
        (0.5, 0.0, 10.0, 0.5),
    ],
)
def test_safe_float(value, min_val, max_val, expected):
    assert safe_float(value, min_val, max_val) == expected


class _ValuesObj:
    def values(self):
        return {"a": 1, "b": 2}


class _BrokenValuesObj:
    def values(self):
        raise RuntimeError("boom")


def test_to_state_dict_passthrough_dict():
    d = {"a": 1}
    assert to_state_dict(d) == d


def test_to_state_dict_values_object():
    assert to_state_dict(_ValuesObj()) == {"a": 1, "b": 2}


def test_to_state_dict_broken_values_falls_back_to_empty():
    # values() 抛异常 → 回退 dict(obj) 也失败 → 返回 {}
    assert to_state_dict(_BrokenValuesObj()) == {}


def test_to_state_dict_plain_object_returns_empty():
    assert to_state_dict(object()) == {}


def test_to_state_dict_strict_raises_on_failure():
    with pytest.raises(ValueError):
        to_state_dict(object(), strict=True)


def test_to_state_dict_strict_ok_for_dict():
    assert to_state_dict({"k": "v"}, strict=True) == {"k": "v"}
