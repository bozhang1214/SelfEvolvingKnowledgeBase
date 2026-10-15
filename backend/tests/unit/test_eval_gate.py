"""评测门禁单测。

**核心不是"阈值比较"能不能算对，而是"门禁跑不起来时必须失败"** ——
一个无法执行却算通过的门禁，就是原本那个 `|| echo "eval non-blocking"` 假门禁。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.eval.gate import (
    GateThresholds,
    build_fingerprint,
    evaluate_gate,
    load_baseline,
    make_baseline,
)


@dataclass
class _Report:
    """最小可用的 EvalReport 替身。"""

    pass_rate: float = 0.0
    total: int = 0
    passed: int = 0
    aggregated: dict[str, dict[str, float]] = field(default_factory=dict)


# ---------------- 通过率 ----------------


class TestPassRateGate:
    def test_passes_when_above_threshold(self):
        r = evaluate_gate(_Report(pass_rate=0.95), GateThresholds(pass_rate_min=0.90))
        assert r.ok is True and len(r.checks) == 1

    def test_fails_when_below_threshold(self):
        r = evaluate_gate(_Report(pass_rate=0.70), GateThresholds(pass_rate_min=0.90))
        assert r.ok is False
        assert [c.name for c in r.failures] == ["pass_rate_min"]
        assert r.failures[0].actual == 0.7

    def test_equal_to_threshold_passes(self):
        r = evaluate_gate(_Report(pass_rate=0.9), GateThresholds(pass_rate_min=0.9))
        assert r.ok is True

    def test_zero_threshold_means_not_configured(self):
        r = evaluate_gate(_Report(pass_rate=0.0), GateThresholds(pass_rate_min=0.0))
        assert r.checks == [] and r.ok is True

    def test_no_checks_still_ok(self):
        """未配置判据时 ok=True —— 但 CLI 会显式提示"本次不构成门禁"。"""
        assert evaluate_gate(_Report(pass_rate=0.1), GateThresholds()).ok is True


# ---------------- 指标下限 ----------------


class TestMetricGate:
    def test_metric_above_min_passes(self):
        r = _Report(aggregated={"answer_groundedness": {"mean": 0.85}})
        gate = evaluate_gate(r, GateThresholds(metric_mins={"answer_groundedness": 0.8}))
        assert gate.ok is True

    def test_metric_below_min_fails(self):
        r = _Report(aggregated={"answer_groundedness": {"mean": 0.5}})
        gate = evaluate_gate(r, GateThresholds(metric_mins={"answer_groundedness": 0.8}))
        assert gate.ok is False
        assert gate.failures[0].name == "metric:answer_groundedness"

    def test_missing_metric_fails_instead_of_silently_passing(self):
        """**关键**：阈值要求的指标在报告里缺失 → 判失败。

        否则改个指标名/口径就能让门禁静默失效（分数全丢，却显示"通过"）。
        """
        r = _Report(aggregated={"other_metric": {"mean": 0.99}})
        gate = evaluate_gate(r, GateThresholds(metric_mins={"answer_groundedness": 0.8}))
        assert gate.ok is False
        f = gate.failures[0]
        assert f.actual is None
        assert "缺少该指标的聚合值" in f.detail
        assert "other_metric" in f.detail  # 提示现有指标，便于排查

    def test_metric_without_mean_fails(self):
        r = _Report(aggregated={"answer_groundedness": {"median": 0.9}})
        gate = evaluate_gate(r, GateThresholds(metric_mins={"answer_groundedness": 0.8}))
        assert gate.ok is False


# ---------------- 基线比对（防渐进退化）----------------


class TestBaselineGate:
    def test_no_regression_passes(self):
        base = {"pass_rate": 0.9}
        gate = evaluate_gate(_Report(pass_rate=0.9),
                             GateThresholds(baseline=base, baseline_path="b.json"))
        assert gate.ok is True
        assert any(c.name == "baseline_pass_rate" for c in gate.checks)

    def test_progressive_regression_detected_even_above_absolute_threshold(self):
        """绝对阈值掩盖不了的退化：0.95 → 0.90，都 >= 0.90 的绝对线，但要被基线抓住。"""
        base = {"pass_rate": 0.95}
        gate = evaluate_gate(
            _Report(pass_rate=0.90),
            GateThresholds(pass_rate_min=0.90, baseline=base, max_regression=0.0),
        )
        assert gate.ok is False
        assert [c.name for c in gate.failures] == ["baseline_pass_rate"]

    def test_max_regression_tolerance_allows_small_drop(self):
        base = {"pass_rate": 0.95}
        gate = evaluate_gate(_Report(pass_rate=0.94),
                             GateThresholds(baseline=base, max_regression=0.02))
        assert gate.ok is True

    def test_baseline_monotonic_counter_metrics_compared(self):
        base = {"pass_rate": 0.9, "metrics": {"answer_groundedness": 0.88}}
        r = _Report(pass_rate=0.9, aggregated={"answer_groundedness": {"mean": 0.80}})
        gate = evaluate_gate(r, GateThresholds(baseline=base))
        assert gate.ok is False
        assert "baseline_metric:answer_groundedness" in [c.name for c in gate.failures]

    def test_baseline_without_pass_rate_fails(self):
        """基线结构不对 → 判失败（不能因为读不懂基线就放行）。"""
        gate = evaluate_gate(_Report(pass_rate=0.99), GateThresholds(baseline={"total": 10}))
        assert gate.ok is False
        assert gate.failures[0].name == "baseline"

    def test_baseline_metric_absent_from_report_is_skipped(self):
        """基线里有、报告里没有的指标不额外判（避免口径变化误报）。"""
        base = {"pass_rate": 0.9, "metrics": {"legacy_metric": 0.9}}
        gate = evaluate_gate(_Report(pass_rate=0.9), GateThresholds(baseline=base))
        assert gate.ok is True


# ---------------- 基线文件读写 ----------------


class TestBaselineIO:
    def test_load_missing_file_returns_none(self, tmp_path: Path):
        assert load_baseline(tmp_path / "nope.json") is None

    def test_load_corrupt_file_returns_none(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{ not json", encoding="utf-8")
        assert load_baseline(p) is None

    def test_load_non_dict_returns_none(self, tmp_path: Path):
        p = tmp_path / "list.json"
        p.write_text("[1,2,3]", encoding="utf-8")
        assert load_baseline(p) is None

    def test_load_valid(self, tmp_path: Path):
        p = tmp_path / "b.json"
        p.write_text(json.dumps({"pass_rate": 0.93}), encoding="utf-8")
        assert load_baseline(p) == {"pass_rate": 0.93}

    def test_make_baseline_shape(self):
        r = _Report(pass_rate=0.9234, total=10, passed=9,
                    aggregated={"answer_groundedness": {"mean": 0.8567}})
        b = make_baseline(r, {"commit": "abc1234"})
        assert b["pass_rate"] == 0.9234
        assert b["total"] == 10 and b["passed"] == 9
        assert b["metrics"]["answer_groundedness"] == 0.8567
        assert b["fingerprint"]["commit"] == "abc1234"

    def test_roundtrip_write_then_gate(self, tmp_path: Path):
        """写出的基线能被读回并用于判定（闭环）。"""
        p = tmp_path / "b.json"
        p.write_text(json.dumps(make_baseline(_Report(pass_rate=0.9))), encoding="utf-8")
        base = load_baseline(p)
        assert evaluate_gate(_Report(pass_rate=0.9), GateThresholds(baseline=base)).ok is True
        assert evaluate_gate(_Report(pass_rate=0.8), GateThresholds(baseline=base)).ok is False


# ---------------- 指纹 ----------------


class TestFingerprint:
    def test_contains_commit_and_prompt_hash(self):
        fp = build_fingerprint()
        assert "commit" in fp  # 取不到时为 "unknown"，但键必须在
        # 仓库里有 prompt/ 目录时应有提示词指纹
        if "prompt_md5" in fp:
            assert len(fp["prompt_md5"]) == 12

    def test_dataset_hash_changes_with_content(self, tmp_path: Path):
        a = tmp_path / "a.json"
        a.write_text('[{"q":"1"}]', encoding="utf-8")
        b = tmp_path / "b.json"
        b.write_text('[{"q":"2"}]', encoding="utf-8")
        fa, fb = build_fingerprint(str(a)), build_fingerprint(str(b))
        assert fa["dataset_md5"] != fb["dataset_md5"]
        assert fa["dataset_name"] == "a.json"

    def test_missing_dataset_does_not_crash(self):
        fp = build_fingerprint("/nonexistent/x.json")
        assert "dataset_md5" not in fp


# ---------------- to_dict ----------------


def test_to_dict_is_serializable():
    r = evaluate_gate(_Report(pass_rate=0.5), GateThresholds(pass_rate_min=0.9))
    d = r.to_dict()
    assert d["ok"] is False and d["failures"] == ["pass_rate_min"]
    json.dumps(d, ensure_ascii=False)  # 必须可序列化（CI 要把结果打进日志）


def test_any_input_type_is_tolerated() -> None:
    """判据只依赖属性访问，不依赖具体类型 —— 避免与 EvalReport 强耦合。"""

    class _Duck:
        pass_rate = 0.95
        aggregated: dict[str, Any] = {}

    assert evaluate_gate(_Duck(), GateThresholds(pass_rate_min=0.9)).ok is True


# ---------------- 单用例超时（挂住必须变成失败）----------------


class TestCaseTimeout:
    """回归背景（2026-10-12 实测）：评测会在某条用例上**永久挂起** ——
    第 2 条卡死 5 分钟以上、报告永不生成、进程一直停在 ep_poll。
    没有超时的话，这不是"慢"而是"永远不结束"，接进 CI 就是 job 一直挂着。
    """

    def test_timeout_constant_is_configured(self):
        from app.eval import runner as eval_runner

        assert eval_runner._CASE_TIMEOUT_S > 0
        assert eval_runner._CASE_TIMEOUT_S <= 3600  # 合理上限，避免被误设为极大值

    @pytest.mark.asyncio
    async def test_hanging_case_becomes_failure_not_hang(self, sample_config, monkeypatch):
        from app.eval import runner as eval_runner

        monkeypatch.setattr(eval_runner, "_CASE_TIMEOUT_S", 0.05)
        runner = eval_runner.EvalRunner(config=sample_config, llm_factory=None,
                              tool_registry=None, memory=None)
        monkeypatch.setattr(runner, "_load_dataset", lambda _p: [{"test_id": "hang", "input": "x"}])
        monkeypatch.setattr(runner, "_get_graph", lambda: None)

        async def _hang(_graph: Any, _case: dict) -> Any:
            import asyncio as _a

            await _a.sleep(30)  # 模拟挂住的工具调用

        monkeypatch.setattr(runner, "_run_single_case", _hang)

        report = await eval_runner.EvalRunner.run_dataset(runner, "fake.json")
        assert report.total == 1
        assert report.passed == 0
        assert report.results[0].passed is False
        assert "超时" in (report.results[0].error or "")
