"""
飞书 Webhook 中转服务（Phase 4 P0-6 告警通知）。

将 Alertmanager 的 webhook 转换为飞书互动卡片消息格式。

部署方式：
    通过 docker-compose.monitoring.yml 中的 feishu-webhook 服务启动

环境变量：
    FEISHU_WEBHOOK_URL  飞书机器人 webhook 地址
    FEISHU_SECRET        飞书机器人签名密钥（可选）
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


def _build_action_buttons() -> list[dict]:
    """构建操作按钮（跳转链接，未配置 URL 则不显示）。"""
    buttons = []
    if GRAFANA_URL:
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看监控指标"},
            "type": "primary",
            "url": GRAFANA_URL,
        })
    if FRONTEND_URL:
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看对话记录"},
            "type": "default",
            "url": FRONTEND_URL,
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

        content = (
            f"**{status_icon} {alertname}**\n"
            f"状态: {status_text} | 级别: {severity} | 服务: {service}\n"
            f"摘要: {annotations.get('summary', '无')}\n"
            f"详情: {annotations.get('description', '无')}\n"
            f"开始: {starts_at}"
            + (f"\n恢复: {ends_at}" if status == "resolved" else "")
        )
        # 补充直白解释 + 建议操作
        guide = _ALERT_GUIDE.get(alertname)
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

    # 操作按钮（跳转链接，未配置则不显示）
    buttons = _build_action_buttons()
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
