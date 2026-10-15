"""LLM 指标两处缺陷的回归测试（2026-10-12 修）。

**缺陷 A：token / 成本被重复计数约 2×**
  `record_llm_call`（单次粒度）接入后，`record_chat_metrics` 里的"按请求聚合"写入
  没有删除，两者写**同一个 Prometheus 序列**。
**缺陷 B：`LLMCallStats.records` 无界增长**
  原先无界 `list` + 每次调用 `append`，长期运行只增不减；且快照用下标切片取差值，
  不能简单换成定长队列。
"""
from __future__ import annotations

from app.core import metrics as metrics_mod


def _counter(metric: object, **labels: str) -> float:
    child = metric.labels(**labels) if labels else metric  # type: ignore[attr-defined]
    return float(child._value.get())  # noqa: SLF001


# ---------------- 缺陷 A：重复计数 ----------------


class TestNoDoubleCounting:
    def test_record_chat_metrics_does_not_touch_llm_token_or_cost(self):
        """`record_chat_metrics` 不得再写 tokens/cost —— 唯一事实源是 `record_llm_call`。"""
        before_tok = _counter(metrics_mod.llm_tokens_total, model="m1", direction="input", role="executor")
        before_cost = _counter(metrics_mod.llm_cost_usd_total, model="m1", role="executor")
        before_calls = _counter(metrics_mod.llm_calls_total, model="m1", role="aggregate", status="success")

        metrics_mod.record_chat_metrics({
            "conversation_id": "c1",
            "intent": "web_default",
            "latency_ms": 1000,
            "metrics": {
                "total_input_tokens": 1234,
                "total_output_tokens": 567,
                "total_cost_usd": 0.5,
                "model_used": ["m1"],
                "llm_retried": True,
                "llm_retry_count": 3,
                "llm_degraded": True,
                "llm_degradation_count": 2,
            },
        })

        assert _counter(metrics_mod.llm_tokens_total, model="m1", direction="input", role="executor") == before_tok
        assert _counter(metrics_mod.llm_cost_usd_total, model="m1", role="executor") == before_cost
        # role="aggregate" 这条系列不再被写（口径混入 llm_calls_total 是缺陷的一部分）
        assert _counter(metrics_mod.llm_calls_total, model="m1", role="aggregate", status="success") == before_calls

    def test_record_chat_metrics_still_writes_quality_metrics(self):
        """删除 LLM 聚合写入不应误伤同函数里的质量指标。"""
        before = _counter(metrics_mod.messages_total, user_id="default", intent="probe_intent", status="success")
        metrics_mod.record_chat_metrics({
            "conversation_id": "c2", "intent": "probe_intent", "latency_ms": 10, "metrics": {},
        })
        after = _counter(metrics_mod.messages_total, user_id="default", intent="probe_intent", status="success")
        assert after == before + 1


# ---------------- 缺陷修复后：单次粒度写入带 role ----------------


class TestRecordLlmCallAttribution:
    def test_tokens_and_cost_carry_role_label(self):
        """按 Agent 归因：tokens/cost 必须能被 role 区分。"""
        metrics_mod.record_llm_call(
            role="planner", model="m2", latency_ms=100, success=True,
            input_tokens=111, output_tokens=222, cost_usd=0.25,
        )
        assert _counter(metrics_mod.llm_tokens_total, model="m2", direction="input", role="planner") >= 111
        assert _counter(metrics_mod.llm_tokens_total, model="m2", direction="output", role="planner") >= 222
        assert _counter(metrics_mod.llm_cost_usd_total, model="m2", role="planner") >= 0.25
        # 另一个 role 不应被计入
        assert _counter(metrics_mod.llm_tokens_total, model="m2", direction="input", role="scribe") == 0

    def test_calls_and_latency_are_per_role(self):
        metrics_mod.record_llm_call(role="critic", model="m3", latency_ms=50, success=True)
        assert _counter(metrics_mod.llm_calls_total, model="m3", role="critic", status="success") >= 1
        assert _counter(metrics_mod.llm_calls_total, model="m3", role="executor", status="success") == 0

    def test_failure_recorded_as_error_status(self):
        metrics_mod.record_llm_call(role="executor", model="m4", latency_ms=10, success=False)
        assert _counter(metrics_mod.llm_calls_total, model="m4", role="executor", status="error") >= 1
