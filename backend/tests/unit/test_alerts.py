"""应用侧告警外发（飞书）：载荷契约 + 去重 + 失败不炸主流程。

背景（真实踩坑）：提交信息里写着「资讯失败推飞书」，但 `send_alert` 只是被**定义**了，
`write_status` 里并没有调用 —— 死代码，真出事时一条告警都不会发。
所以这里既测 `send_alert` 本身，也测「失败路径确实调到了它」。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agents.news import service as news_service
from app.core import alerts


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每个用例都从干净的环境变量 + 去重记忆开始（避免用例相互污染）。"""
    monkeypatch.delenv("NEWS_ALERT_WEBHOOK_URL", raising=False)
    alerts.reset_dedup()
    yield
    alerts.reset_dedup()


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = '{"code":0}') -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """替掉 httpx.AsyncClient：记录 POST，并可按需抛异常。"""

    calls: list[dict] = []
    status_code = 200
    raises: Exception | None = None

    def __init__(self, *a, **kw) -> None:  # noqa: D107
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, url: str, json: dict | None = None):
        type(self).calls.append({"url": url, "payload": json})
        if type(self).raises is not None:
            raise type(self).raises
        return _FakeResponse(status_code=type(self).status_code)


@pytest.fixture
def fake_http(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.status_code = 200
    _FakeClient.raises = None
    monkeypatch.setattr(alerts.httpx, "AsyncClient", _FakeClient)
    return _FakeClient


# ---------- 地址解析 ----------


def test_default_url_points_to_gateway() -> None:
    """不配任何变量时，默认指向同网的飞书网关（告警默认是**开**的）。"""
    assert alerts.alert_webhook_url() == alerts.DEFAULT_ALERT_WEBHOOK_URL
    assert alerts.alert_webhook_url().endswith("/webhook")


def test_url_override(monkeypatch) -> None:
    monkeypatch.setenv("NEWS_ALERT_WEBHOOK_URL", "http://127.0.0.1:9999/webhook")
    assert alerts.alert_webhook_url() == "http://127.0.0.1:9999/webhook"


@pytest.mark.parametrize("value", ["off", "OFF", "none", "0", "disabled", "no", " false "])
def test_url_can_be_disabled(monkeypatch, value: str) -> None:
    """compose 里用 ``${VAR:-默认}``：空串会退回默认值，所以关闭要用明确的开关字。"""
    monkeypatch.setenv("NEWS_ALERT_WEBHOOK_URL", value)
    assert alerts.alert_webhook_url() == ""


# ---------- 载荷契约（网关按这些字段渲染卡片）----------


@pytest.mark.asyncio
async def test_payload_matches_gateway_contract(fake_http) -> None:
    ok = await alerts.send_alert("科技资讯日报生成失败", "类型: daily｜原因: SSLError", source="news")
    assert ok is True
    assert len(fake_http.calls) == 1
    body = fake_http.calls[0]["payload"]
    alert = body["alerts"][0]
    # 网关读的字段：alertname / severity / source / service + annotations
    assert alert["labels"]["alertname"] == "科技资讯日报生成失败"
    assert alert["labels"]["severity"] == "critical"
    assert alert["labels"]["source"] == "news"
    assert alert["labels"]["service"] == "sekb"
    assert alert["annotations"]["summary"] == "科技资讯日报生成失败"
    assert "SSLError" in alert["annotations"]["description"]
    assert alert["status"] == "firing" and body["status"] == "firing"
    # 必须是可 JSON 序列化的纯 dict（网关 request.json() 直接读）
    json.dumps(body, ensure_ascii=False)


@pytest.mark.asyncio
async def test_http_error_returns_false_without_raising(fake_http) -> None:
    fake_http.status_code = 500
    assert await alerts.send_alert("x", source="news") is False


@pytest.mark.asyncio
async def test_network_exception_never_propagates(fake_http) -> None:
    """告警是旁路能力：DNS/连接失败绝不能影响生成报告的主流程。"""
    fake_http.raises = ConnectionError("Name or service not known")
    assert await alerts.send_alert("x", source="news") is False


@pytest.mark.asyncio
async def test_disabled_sends_nothing(monkeypatch, fake_http) -> None:
    monkeypatch.setenv("NEWS_ALERT_WEBHOOK_URL", "off")
    assert await alerts.send_alert("x", source="news") is False
    assert fake_http.calls == []


# ---------- 去重（调度器会重试，重试必然重复报同一个错）----------


@pytest.mark.asyncio
async def test_duplicate_failure_deduped(fake_http) -> None:
    first = await alerts.send_alert("科技资讯日报生成失败", "原因: SSLError", source="news")
    second = await alerts.send_alert("科技资讯日报生成失败", "原因: SSLError", source="news")
    assert (first, second) == (True, False)
    assert len(fake_http.calls) == 1, "同一故障在窗口内只应推一次"


@pytest.mark.asyncio
async def test_different_failure_not_deduped(fake_http) -> None:
    await alerts.send_alert("科技资讯日报生成失败", "原因: SSLError", source="news")
    assert await alerts.send_alert("科技资讯周报生成失败", "原因: SSLError", source="news") is True
    assert len(fake_http.calls) == 2


@pytest.mark.asyncio
async def test_dedup_window_expires(monkeypatch, fake_http) -> None:
    await alerts.send_alert("t", "d", source="news")
    monkeypatch.setattr(alerts, "DEDUP_WINDOW_S", 0.0)
    assert await alerts.send_alert("t", "d", source="news") is True


# ---------- write_status 必须真的调用告警（曾经是死代码）----------


def _bare_agent(tmp_path):
    """不跑 __init__（避免拉起 fetcher/LLM），只给 write_status 需要的存储目录。"""
    agent = news_service.NewsAgent.__new__(news_service.NewsAgent)
    d = tmp_path / "news"
    d.mkdir(parents=True, exist_ok=True)
    agent._storage = SimpleNamespace(_dir=d)
    return agent, d


@pytest.mark.asyncio
async def test_write_status_failure_fires_alert(tmp_path, monkeypatch) -> None:
    sent: list[tuple] = []

    async def fake_send(title, detail="", **kw):
        sent.append((title, detail, kw))
        return True

    monkeypatch.setattr(news_service, "send_alert", fake_send)
    agent, d = _bare_agent(tmp_path)
    agent.write_status("daily", ok=False, period="2026-09-15", error="SSLError: boom")
    await asyncio.sleep(0.05)  # fire-and-forget：让后台任务跑起来

    assert (d / "last_status.json").exists(), "状态仍要落盘"
    assert sent, "失败必须推告警（否则又是静默失败）"
    title, detail, kw = sent[0]
    assert title == "科技资讯日报生成失败"
    assert kw["source"] == "news" and kw["severity"] == "critical"
    assert "SSLError" in detail and "daily" in detail


@pytest.mark.asyncio
async def test_write_status_success_does_not_alert(tmp_path, monkeypatch) -> None:
    sent: list[tuple] = []

    async def fake_send(title, detail="", **kw):
        sent.append((title, detail, kw))
        return True

    monkeypatch.setattr(news_service, "send_alert", fake_send)
    agent, _ = _bare_agent(tmp_path)
    agent.write_status("weekly", ok=True, period="2026-09-07")
    await asyncio.sleep(0.05)
    assert sent == []


@pytest.mark.asyncio
async def test_write_status_alert_uses_kind_label(tmp_path, monkeypatch) -> None:
    sent: list[str] = []

    async def fake_send(title, detail="", **kw):
        sent.append(title)
        return True

    monkeypatch.setattr(news_service, "send_alert", fake_send)
    agent, _ = _bare_agent(tmp_path)
    agent.write_status("monthly", ok=False, error="boom")
    await asyncio.sleep(0.05)
    assert sent == ["科技资讯月报生成失败"]


def test_fire_and_forget_without_loop_only_logs() -> None:
    """同步上下文（没有事件循环）不应抛异常，也不应留下未 await 的告警协程。"""

    async def never_called():  # pragma: no cover - 不应被调用
        raise AssertionError("没有事件循环时不应执行")

    alerts.fire_and_forget(never_called())  # 不抛异常即通过
