"""
通用浏览器登录 / 采集服务（Playwright）。

对外提供：
- 通用 Cookie 存储（多站点）：导入 Cookie、列出站点、扫码登录
- 可插拔站点采集器：``SCRAPERS`` 注册表，按 site 名路由

首个接入站点：BOSS 直聘（boss）。
其它需要登录的站点后续只需在 ``SCRAPERS`` / ``LOGIN_HANDLERS`` 注册即可复用
本服务的 Cookie 存储与扫码登录机制。

Cookie 持久化在 ``/data/cookies.json``，Playwright 浏览器登录态按站点存
``/data/state_{site}.json``（storage_state），实现免重复登录。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
COOKIES_FILE = DATA_DIR / "cookies.json"

app = FastAPI(title="sekb-browser", version="1.0.0")

# Playwright 单实例复用（启动较慢，避免每请求都启动）
_playwright = None
_pw_lock = threading.Lock()


def _get_pw():
    global _playwright
    with _pw_lock:
        if _playwright is None:
            _playwright = sync_playwright().start()
        return _playwright


# ---------------- 通用 Cookie 存储 ----------------

def _read_cookies() -> dict[str, dict[str, Any]]:
    if not COOKIES_FILE.exists():
        return {}
    try:
        return json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_cookies(data: dict[str, dict[str, Any]]) -> None:
    COOKIES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _state_path(site: str) -> Path:
    return DATA_DIR / f"state_{site}.json"


# ---------------- 数据模型 ----------------

class ImportCookieReq(BaseModel):
    site: str = Field(..., description="站点标识，如 boss")
    cookie_header: str = Field(..., description="浏览器请求头里的 Cookie 串")


class LoginReq(BaseModel):
    site: str = Field(..., description="站点标识，如 boss")


class CompleteLoginReq(BaseModel):
    site: str = Field(..., description="站点标识")
    timeout_seconds: int = Field(180, ge=10, le=600, description="等待扫码确认的秒数")


class ScrapeReq(BaseModel):
    site: str = Field(..., description="站点标识，如 boss")
    keyword: str = Field(..., description="搜索关键词")
    city: str = Field("", description="城市（站点相关，可为空）")
    page: int = Field(0, ge=0, description="页码，从 0 开始")
    limit: int = Field(20, ge=1, le=100, description="返回条数上限")


# ---------------- 通用 Cookie 接口 ----------------

@app.post("/cookies")
def import_cookie(req: ImportCookieReq) -> dict[str, Any]:
    """导入某站点的登录 Cookie（持久化）。"""
    if not req.cookie_header.strip():
        raise HTTPException(400, "cookie_header 不能为空")
    data = _read_cookies()
    data[req.site] = {"cookie_header": req.cookie_header.strip(), "updated_at": _now()}
    _write_cookies(data)
    return {"site": req.site, "ok": True}


@app.get("/cookies")
def list_cookies() -> dict[str, Any]:
    """列出已存储 Cookie 的站点（不含 Cookie 值）。"""
    data = _read_cookies()
    return {
        "sites": [
            {"site": k, "updated_at": v.get("updated_at")} for k, v in data.items()
        ]
    }


# ---------------- 扫码登录（通用，按站点注册） ----------------

# 各站点的扫码登录实现：返回 (start_fn, complete_fn)
# start_fn(pw) -> {"qr_image_base64": str}   生成二维码图片
# complete_fn(pw, timeout_seconds) -> bool   等待扫码完成并保存 storage_state
LOGIN_HANDLERS: dict[str, tuple] = {}


def _register_login(site: str):
    def deco(fn):
        LOGIN_HANDLERS[site] = fn
        return fn
    return deco


@_register_login("boss")
def _boss_login(pw, state_path: Path, action: str, timeout_seconds: int = 180):
    """BOSS 直聘扫码登录：action = start | complete。"""
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        locale="zh-CN",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    )
    page = context.new_page()
    page.goto("https://www.zhipin.com/web/user/?ka=header-login", timeout=60000)

    if action == "start":
        page.wait_for_selector(".login-qrcode img, img[class*=qr], img[src*='qrcode']", timeout=15000)
        qr_img = page.query_selector(".login-qrcode img, img[class*=qr], img[src*='qrcode']")
        if qr_img is None:
            context.close(); browser.close()
            raise HTTPException(500, "未找到登录二维码元素")
        qr_bytes = qr_img.screenshot()
        import base64
        b64 = base64.b64encode(qr_bytes).decode("utf-8")
        context.close(); browser.close()
        return {"qr_image_base64": b64}

    # complete：轮询等待登录成功（页面跳转离开登录页或出现登录态）
    import time
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if "login" not in page.url and page.query_selector(".user-nav, .user-name, a[href*='geek/web/chat']"):
            context.storage_state(path=str(state_path))
            context.close(); browser.close()
            return {"ok": True}
        time.sleep(3)
    context.close(); browser.close()
    return {"ok": False, "reason": "等待扫码超时"}


@app.post("/login/qr/start")
def start_qr_login(req: LoginReq) -> dict[str, Any]:
    """启动某站点的扫码登录，返回二维码图片（base64）。"""
    handler = LOGIN_HANDLERS.get(req.site)
    if handler is None:
        raise HTTPException(404, f"站点 {req.site} 未注册扫码登录")
    try:
        return handler(_get_pw(), _state_path(req.site), "start", 180)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"扫码登录启动失败: {e}")


@app.post("/login/qr/complete")
def complete_qr_login(req: CompleteLoginReq) -> dict[str, Any]:
    """等待用户扫码确认并保存登录态。"""
    handler = LOGIN_HANDLERS.get(req.site)
    if handler is None:
        raise HTTPException(404, f"站点 {req.site} 未注册扫码登录")
    try:
        return handler(_get_pw(), _state_path(req.site), "complete", req.timeout_seconds)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"扫码登录完成失败: {e}")


# ---------------- 可插拔采集器 ----------------

# 各站点的采集器：scrape(pw, state_path, cookie_header, params) -> list[dict]
SCRAPERS: dict[str, Any] = {}


def _register_scraper(site: str):
    def deco(fn):
        SCRAPERS[site] = fn
        return fn
    return deco


@_register_scraper("boss")
def _scrape_boss(pw, state_path: Path, cookie_header: str, params: dict) -> list[dict]:
    """BOSS 直聘采集：用登录态（storage_state 或 Cookie）打开搜索页并抽取职位。"""
    keyword = params.get("keyword", "")
    city = params.get("city", "")
    browser = pw.chromium.launch(headless=True)
    context_args: dict[str, Any] = {"locale": "zh-CN"}
    if state_path.exists():
        context_args["storage_state"] = str(state_path)
    context = browser.new_context(**context_args)
    if cookie_header and not state_path.exists():
        # 有 Cookie 无 storage_state：写入 Cookie 后使用
        from urllib.parse import urlparse
        cookies = _parse_cookie_header(cookie_header, "https://www.zhipin.com")
        context.add_cookies(cookies)
    page = context.new_page()
    query = keyword
    if city and city != "全国":
        query = f"{keyword} {city}"
    url = f"https://www.zhipin.com/web/geek/jobs?query={query}&city=101010100"
    page.goto(url, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)  # 等 SPA 渲染

    jobs: list[dict] = []
    cards = page.query_selector_all(".job-card-wrapper, .job-list-box li, .job-card")
    for card in cards[: params.get("limit", 20)]:
        title_el = card.query_selector(".job-name, .job-title, a[ka='job-detail']")
        company_el = card.query_selector(".company-name, .boss-name, .company-text")
        salary_el = card.query_selector(".salary, .job-salary")
        area_el = card.query_selector(".job-area, .job-area-wrapper")
        title = title_el.inner_text().strip() if title_el else ""
        if not title:
            continue
        jobs.append({
            "job_id": "",
            "title": title,
            "company": company_el.inner_text().strip() if company_el else "",
            "salary": salary_el.inner_text().strip() if salary_el else "",
            "city": area_el.inner_text().strip() if area_el else "",
            "job_url": page.url,
            "jd_text": "",
            "source": "BOSS直聘",
        })
    browser.close()
    return jobs


def _parse_cookie_header(cookie_header: str, url: str) -> list[dict]:
    """把 'k1=v1; k2=v2' 的 Cookie 串转成 Playwright add_cookies 格式。"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    domain = parsed.netloc
    cookies = []
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies.append({"name": name.strip(), "value": value.strip(), "domain": domain, "path": "/"})
    return cookies


@app.post("/scrape")
def scrape(req: ScrapeReq) -> dict[str, Any]:
    """按站点采集职位，返回归一化职位列表。"""
    scraper = SCRAPERS.get(req.site)
    if scraper is None:
        raise HTTPException(404, f"站点 {req.site} 未注册采集器")
    cookie_header = ""
    data = _read_cookies()
    if req.site in data:
        cookie_header = data[req.site].get("cookie_header", "")
    params = {"keyword": req.keyword, "city": req.city, "page": req.page, "limit": req.limit}
    try:
        jobs = scraper(_get_pw(), _state_path(req.site), cookie_header, params)
        return {"site": req.site, "count": len(jobs), "jobs": jobs}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"采集失败: {e}")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
