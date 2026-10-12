"""四层职位定时刷新服务单测（隔周判定 / 薪资解析 / 层筛选 / search_id 契约）。"""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.core.config import JobLayerConfig
from app.services import job_schedule_service as svc


def _layer(**kw: Any) -> JobLayerConfig:
    base: dict[str, Any] = {"label": "第X层·测试", "keywords": ["Agent"], "city": "北京"}
    base.update(kw)
    return JobLayerConfig(**base)


# ---------------- 隔周判定 ----------------


class TestIsRunWeek:
    """隔周语义 = 距锚点天数能被 14 整除（不用周序号：跨年会断）。"""

    ANCHOR = date(2026, 10, 12)  # 用户指定：今天执行一次，下下周一（10-26）再执行

    def test_anchor_week_runs(self):
        assert svc.is_run_week(self.ANCHOR, self.ANCHOR, biweekly=True) is True

    def test_next_week_skips(self):
        """下周一（10-19）应跳过 —— 这是"隔周"的核心断言。"""
        assert svc.is_run_week(date(2026, 10, 19), self.ANCHOR, biweekly=True) is False

    def test_two_weeks_later_runs(self):
        """下下周一（10-26）应执行。"""
        assert svc.is_run_week(date(2026, 10, 26), self.ANCHOR, biweekly=True) is True

    def test_four_weeks_later_runs(self):
        assert svc.is_run_week(date(2026, 11, 9), self.ANCHOR, biweekly=True) is True

    def test_biweekly_false_always_runs(self):
        for d in (date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26)):
            assert svc.is_run_week(d, self.ANCHOR, biweekly=False) is True

    def test_parity_is_stable_across_year_boundary(self):
        """跨年仍精确：锚点后 2 周命中、后 1 周跳过（用周序号会在这里出错）。"""
        anchor = date(2026, 12, 28)
        assert svc.is_run_week(date(2027, 1, 11), anchor, biweekly=True) is True
        assert svc.is_run_week(date(2027, 1, 4), anchor, biweekly=True) is False


# ---------------- 薪资上限解析 ----------------


class TestParseSalaryMaxK:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("35-55k·15薪", 55),
            ("40-60k·16薪", 60),
            ("2-3万", 30),          # 万 必须 ×10（旧 parse_min_salary 返回 2，是 bug）
            ("3-5万", 50),
            ("2.5-5万·15薪", 50),   # 小数万
            ("1.6-2.5万", 25),
            ("0.8-1.6万", 16),
            ("8000-16000元", 16),   # 元 必须 ÷1000（旧函数返回 8000）
            ("12000元", 12),
            ("50k", 50),
            ("面议", None),
            ("", None),
            ("**-**元", None),
        ],
    )
    def test_parse(self, raw: str, expected: int | None):
        assert svc.parse_salary_max_k(raw) == expected


# ---------------- 层筛选规则 ----------------


class TestFilterLayerJobs:
    def test_include_any_keeps_only_matching_titles(self):
        layer = _layer(include_any=["FDE", "交付"])
        jobs = [{"title": "FDE 工程师"}, {"title": "Java 后端"}, {"title": "交付专家"}]
        kept, dropped = svc.filter_layer_jobs(jobs, layer)
        assert [j["title"] for j in kept] == ["FDE 工程师", "交付专家"]
        assert dropped == {"不含 include_any": 1}

    def test_exclude_drops_matching_titles(self):
        layer = _layer(include_any=["交付"], exclude=["推理", "产品经理"])
        jobs = [{"title": "交付工程师"}, {"title": "推理部署交付"}, {"title": "产品经理交付"}]
        kept, dropped = svc.filter_layer_jobs(jobs, layer)
        assert [j["title"] for j in kept] == ["交付工程师"]
        assert dropped == {"命中 exclude": 2}

    def test_require_ai_checks_title_then_jd(self):
        layer = _layer(include_any=["交付"], require_ai=True)
        jobs = [
            {"title": "交付工程师（大模型方向）", "jd_text": ""},   # 标题含 AI 信号
            {"title": "交付工程师", "jd_text": "负责 Agent 与 RAG 落地"},  # JD 含
            {"title": "交付工程师", "jd_text": "传统 IT 项目交付"},        # 都不含
        ]
        kept, dropped = svc.filter_layer_jobs(jobs, layer)
        assert len(kept) == 2
        assert dropped == {"无 AI 信号": 1}

    def test_salary_floor_drops_low_and_keeps_negotiable(self):
        layer = _layer(salary_floor_k=25)
        jobs = [
            {"title": "高薪岗", "salary": "35-55k·15薪"},
            {"title": "低薪岗", "salary": "1.5-2万"},
            {"title": "面议岗", "salary": "面议"},      # 不可解析 → 保留（避免误杀大厂面议岗）
            {"title": "无薪资岗", "salary": ""},
        ]
        kept, dropped = svc.filter_layer_jobs(jobs, layer)
        assert [j["title"] for j in kept] == ["高薪岗", "面议岗", "无薪资岗"]
        assert dropped == {"薪资上限<25K": 1}

    def test_untitled_jobs_are_dropped(self):
        layer = _layer()
        kept, dropped = svc.filter_layer_jobs([{"title": "  "}, {"title": "正常岗"}], layer)
        assert [j["title"] for j in kept] == ["正常岗"]
        assert dropped == {"无标题": 1}

    def test_empty_rules_keep_everything(self):
        kept, dropped = svc.filter_layer_jobs([{"title": "任意岗"}], _layer())
        assert len(kept) == 1 and dropped == {}


