"""
飞书 Webhook 中转服务（Phase 4 P0-6 告警通知）。

将 Alertmanager 的 webhook 转换为飞书互动卡片消息格式。

部署方式：
    通过 docker-compose.monitoring.yml 中的 feishu-webhook 服务启动

环境变量：
    FEISHU_WEBHOOK_URL  飞书机器人 webhook 地址
    FEISHU_SECRET        飞书机器人签名密钥（可选）
    PROMETHEUS_URL       告警源 Prometheus 地址（「查看告警」按钮，告警由 Prometheus 规则触发）
    GRAFANA_URL          Grafana 看板地址（「查看看板」按钮，纯可视化辅助）
    FRONTEND_URL         前端应用地址（「查看对话记录」按钮，深链定位会话）
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="SEKB Feishu Webhook Gateway")

FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")
FEISHU_SECRET = os.getenv("FEISHU_SECRET", "")
# 操作按钮跳转地址（未配置则不显示按钮）
# 告警源是 Prometheus（alerts.yml 规则触发），「查看告警」直达其 /alerts 页，保证「告警源→URL」一致
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "").rstrip("/")
# Grafana 是可视化辅助（看板 + 日志），仅作「查看看板」入口
GRAFANA_URL = os.getenv("GRAFANA_URL", "").rstrip("/")
FRONTEND_URL = os.getenv("FRONTEND_URL", "").rstrip("/")

# 告警类型的「人话解释」+ 建议操作（卡片里补充直白说明）
_ALERT_GUIDE = {
    "LowGroundedness": {
        "meaning": "AI 回答与真实检索内容的匹配度（锚定度）偏低，可能出现了不基于事实、编造的回答",
        "action": "查看最近对话，确认 AI 回答是否准确；若频繁出现，检查检索/知识库是否正常",
    },
    "LLMDegradationHigh": {
        "meaning": "LLM 频繁降级（从 reasoner 降到 chat），可能是 reasoner 模型异常或额度/限流",
        "action": "检查 LLM 日志与费用，确认 reasoner 模型是否可用",
    },
}

# 应用侧（非 Prometheus）告警：alertname 是动态中文标题，查不到上面那张表，
# 因此按 labels.source 给释义与入口，避免卡片只有一行「任务失败」看不出该怎么办。
_SOURCE_GUIDE = {
    "news": {
        "meaning": "科技资讯的日报/周报/月报生成失败（RSS 采集、正文抽取或大模型调用出错），报告没有产出或没有更新",
        "action": "打开「科技资讯」页面手动重新生成；若反复失败，查后端日志（关键字 news）确认是网络还是模型侧问题",
    },
}


def _build_action_buttons(conversation_id: str = "", *, source: str = "") -> list[dict]:
    """构建操作按钮（跳转链接，未配置 URL 则不显示）。

    顺序与主辅分工一致：Prometheus 告警（主）→ Grafana 看板（辅）→ 应用页面/对话定位。

    应用侧告警（如资讯生成失败）没有 conversation_id：此时给「查看资讯」这类
    真正的处置入口，而不是一个点了没用的「查看对话记录」。
    """
    buttons = []
    # 告警源是 Prometheus：主按钮直达告警页（/alerts 列出活跃告警与规则）
    if PROMETHEUS_URL:
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看告警"},
            "type": "primary",
            "url": PROMETHEUS_URL + "/alerts",
        })
    # Grafana 是可视化辅助：看板入口
    if GRAFANA_URL:
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看看板"},
            "type": "default",
            "url": GRAFANA_URL,
        })
    if source == "news" and FRONTEND_URL:
        # FRONTEND_URL 已含 /sekb 子路径（如 https://bos-studio.tech/sekb）
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看资讯"},
            "type": "default",
            "url": FRONTEND_URL + "/news",
        })
    elif FRONTEND_URL:
        # FRONTEND_URL 已含 /sekb 子路径（如 https://bos-studio.tech/sekb）
        # 深链到 /chat，携带 conversation_id 时前端自动定位到对应会话
        chat_url = FRONTEND_URL + "/chat"
        if conversation_id:
            chat_url += f"?conversation_id={conversation_id}"
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看对话记录"},
            "type": "default",
            "url": chat_url,
        })
    return buttons


def _gen_sign(timestamp: int, secret: str) -> str:
    """生成飞书机器人签名。"""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    import base64

    return base64.b64encode(hmac_code).decode("utf-8")


def _build_card(alerts: list[dict]) -> dict:
    """
    根据告警列表构建飞书互动卡片消息。

    Args:
        alerts: Alertmanager 推送的 alerts 列表

    Returns:
        飞书卡片消息字典
    """
    # 判断整体严重级别
    has_critical = any(
        a.get("labels", {}).get("severity") == "critical" for a in alerts
    )
    header_template = "red" if has_critical else "orange"
    status_icon = "🔴" if has_critical else "🟡"

    # 构建告警元素
    elements = []
    for alert in alerts:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        status = alert.get("status", "firing")
        starts_at = alert.get("startsAt", "")[:19]
        ends_at = alert.get("endsAt", "")[:19]

        status_text = "✅ 已恢复" if status == "resolved" else "🔥 触发中"
        severity = labels.get("severity", "unknown")
        alertname = labels.get("alertname", "unknown")
        service = labels.get("service", "unknown")
        source = labels.get("source", "")

        content = (
            f"**{status_icon} {alertname}**\n"
            f"状态: {status_text} | 级别: {severity} | 服务: {service}"
            + (f" | 来源: {source}" if source else "") + "\n"
            f"摘要: {annotations.get('summary', '无')}\n"
            f"详情: {annotations.get('description', '无')}\n"
            f"开始: {starts_at}"
            + (f"\n恢复: {ends_at}" if status == "resolved" else "")
        )
        # 补充直白解释 + 建议操作（先按告警名，再退回按来源）
        guide = _ALERT_GUIDE.get(alertname) or _SOURCE_GUIDE.get(source)
        if guide:
            content += (
                f"\n\n💡 **这是什么**：{guide['meaning']}"
                f"\n👉 **建议**：{guide['action']}"
            )

        element = {
            "tag": "div",
            "text": {"tag": "lark_md", "content": content},
        }
        elements.append(element)
        elements.append({"tag": "hr"})

    # 移除最后一个 hr
    if elements and elements[-1].get("tag") == "hr":
        elements.pop()

    # 提取 conversation_id（用于「查看对话记录」深链，多条告警取第一个有效的）
    conversation_id = ""
    source = ""
    for alert in alerts:
        labels = alert.get("labels", {})
        cid = labels.get("conversation_id", "")
        if cid and not conversation_id:
            conversation_id = cid
        src = labels.get("source", "")
        if src and not source:
            source = src

    # 操作按钮（跳转链接，未配置则不显示）
    buttons = _build_action_buttons(conversation_id, source=source)
    if buttons:
        elements.append({"tag": "hr"})
        elements.append({"tag": "action", "actions": buttons})

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"SEKB 告警通知（{len(alerts)} 条）",
                },
                "template": header_template,
            },
            "elements": elements,
        },
    }


async def _send_to_feishu(card: dict) -> dict:
    """发送卡片消息到飞书。"""
    if not FEISHU_WEBHOOK_URL:
        return {"status": "skipped", "reason": "FEISHU_WEBHOOK_URL not configured"}

    payload: dict[str, Any] = dict(card)

    # 签名
    if FEISHU_SECRET:
        timestamp = int(time.time())
        payload["timestamp"] = str(timestamp)
        payload["sign"] = _gen_sign(timestamp, FEISHU_SECRET)

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(FEISHU_WEBHOOK_URL, json=payload)
        return {
            "status": "sent" if resp.status_code == 200 else "failed",
            "status_code": resp.status_code,
            "response": resp.text[:200],
        }


@app.post("/webhook")
async def alertmanager_webhook(request: Request) -> JSONResponse:
    """
    接收 Alertmanager webhook 推送并转发到飞书。

    Alertmanager 推送格式：
        {"version": "...", "alerts": [{...}, ...]}
    """
    try:
        body = await request.json()
        alerts = body.get("alerts", [])
        if not alerts:
            return JSONResponse({"status": "no alerts"})

        card = _build_card(alerts)
        result = await _send_to_feishu(card)

        return JSONResponse(
            {
                "status": "ok",
                "alerts_count": len(alerts),
                "feishu": result,
            }
        )
    except Exception as e:
        return JSONResponse(
            {"status": "error", "detail": str(e)}, status_code=500
        )


@app.post("/test")
async def send_test_alert(source: str = "news", severity: str = "critical") -> JSONResponse:
    """发一条**测试卡片**，验证「网关 → 飞书」链路（不经过 Alertmanager）。

    为什么要有这个端点：这条链路曾经「配置看着是好的、实际发不出去」（env-file 没传），
    只能在真出事时才发现。改完网关/飞书配置后先打一发，确认群里真的收到卡片。
    """
    card = _build_card([
        {
            "status": "firing",
            "labels": {
                "alertname": "告警链路测试",
                "severity": severity,
                "source": source,
                "service": "sekb",
            },
            "annotations": {
                "summary": "这是一条测试告警，收到即说明「SEKB → 飞书网关 → 飞书群」链路正常",
                "description": "忽略即可；无需任何处理。",
            },
        }
    ])
    result = await _send_to_feishu(card)
    return JSONResponse({"status": "ok", "source": source, "feishu": result})


@app.get("/health")
async def health() -> dict:
    """健康检查端点。"""
    return {
        "status": "ok",
        "feishu_configured": bool(FEISHU_WEBHOOK_URL),
    }


@app.get("/")
async def root() -> dict:
    """根端点，返回服务信息。"""
    return {
        "service": "SEKB Feishu Webhook Gateway",
        "endpoints": ["/webhook", "/health"],
        "feishu_configured": bool(FEISHU_WEBHOOK_URL),
    }
