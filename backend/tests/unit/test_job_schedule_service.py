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

    def test_ai_exempt_any_skips_ai_requirement(self):
        """规则①：FDE / 前沿部署 这类词本身即目标信号，不应因为没有「AI」字样被误杀。

        回归背景（2026-10-12）：一刀切 require_ai 会让标题为「FDE 工程师」、
        JD 正文也没写 AI 的正确岗位被剔除 —— 而它恰恰是最正的 FDE 岗。
        """
        layer = _layer(
            include_any=["FDE", "前沿部署", "交付"],
            require_ai=True,
            ai_exempt_any=["FDE", "前沿部署"],
        )
        jobs = [
            {"title": "FDE 工程师", "jd_text": "负责客户现场交付"},          # 免除 → 保留
            {"title": "前沿部署工程师", "jd_text": "驻场实施"},              # 免除 → 保留
            {"title": "交付工程师", "jd_text": "传统 IT 交付"},              # 未免除且无 AI → 剔除
            {"title": "交付工程师", "jd_text": "负责 Agent 落地"},           # 未免除但有 AI → 保留
        ]
        kept, dropped = svc.filter_layer_jobs(jobs, layer)
        assert [j["title"] for j in kept] == ["FDE 工程师", "前沿部署工程师", "交付工程师"]
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

    def test_exclude_matches_company_name_too(self):
        """回归：噪声常只出现在**公司名**上——「前沿部署工程师 FDE」的公司在「北京加拿大国际学校」，
        只匹配标题的 exclude 会完全失效。"""
        layer = _layer(exclude=["学校", "半导体"])
        jobs = [
            {"title": "前沿部署工程师 FDE", "company": "北京加拿大国际学校"},
            {"title": "FDE前沿部署工程师", "company": "芜湖鑫跃微半导体有限公司"},
            {"title": "FDE 工程师", "company": "正常科技有限公司"},
        ]
        kept, dropped = svc.filter_layer_jobs(jobs, layer)
        assert [j["company"] for j in kept] == ["正常科技有限公司"]
        assert dropped == {"命中 exclude": 2}

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
    async def test_saturate_fetches_second_page_for_high_yield_keyword(self, monkeypatch):
        """高产词（≥阈值）补采第 2 页 —— 2026-09-23 扩搜里"最大的解锁点"。"""
        calls: list[tuple[str, int]] = []

        class _FakeCollector:
            def __init__(self, **kw: Any) -> None:
                pass

            async def fetch_all(self, keyword: str, page: int = 0, limit: int = 40):
                calls.append((keyword, page))
                if page == 0:
                    return {"count": 60, "jobs": [{"title": "FDE 岗A"}], "sources": {}}
                return {"count": 25, "jobs": [{"title": "FDE 岗B"}], "sources": {}}

        async def fake_analyze(**kw: Any) -> dict[str, Any]:
            return {"cached": False, "search_id": kw["search_id"],
                    "report": {"keyword": kw["keyword"], "job_count": len(kw["jobs"])}}

        monkeypatch.setattr(svc, "JobCollector", _FakeCollector)
        monkeypatch.setattr(svc, "save_cached_jobs", lambda k, j: None)
        monkeypatch.setattr(svc, "load_user_profile", lambda uid: "p")
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

        layer = _layer(keywords=["大模型交付"], include_any=["FDE"], saturate=True,
                       saturate_threshold=35)
        out = await svc.refresh_one_layer(_Ctx(), "u1", layer)

        assert calls == [("大模型交付", 0), ("大模型交付", 1)]
        assert out["pages"] == 2
        assert out["collected"] == 85  # 60 + 25

    @pytest.mark.asyncio
    async def test_saturate_off_fetches_only_first_page(self, monkeypatch):
        calls: list[int] = []

        class _FakeCollector:
            def __init__(self, **kw: Any) -> None:
                pass

            async def fetch_all(self, keyword: str, page: int = 0, limit: int = 40):
                calls.append(page)
                return {"count": 60, "jobs": [{"title": "FDE 岗A"}], "sources": {}}

        async def fake_analyze(**kw: Any) -> dict[str, Any]:
            return {"cached": False, "search_id": kw["search_id"],
                    "report": {"keyword": kw["keyword"], "job_count": 1}}

        monkeypatch.setattr(svc, "JobCollector", _FakeCollector)
        monkeypatch.setattr(svc, "save_cached_jobs", lambda k, j: None)
        monkeypatch.setattr(svc, "load_user_profile", lambda uid: "p")
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

        layer = _layer(keywords=["大模型交付"], include_any=["FDE"], saturate=False)
        out = await svc.refresh_one_layer(_Ctx(), "u1", layer)
        assert calls == [0] and out["pages"] == 1

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

# ---------------- 去重（回归：不能用 job_url）----------------


