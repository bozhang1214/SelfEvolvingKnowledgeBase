"""
文章正文抽取模块。

流程：httpx 抓取原文 HTML → trafilatura 抽取正文纯文本。
任何一步失败都返回空串（调用方会降级使用 RSS 摘要），绝不抛异常中断流水线。
"""
from __future__ import annotations

import asyncio

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


async def fetch_article_text(url: str, timeout: int = 8) -> str:
    """抓取文章正文纯文本；失败返回空串。"""
    if not url:
        return ""
    html = await _fetch_html(url, timeout)
    if not html:
        return ""
    # trafilatura.extract 是 CPU 密集操作，放到线程池避免阻塞事件循环
    try:
        return await asyncio.to_thread(_extract_text, html)
    except Exception as e:  # noqa: BLE001
        logger.debug("正文抽取异常", url=url, error=str(e))
        return ""


async def _fetch_html(url: str, timeout: int) -> str:
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True
        ) as client:
            resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            return resp.text
    except Exception as e:  # noqa: BLE001
        logger.debug("文章抓取失败", url=url, error=str(e))
        return ""


def _extract_text(html: str) -> str:
    """用 trafilatura 抽取正文；未安装或抽取失败返回空串。"""
    try:
        import trafilatura  # 延迟导入，避免依赖缺失导致模块加载失败
    except ImportError:
        logger.debug("trafilatura 未安装，跳过正文抽取")
        return ""
    try:
        text = trafilatura.extract(
            html,
            include_links=False,
            include_images=False,
            include_tables=False,
            include_comments=False,
        )
        return (text or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("trafilatura 抽取失败", error=str(e))
        return ""
