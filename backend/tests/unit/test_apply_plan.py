"""投递作战计划（apply_plan）单元测试。

覆盖：
- 新增 / 更新 / 用户隔离 / 删除 / 统计
- 白名单字段规范化（tier / status / cooldown_months 脏值回退，不报错）
- 冷却期计算（挂面 + 结果日期 + 冷却月数 → cooldown_until / cooling / days_left）
- 月末溢出（1/31 + 1 月 → 2/28）
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents.job import apply_plan


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    """把存储文件重定向到临时目录，测试间互不污染。"""
    monkeypatch.setattr(apply_plan, "_FILE", tmp_path / "job_apply_plan.json")


# ============================================================
# 增删改查
# ============================================================

class TestUpsert:
    def test_create_and_list(self):
        item = apply_plan.upsert_plan("u1", {"company": "字节", "title": "方案架构师", "tier": 1})
        assert item["id"]
        assert item["company"] == "字节"
        assert item["status"] == "planned"
        assert item["can_apply"] is True
        assert len(apply_plan.list_plans("u1")) == 1

    def test_update_by_id_does_not_duplicate(self):
        item = apply_plan.upsert_plan("u1", {"company": "字节"})
        updated = apply_plan.upsert_plan(
            "u1", {"id": item["id"], "company": "字节跳动", "status": "applied"}
        )
        assert updated["id"] == item["id"]
        assert updated["company"] == "字节跳动"
        assert updated["status"] == "applied"
        assert len(apply_plan.list_plans("u1")) == 1

    def test_user_isolation(self):
        apply_plan.upsert_plan("u1", {"company": "A"})
        apply_plan.upsert_plan("u2", {"company": "B"})
        assert [i["company"] for i in apply_plan.list_plans("u1")] == ["A"]
        assert [i["company"] for i in apply_plan.list_plans("u2")] == ["B"]

    def test_delete_twice(self):
        item = apply_plan.upsert_plan("u1", {"company": "A"})
        assert apply_plan.delete_plan("u1", item["id"]) is True
        assert apply_plan.delete_plan("u1", item["id"]) is False
        assert apply_plan.list_plans("u1") == []


# ============================================================
# 字段规范化（宽松，不因脏值报错）
# ============================================================

class TestNormalization:
    def test_invalid_tier_falls_back_to_1(self):
        assert apply_plan.upsert_plan("u1", {"tier": 99})["tier"] == 1
        assert apply_plan.upsert_plan("u1", {"tier": "abc"})["tier"] == 1
        assert apply_plan.upsert_plan("u1", {"tier": 3})["tier"] == 3

    def test_invalid_status_falls_back_to_planned(self):
        assert apply_plan.upsert_plan("u1", {"status": "hacked"})["status"] == "planned"
        assert apply_plan.upsert_plan("u1", {"status": "offer"})["status"] == "offer"

    def test_cooldown_out_of_range(self):
        assert apply_plan.upsert_plan("u1", {"cooldown_months": -1})["cooldown_months"] == 0
        assert apply_plan.upsert_plan("u1", {"cooldown_months": 99})["cooldown_months"] == 0
        assert apply_plan.upsert_plan("u1", {"cooldown_months": 6})["cooldown_months"] == 6


# ============================================================
# 冷却期计算（核心）
# ============================================================

class TestCooldown:
    def test_rejected_with_cooldown_is_cooling(self):
        item = apply_plan.upsert_plan(
            "u1",
            {
                "company": "字节",
                "status": "rejected",
                "result_at": date.today().isoformat(),
                "cooldown_months": 6,
            },
        )
        assert item["cooling"] is True
        assert item["can_apply"] is False
        assert item["cooldown_until"]
        # 6 个月 ≈ 180 天（容忍月长差异）
        assert 150 < item["days_left"] < 200

    def test_expired_cooldown_can_apply(self):
        past = (date.today() - timedelta(days=400)).isoformat()
        item = apply_plan.upsert_plan(
            "u1", {"status": "rejected", "result_at": past, "cooldown_months": 6}
        )
        assert item["cooling"] is False
        assert item["can_apply"] is True
        assert item["cooldown_until"]  # 仍显示解冻日期

    def test_not_rejected_has_no_cooldown(self):
        item = apply_plan.upsert_plan(
            "u1",
            {"status": "applied", "result_at": date.today().isoformat(), "cooldown_months": 6},
        )
        assert item["cooling"] is False
        assert item["cooldown_until"] == ""

    def test_rejected_without_result_date_or_months(self):
        assert apply_plan.upsert_plan("u1", {"status": "rejected", "cooldown_months": 6})[
            "cooldown_until"
        ] == ""
        assert apply_plan.upsert_plan(
            "u1", {"status": "rejected", "result_at": date.today().isoformat()}
        )["cooldown_until"] == ""

    def test_add_months_handles_month_end_overflow(self):
        # 1/31 + 1 月 → 2/28（2026 非闰年）
        assert apply_plan._add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
        # 8/31 + 6 月 → 次年 2/28
        assert apply_plan._add_months(date(2025, 8, 31), 6) == date(2026, 2, 28)
        # 跨年正常情况
        assert apply_plan._add_months(date(2025, 11, 15), 3) == date(2026, 2, 15)
        # 闰年 2 月
        assert apply_plan._add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


# ============================================================
# 统计
# ============================================================

class TestStats:
    def test_stats_counts(self):
        apply_plan.upsert_plan("u1", {"status": "planned"})
        apply_plan.upsert_plan("u1", {"status": "applied"})
        apply_plan.upsert_plan("u1", {"status": "interview"})
        apply_plan.upsert_plan(
            "u1",
            {
                "status": "rejected",
                "result_at": date.today().isoformat(),
                "cooldown_months": 6,
            },
        )
        stats = apply_plan.compute_stats(apply_plan.list_plans("u1"))
        assert stats["total"] == 4
        assert stats["planned"] == 1
        assert stats["applied"] == 1
        assert stats["interview"] == 1
        assert stats["rejected"] == 1
        assert stats["cooling"] == 1

    def test_stats_empty(self):
        stats = apply_plan.compute_stats([])
        assert stats["total"] == 0
        assert stats["cooling"] == 0
