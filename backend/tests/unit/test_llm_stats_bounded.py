"""`LLMCallStats.records` 定长化 + 快照差值口径的回归测试（2026-10-12 修）。

**背景**：`records` 原先是无界 `list`，每次 LLM 调用 `append`，长期运行的服务只增不减
（内存持续增长）。改为定长 `deque` 后**不能沿用下标切片取差值** ——
队列会淘汰旧元素，`records[snapshot.records_len:]` 会错位切到不属于本次请求的记录。
本文件把这两个口径都钉住。
"""
from __future__ import annotations

import pytest

from app.core import llm_factory as llm_factory_mod
from app.core.llm_factory import LLMCallRecord, LLMCallStats, LLMFactory


def _rec(model: str, *, retried: bool = False, degraded: bool = False) -> LLMCallRecord:
    return LLMCallRecord(
        role="executor", model=model, configured_model=model,
        input_tokens=10, output_tokens=5, latency_ms=100, cost_usd=0.001,
        success=True, retried=retried, degraded=degraded,
    )


# ---------------- 定长 ----------------


class TestRecordsBounded:
    def test_records_stop_growing_at_maxlen(self, monkeypatch):
        monkeypatch.setattr(llm_factory_mod, "_RECORDS_MAXLEN", 5)
        stats = LLMCallStats()
        for i in range(50):
            stats.records.append(_rec(f"m{i}"))
        assert len(stats.records) == 5

    def test_keeps_most_recent_entries(self, monkeypatch):
        monkeypatch.setattr(llm_factory_mod, "_RECORDS_MAXLEN", 3)
        stats = LLMCallStats()
        for i in range(5):
            stats.records.append(_rec(f"m{i}"))
        assert [r.model for r in stats.records] == ["m2", "m3", "m4"]

    def test_default_maxlen_is_bounded_and_reasonable(self):
        """默认上限必须有限（防止将来被人改成 None 又变成无界）。"""
        assert isinstance(llm_factory_mod._RECORDS_MAXLEN, int)
        assert 100 <= llm_factory_mod._RECORDS_MAXLEN <= 100_000

    def test_direct_construction_is_bounded_too(self):
        """不经过 monkeypatch 时，直接构造也必须是定长队列。"""
        stats = LLMCallStats()
        assert getattr(stats.records, "maxlen", None) == llm_factory_mod._RECORDS_MAXLEN


# ---------------- 快照差值（淘汰后仍正确）----------------


class TestDeltaWithEviction:
    def test_delta_excludes_evicted_pre_snapshot_records(self, sample_config, monkeypatch):
        """快照前有记录、快照后新增到触发淘汰 —— 差值必须只反映**快照之后**的记录。

        这正是原来的下标切片会出错的场景：
        maxlen=3，先写 A,B（快照）→ 再写 C,D,E（A,B 被淘汰）
        正确：delta 模型 = {C,D,E}
        旧实现：`records[2:]` 只剩 [E]（错位丢了两条）
        """
        monkeypatch.setattr(llm_factory_mod, "_RECORDS_MAXLEN", 3)
        factory = LLMFactory(sample_config)

        for m in ("A", "B"):
            factory.stats.records.append(_rec(m))
            factory.stats.total_calls += 1

        snapshot = factory.snapshot_stats()
        assert snapshot.total_calls == 2

        for m in ("C", "D", "E"):
            factory.stats.records.append(_rec(m))
            factory.stats.total_calls += 1

        delta = factory.delta_stats(snapshot)
        assert sorted(delta["model_used"]) == ["C", "D", "E"]
        assert delta["total_calls"] == 3

    def test_delta_counts_retries_and_degradations(self, sample_config, monkeypatch):
        monkeypatch.setattr(llm_factory_mod, "_RECORDS_MAXLEN", 4)
        factory = LLMFactory(sample_config)
        snapshot = factory.snapshot_stats()

        factory.stats.records.append(_rec("A", retried=True))
        factory.stats.total_calls += 1
        factory.stats.records.append(_rec("B", degraded=True))
        factory.stats.total_calls += 1

        delta = factory.delta_stats(snapshot)
        assert delta["llm_retried"] is True and delta["llm_retry_count"] == 1
        assert delta["llm_degraded"] is True and delta["llm_degradation_count"] == 1

    def test_delta_is_empty_when_no_new_calls(self, sample_config):
        factory = LLMFactory(sample_config)
        factory.stats.records.append(_rec("A"))
        factory.stats.total_calls += 1
        snapshot = factory.snapshot_stats()
        delta = factory.delta_stats(snapshot)
        assert delta["model_used"] == []
        assert delta["llm_retried"] is False
        assert delta["total_calls"] == 0

    def test_delta_tolerates_eviction_beyond_window(self, sample_config, monkeypatch):
        """极端情况：本次新增超过窗口上限 → 只能看到窗口内那部分，且不得抛异常。"""
        monkeypatch.setattr(llm_factory_mod, "_RECORDS_MAXLEN", 2)
        factory = LLMFactory(sample_config)
        snapshot = factory.snapshot_stats()
        for m in ("A", "B", "C", "D"):
            factory.stats.records.append(_rec(m))
            factory.stats.total_calls += 1
        delta = factory.delta_stats(snapshot)
        assert sorted(delta["model_used"]) == ["C", "D"]
        assert delta["total_calls"] == 4  # 计数仍准确（来自单调计数器，不受淘汰影响）


# ---------------- retry_rate 口径 ----------------


class TestRetryRate:
    def test_denominator_is_window_not_total_calls(self):
        """分子是窗口内重试数，分母也必须是窗口长度 —— 否则得到无意义的数。"""
        stats = LLMCallStats()
        for i in range(4):
            stats.records.append(_rec(f"m{i}", retried=(i == 0)))
        stats.total_calls = 1000  # 全量调用数远大于窗口
        assert stats.retry_rate == pytest.approx(0.25)

    def test_empty_window_rate_is_zero(self):
        assert LLMCallStats().retry_rate == 0.0