# ---------------- search_id 契约（最关键：label 必须决定 search_id）----------------


class TestRefreshOneLayer:
    @pytest.mark.asyncio
    async def test_uses_label_as_cache_key_so_search_id_is_stable(self, monkeypatch, tmp_path):
        """采集用 keywords，落库用 label —— 这保证四层 Tab 的 search_id 不漂。"""
        from app.agents.job.job_cache import cache_key, make_search_id

        saved: dict[str, Any] = {}
        analyzed: dict[str, Any] = {}

        class _FakeCollector:
            def __init__(self, **kw: Any) -> None:
                pass

            async def fetch_all(self, keyword: str, page: int = 0, limit: int = 40):
                return {"count": 2, "jobs": [
                    {"title": "FDE 工程师", "salary": "35-55k", "job_url": "u1"},
                    {"title": "FDE 专家", "salary": "40-60k", "job_url": "u2"},
                ], "sources": {}}

        def fake_save(key: str, jobs: list[dict[str, Any]]) -> None:
            saved["key"] = key
            saved["jobs"] = jobs

        async def fake_analyze(**kw: Any) -> dict[str, Any]:
            analyzed.update(kw)
            return {"cached": False, "search_id": kw["search_id"],
                    "report": {"keyword": kw["keyword"], "job_count": len(kw["jobs"])}}

        monkeypatch.setattr(svc, "JobCollector", _FakeCollector)
        monkeypatch.setattr(svc, "save_cached_jobs", fake_save)
        monkeypatch.setattr(svc, "load_user_profile", lambda uid: "profile")
        import app.agents.job.archive as archive
        import app.agents.job.market as market
        monkeypatch.setattr(market, "analyze_market", fake_analyze)
        monkeypatch.setattr(market, "delete_report", lambda *a, **k: True)
        monkeypatch.setattr(archive, "save_report", lambda *a, **k: None)

        class _Ctx:
            class config:  # noqa: N801
                class job:  # noqa: N801
                    default_city = "北京"
                    exclude_companies: list[str] = []
            llm_factory = object()

        layer = _layer(label="第四层·企业级AI Agent交付(FDE)",
                       keywords=["FDE", "前沿部署"], include_any=["FDE", "前沿部署"])
        out = await svc.refresh_one_layer(_Ctx(), "u1", layer)

        expected_key = cache_key("u1", "第四层·企业级AI Agent交付(FDE)", "北京", 0)
        assert saved["key"] == expected_key
        assert out["search_id"] == make_search_id(expected_key)
        assert analyzed["keyword"] == "第四层·企业级AI Agent交付(FDE)"
        assert analyzed["search_id"] == out["search_id"]
        assert out["ok"] is True and out["job_count"] == 2

    @pytest.mark.asyncio
    async def test_missing_label_or_keywords_fails_fast(self):
        class _Ctx:
            class config:  # noqa: N801
                class job:  # noqa: N801
                    default_city = "北京"
                    exclude_companies: list[str] = []
            llm_factory = object()

        out = await svc.refresh_one_layer(_Ctx(), "u1", _layer(label="", keywords=[]))
        assert out["ok"] is False and "为空" in out["error"]


class TestRefreshAllLayers:
    @pytest.mark.asyncio
    async def test_one_layer_failure_does_not_block_others(self, monkeypatch):
        calls: list[str] = []

        async def fake_one(ctx: Any, user_id: str, layer: Any, **kw: Any) -> dict[str, Any]:
            calls.append(layer.label)
            if layer.label == "坏层":
                raise RuntimeError("boom")
            return {"label": layer.label, "ok": True}

        monkeypatch.setattr(svc, "refresh_one_layer", fake_one)
        layers = [_layer(label="好层1"), _layer(label="坏层"), _layer(label="好层2")]
        out = await svc.refresh_all_layers(object(), "u1", layers)

        assert calls == ["好层1", "坏层", "好层2"]  # 坏层之后的层仍然执行
        assert out["ok"] == 2 and out["layers"] == 3
        assert [r["ok"] for r in out["results"]] == [True, False, True]