class TestDedupe:
    """回归背景（2026-10-12）：原实现按 `job_url` 去重，但智联/猎聘的 URL 带搜索上下文，
    同一岗位在不同关键词下 URL 不同 —— 实测「纷美(北京)贸易 FDE工程师」在缓存里重复 11 次、
    151 条里大量是重复。改为「标题 + 公司」，与 2026-09-23 手工策展口径一致。
    """

    def test_same_job_with_different_urls_collapses(self):
        jobs = [
            {"title": "FDE工程师", "company": "纷美(北京)贸易有限公司",
             "job_url": "https://zhaopin.com/a?kw=FDE", "source": "智联招聘"},
            {"title": "FDE工程师", "company": "纷美(北京)贸易有限公司",
             "job_url": "https://zhaopin.com/b?kw=交付工程师", "source": "智联招聘"},
            {"title": "FDE工程师", "company": "纷美(北京)贸易有限公司",
             "job_url": "https://zhaopin.com/c?kw=落地", "source": "智联招聘"},
        ]
        assert len(svc.dedupe_jobs(jobs)) == 1

    def test_official_source_wins_on_conflict(self):
        """同一岗位同时出现在官方站与猎聘时，保留官方源（信息更准）。"""
        jobs = [
            {"title": "DuMate智能体前沿部署工程师FDE", "company": "百度", "source": "猎聘"},
            {"title": "DuMate智能体前沿部署工程师FDE", "company": "百度", "source": "百度"},
        ]
        out = svc.dedupe_jobs(jobs)
        assert len(out) == 1 and out[0]["source"] == "百度"

    def test_official_source_does_not_override_official(self):
        """两个都是官方源时保留先到的（不反复替换）。"""
        jobs = [
            {"title": "FDE", "company": "字节跳动", "source": "字节"},
            {"title": "FDE", "company": "字节跳动", "source": "阿里"},
        ]
        out = svc.dedupe_jobs(jobs)
        assert len(out) == 1 and out[0]["source"] == "字节"

    def test_different_titles_same_company_are_kept(self):
        jobs = [
            {"title": "FDE工程师（数智金融方向）", "company": "万联易达"},
            {"title": "FDE工程师（工业数据方向）", "company": "万联易达"},
        ]
        assert len(svc.dedupe_jobs(jobs)) == 2

    def test_title_normalization_ignores_punctuation_variants(self):
        """全角/半角括号与不同连字符应视为同一岗位。"""
        jobs = [
            {"title": "AI交付工程师（FDE‑工业算法方向）", "company": "X"},
            {"title": "AI交付工程师(FDE-工业算法方向)", "company": "X"},
        ]
        assert len(svc.dedupe_jobs(jobs)) == 1

    def test_anonymous_company_dedupes_by_title_only(self):
        """猎聘的「某北京互联网公司」无区分度 → 退化为只按标题判重（有意为之的取舍）。"""
        jobs = [
            {"title": "FDE专家", "company": "某北京互联网公司"},
            {"title": "FDE专家", "company": "某上海互联网公司"},
        ]
        assert len(svc.dedupe_jobs(jobs)) == 1

    def test_empty_title_is_dropped(self):
        assert svc.dedupe_jobs([{"title": "  ", "company": "X"}]) == []

    def test_job_identity_ignores_url_and_uses_company(self):
        a = svc.job_identity({"title": "FDE", "company": "A公司", "job_url": "u1"})
        b = svc.job_identity({"title": "FDE", "company": "A公司", "job_url": "u2"})
        c = svc.job_identity({"title": "FDE", "company": "B公司", "job_url": "u1"})
        assert a == b != c


class TestReuseMode:
    """`source_jobs` 复用模式：规则调整后重新筛选，但不重新抓取站点。"""

    @pytest.mark.asyncio
    async def test_reuse_skips_collection_entirely(self, monkeypatch):
        collect_calls: list[str] = []

        class _BoomCollector:
            def __init__(self, **kw: Any) -> None:
                pass

            async def fetch_all(self, keyword: str, page: int = 0, limit: int = 40):
                collect_calls.append(keyword)
                raise AssertionError("复用模式不应触发采集")

        saved: dict[str, Any] = {}

        async def fake_analyze(**kw: Any) -> dict[str, Any]:
            return {"cached": False, "search_id": kw["search_id"],
                    "report": {"keyword": kw["keyword"], "job_count": len(kw["jobs"])}}

        monkeypatch.setattr(svc, "JobCollector", _BoomCollector)
        monkeypatch.setattr(svc, "save_cached_jobs", lambda k, j: saved.update(key=k, jobs=j))
        monkeypatch.setattr(svc, "load_user_profile", lambda uid: "p")
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

        layer = _layer(include_any=["FDE"], exclude=["学校"])
        cached = [
            {"title": "FDE工程师", "company": "A公司", "job_url": "u1"},
            {"title": "FDE工程师", "company": "A公司", "job_url": "u2"},      # 重复 → 去重
            {"title": "FDE工程师", "company": "北京加拿大国际学校"},            # 公司命中新 exclude → 剔除
        ]
        out = await svc.refresh_one_layer(_Ctx(), "u1", layer, source_jobs=cached)

        assert collect_calls == []          # 关键：没有采集
        assert out["ok"] is True
        assert out["collected"] == 3        # 复用模式下的"输入量"
        assert out["kept"] == 1             # 去重 + 剔除后
        assert len(saved["jobs"]) == 1
