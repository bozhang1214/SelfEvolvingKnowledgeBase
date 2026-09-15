"""打通「AI 对话 → 职位分析」：Executor 的 search_jobs 工具。

验证三点：
1. 调度：`_dispatch_tool("search_jobs", ...)` 会路由到 `_search_jobs` 并带上 user_id；
2. 编排：`_search_jobs` 用对话里的 keyword/city/min_salary_k 调用同一套
   `market.analyze_market`（与「招聘分析」页面共用流水线/缓存/画像）；
3. 格式化：把可能很大的报告压成 Executor 能引用的紧凑摘要（截断正文、只给代表职位）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.executor import ExecutorAgent, _format_market_report


def _agent() -> ExecutorAgent:
    # 只测工具编排与格式化，不跑 __init__（它们不依赖 llm_factory/config）
    agent = ExecutorAgent.__new__(ExecutorAgent)
    # 兜底分支会用到 self.logger.warning，给个 no-op 避免 AttributeError
    agent.logger = SimpleNamespace(warning=lambda *a, **kw: None, info=lambda *a, **kw: None)
    return agent


def _fake_ctx(default_keyword: str = "Java", default_city: str = "全国",
              default_min_salary_k: int = 0) -> SimpleNamespace:
    job_cfg = SimpleNamespace(
        default_keyword=default_keyword,
        default_city=default_city,
        default_min_salary_k=default_min_salary_k,
    )
    return SimpleNamespace(config=SimpleNamespace(job=job_cfg), llm_factory=object())


SAMPLE_REPORT = {
    "keyword": "Python 后端",
    "city": "深圳",
    "job_count": 3,
    "stats": {
        "role_distribution": [{"name": "后端开发", "count": 3}],
        "company_distribution": [{"name": "某科技", "count": 2}],
        "hot_keywords": [{"keyword": "FastAPI", "count": 3}],
    },
    "market": {"结论": "深圳 Python 后端需求旺盛", "详情": "x" * 5000},  # 超长正文，须截断
    "knowledge_iteration": {"技能": "FastAPI、PostgreSQL"},
    "jobs": [
        {"title": "Python 后端", "company": "某科技", "salary": "25-40k",
         "city": "深圳", "job_url": "https://example.com/j/1"},
        {"title": "后端工程师", "company": "B 公司", "salary": "20-35k",
         "city": "深圳", "job_url": ""},
    ],
}


# ---------- 格式化 ----------


def test_format_report_contains_key_facts() -> None:
    out = _format_market_report(SAMPLE_REPORT)
    assert "Python 后端" in out and "深圳" in out and "3 个职位" in out
    assert "后端开发×3" in out          # 角色分布
    assert "某科技×2" in out            # 公司分布
    assert "FastAPI" in out             # 热点关键词
    assert "Python 后端 | 某科技 | 25-40k | 深圳" in out  # 代表职位


def test_format_report_truncates_long_body() -> None:
    out = _format_market_report(SAMPLE_REPORT)
    # 正文里塞了 5000 个 x，摘要必须截断到预算内
    assert len(out) < 6000
    assert "x" * 5000 not in out


def test_format_report_handles_empty_report() -> None:
    out = _format_market_report({})
    assert "职位分析结果" in out
    assert "0 个职位" in out


# ---------- 编排 ----------


@pytest.mark.asyncio
async def test_search_jobs_calls_analyze_market(monkeypatch) -> None:
    calls: dict = {}

    async def fake_analyze_market(**kw):
        calls.update(kw)
        return {"report": SAMPLE_REPORT, "search_id": "s1"}

    monkeypatch.setattr("app.agents.job.market.analyze_market", fake_analyze_market)
    monkeypatch.setattr("app.agents.job.profile.load_user_profile", lambda uid: f"profile:{uid}")
    monkeypatch.setattr("app.core.bootstrap.get_app_context", lambda: _fake_ctx())

    out = await _agent()._search_jobs(
        {"keyword": "Go 后端", "city": "北京", "min_salary_k": 30}, user_id="u1"
    )
    # 返回值是「报告格式化后的摘要」，报告内容来自 fake_analyze_market 返回的 SAMPLE_REPORT
    assert "3 个职位" in out
    assert calls["keyword"] == "Go 后端"
    assert calls["city"] == "北京"
    assert calls["min_salary_k"] == 30
    assert calls["user_id"] == "u1"
    assert calls["user_profile"] == "profile:u1"
    assert calls["llm_factory"] is not None


@pytest.mark.asyncio
async def test_search_jobs_defaults_from_config(monkeypatch) -> None:
    captured: dict = {}

    async def fake_analyze_market(**kw):
        captured.update(kw)
        return {"report": {"keyword": "Java", "city": "全国", "job_count": 0}, "search_id": "s"}

    monkeypatch.setattr("app.agents.job.market.analyze_market", fake_analyze_market)
    monkeypatch.setattr("app.agents.job.profile.load_user_profile", lambda uid: "")
    monkeypatch.setattr(
        "app.core.bootstrap.get_app_context",
        lambda: _fake_ctx(default_keyword="Java", default_city="全国", default_min_salary_k=10),
    )

    await _agent()._search_jobs({}, user_id="u2")
    assert captured["keyword"] == "Java"
    assert captured["city"] == "全国"
    assert captured["min_salary_k"] == 10


@pytest.mark.asyncio
async def test_search_jobs_failure_raises_tool_error(monkeypatch) -> None:
    async def fake_analyze_market(**kw):
        raise RuntimeError("职位源不可达")

    monkeypatch.setattr("app.agents.job.market.analyze_market", fake_analyze_market)
    monkeypatch.setattr("app.agents.job.profile.load_user_profile", lambda uid: "")
    monkeypatch.setattr("app.core.bootstrap.get_app_context", lambda: _fake_ctx())

    from app.core.exceptions import ToolError

    with pytest.raises(ToolError, match="职位搜索分析失败"):
        await _agent()._search_jobs({"keyword": "x"}, user_id="u3")


@pytest.mark.asyncio
async def test_dispatch_routes_search_jobs(monkeypatch) -> None:
    agent = _agent()
    seen: dict = {}

    async def fake_search_jobs(tool_input, user_id):
        seen["tool_input"] = tool_input
        seen["user_id"] = user_id
        return "ok"

    monkeypatch.setattr(agent, "_search_jobs", fake_search_jobs)
    out = await agent._dispatch_tool(
        "search_jobs", {"keyword": "算法"}, "帮我找算法岗", user_id="u9"
    )
    assert out == "ok"
    assert seen == {"tool_input": {"keyword": "算法"}, "user_id": "u9"}


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_still_falls_back(monkeypatch) -> None:
    """新增工具不能影响「未知工具 → llm_generate」的既有兜底。"""
    agent = _agent()

    async def fake_llm_generate(query):
        return f"gen:{query}"

    monkeypatch.setattr(agent, "_llm_generate", fake_llm_generate)
    out = await agent._dispatch_tool("no_such_tool", {}, "hi", user_id="u")
    assert out == "gen:hi"
