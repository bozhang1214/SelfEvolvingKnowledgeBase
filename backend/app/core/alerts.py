"""告警外发：把「任务失败」这类**用户能感知**的问题推到飞书。

复用既有的 `deploy/feishu-webhook` 网关（FastAPI，`POST /webhook`，接受 Alertmanager
格式载荷并渲染成飞书卡片）——它原本只被 Alertmanager 使用，这里让应用侧也能用，
避免再写一份飞书签名逻辑。

设计原则（很重要）：

- **绝不抛异常**：告警是旁路能力，发不出去不能影响主流程（生成报告本身）；
- **可配置且可关闭**：``NEWS_ALERT_WEBHOOK_URL`` 覆盖默认地址，设为空串即关闭；
- 载荷用 Alertmanager 形状（``alerts[].labels/annotations``），网关无需改动。
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 默认指向监控栈里的飞书网关（backend 与它同网，实测可解析并返回 200）
DEFAULT_ALERT_WEBHOOK_URL = "http://sekb-feishu-webhook:5001/webhook"
#: 告警发送超时（秒）—— 短超时，避免拖慢任务收尾
ALERT_TIMEOUT_S = 5.0


def alert_webhook_url() -> str:
    """当前生效的告警地址（``NEWS_ALERT_WEBHOOK_URL`` 优先，空串=关闭告警）。"""
    return os.environ.get("NEWS_ALERT_WEBHOOK_URL", DEFAULT_ALERT_WEBHOOK_URL).strip()


async def send_alert(
    title: str,
    detail: str = "",
    *,
    source: str = "sekb",
    severity: str = "error",
) -> bool:
    """把一条告警推到飞书网关。

    Args:
        title: 告警标题（飞书卡片里作为 alertname/summary 展示）。
        detail: 详细说明（失败原因等）。
        source: 来源标记，便于在卡片上区分模块（如 ``news``）。
        severity: Alertmanager 的 severity 标签（``error`` / ``warning`` / ``info``）。

    Returns:
        是否发送成功；**失败只记日志，绝不抛出**。
    """
    url = alert_webhook_url()
    if not url:
        logger.info("告警已关闭（NEWS_ALERT_WEBHOOK_URL 为空）", title=title)
        return False

    payload: dict[str, Any] = {
        "version": "4",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": title,
                    "severity": severity,
                    "source": source,
                    "service": "sekb",
                },
                "annotations": {
                    "summary": title,
                    "description": detail or title,
                },
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=ALERT_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            logger.warning("告警发送失败", status=resp.status_code, title=title)
            return False
        logger.info("告警已发送", title=title, source=source)
        return True
    except Exception as e:  # noqa: BLE001 - 旁路能力，任何异常都不能影响主流程
        logger.warning("告警发送异常", title=title, error=f"{type(e).__name__}: {str(e)[:120]}")
        return False
