"""
通用浏览器登录 / 采集服务（Playwright，async 版）。

对外提供：
- 通用 Cookie 存储（多站点）：导入 Cookie、列出站点、扫码登录
- 可插拔站点采集器：``SCRAPERS`` 注册表，按 site 名路由

注意：必须用 Playwright **async API**（FastAPI 请求跑在线程池里，
同步 Playwright 的 greenlet 会报「Cannot switch to a different thread」）。

Cookie 持久化在 ``/data/cookies.json``。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
COOKIES_FILE = DATA_DIR / "cookies.json"

app = FastAPI(title="sekb-browser", version="2.0.0")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

# 全局 async_playwright 实例（懒加载，进程内复用）
_pw = None
_pw_lock = asyncio.Lock()


async def _get_pw():
    global _pw
    async with _pw_lock:
        if _pw is None:
            from playwright.async_api import async_playwright
            _pw = await async_playwright().start()
        return _pw


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


# ---------------- 数据模型 ----------------

class ImportCookieReq(BaseModel):
    site: str = Field(..., description="站点标识，如 boss")
    cookie_header: str = Field(..., description="浏览器请求头里的 Cookie 串")


class LoginReq(BaseModel):
    site: str = Field(..., description="站点标识，如 boss")


class StatusLoginReq(BaseModel):
    site: str = Field(..., description="站点标识，如 boss")
    qr_id: str = Field(..., description="start 返回的 qr_id")


class ScrapeReq(BaseModel):
    site: str = Field(..., description="站点标识，如 boss")
    keyword: str = Field(..., description="搜索关键词")
    city: str = Field("", description="城市（站点相关，可为空）")
    page: int = Field(0, ge=0, description="页码，从 0 开始")
    limit: int = Field(20, ge=1, le=100, description="返回条数上限")


# ---------------- 通用 Cookie 接口 ----------------

@app.post("/cookies")
async def import_cookie(req: ImportCookieReq) -> dict[str, Any]:
    """导入某站点的登录 Cookie（持久化）。"""
    if not req.cookie_header.strip():
        raise HTTPException(400, "cookie_header 不能为空")
    data = _read_cookies()
    data[req.site] = {"cookie_header": req.cookie_header.strip(), "updated_at": _now()}
    _write_cookies(data)
    return {"site": req.site, "ok": True}


@app.get("/cookies")
async def list_cookies() -> dict[str, Any]:
    """列出已存储 Cookie 的站点（不含 Cookie 值）。"""
    data = _read_cookies()
    return {
        "sites": [{"site": k, "updated_at": v.get("updated_at")} for k, v in data.items()]
    }


# ---------------- 扫码登录（BOSS，纯 HTTP，APP 扫码流程） ----------------

# 进行中的扫码会话：qr_id -> 会话状态（含持有登录 Cookie 的 APIRequestContext）
_QR_SESSIONS: dict[str, dict[str, Any]] = {}

# BOSS「APP扫码登录」真实流程（逆向自 user-login chunk 的 BossAppScan 类）：
#  1. randkey -> qrId（二维码内容就是这个 qrId 字符串，前端/后端用 qrId 渲染标准二维码）
#  2. 用户用 BOSS App 扫第一张码
#  3. scan?uuid=qrId       长轮询 -> {scaned:true}
#  4. getSecondKey?uuid=qrId -> {qrId: secondUuid}（换第二张码）
#  5. 用户再扫第二张码
#  6. scanSecond?uuid=secondUuid 长轮询 -> {scaned:true}
#  7. scanLogin?qrId=qrId   -> {login:true} 表示在 App 上点了「确认登录」
#  8. dispatcher?qrId=qrId  -> {code:0}，并在响应里 Set-Cookie
# 注意：不能用 getMpCode/scanByMp（那是微信小程序码，BOSS App 扫不了）。
_QR_TTL = 30  # 第一张二维码有效期（秒），与前端 expireTime 一致


def _make_qr_png_data_url(content: str) -> str:
    """把字符串渲染成标准二维码 PNG，返回 data URL（BOSS App 可直接扫）。"""
    import base64
    import io

    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


async def _boss_qr_start(pw) -> dict[str, Any]:
    ctx = await pw.request.new_context(
        base_url="https://login.zhipin.com",
        user_agent=_UA,
        extra_http_headers={"Referer": "https://login.zhipin.com/"},
    )
    await ctx.get("/")
    r1 = await ctx.post("/wapi/zppassport/captcha/randkey")
    d1 = await r1.json()
    zp = d1.get("zpData") or {}
    qr_id = zp.get("qrId")
    if not qr_id:
        await ctx.dispose()
        raise HTTPException(500, f"randkey 返回异常: {await r1.text()}")
    _QR_SESSIONS[qr_id] = {
        "ctx": ctx,
        "qr_id": qr_id,
        "second_uuid": "",
        "phase": "waiting_scan",
        "created": time.time(),
    }
    return {
        "qr_id": qr_id,
        "qr_image_url": _make_qr_png_data_url(qr_id),
        "phase": "waiting_scan",
    }


def _log(msg: str) -> None:
    print(f"[boss-qr] {msg}", flush=True)


async def _boss_qr_status(pw, qr_id: str) -> dict[str, Any]:
    """推进扫码状态机一步（非阻塞轮询，2.5s 内返回）。"""
    sess = _QR_SESSIONS.get(qr_id)
    if sess is None:
        raise HTTPException(404, "二维码会话不存在或已过期，请重新获取")
    ctx = sess["ctx"]
    phase = sess["phase"]

    if phase == "waiting_scan":
        if time.time() - sess["created"] > _QR_TTL:
            _log(f"{qr_id} 第一张码过期")
            return {"phase": "expired"}
        try:
            r = await ctx.get(
                "/wapi/zppassport/qrcode/scan", params={"uuid": qr_id}, timeout=2500
            )
            d = await r.json()
        except Exception:  # noqa: BLE001  超时=尚未扫码
            return {"phase": "waiting_scan"}
        if not d.get("scaned"):
            return {"phase": "waiting_scan"}
        _log(f"{qr_id} 第一张码已扫 -> 请求 getSecondKey")
        r2 = await ctx.get("/wapi/zppassport/captcha/getSecondKey", params={"uuid": qr_id})
        d2 = await r2.json()
        _log(f"{qr_id} getSecondKey 响应: {json.dumps(d2, ensure_ascii=False)[:200]}")
        second = ((d2.get("zpData") or {}).get("qrId")) or ""
        if not second:
            return {"phase": "waiting_scan"}
        sess["second_uuid"] = second
        sess["phase"] = "waiting_second_scan"
        _log(f"{qr_id} 换第二张码 second_uuid={second}")
        return {"phase": "waiting_second_scan", "qr_image_url": _make_qr_png_data_url(second)}

    if phase == "waiting_second_scan":
        try:
            r = await ctx.get(
                "/wapi/zppassport/qrcode/scanSecond",
                params={"uuid": sess["second_uuid"]},
                timeout=2500,
            )
            d = await r.json()
        except Exception:  # noqa: BLE001
            return {"phase": "waiting_second_scan"}
        if d.get("scaned"):
            _log(f"{qr_id} 第二张码已扫 -> waiting_confirm")
            sess["phase"] = "waiting_confirm"
            return {"phase": "waiting_confirm"}
        return {"phase": "waiting_second_scan"}

    if phase == "waiting_confirm":
        r = await ctx.get("/wapi/zppassport/qrcode/scanLogin", params={"qrId": qr_id})
        d = await r.json()
        _log(f"{qr_id} scanLogin 响应: {json.dumps(d, ensure_ascii=False)[:200]}")
        if d.get("login"):
            _log(f"{qr_id} App 已确认 -> dispatcher")
            rd = await ctx.get("/wapi/zppassport/qrcode/dispatcher", params={"qrId": qr_id})
            dd = await rd.json()
            _log(f"{qr_id} dispatcher 响应: {json.dumps(dd, ensure_ascii=False)[:200]}")
            if dd.get("code") == 0:
                state = await ctx.storage_state()
                cookies = state.get("cookies", []) if isinstance(state, dict) else []
                names = [c.get("name") for c in cookies]
                _log(f"{qr_id} 登录成功，Cookie 数={len(cookies)} names={names}")
                cookie_header = "; ".join(
                    f"{c.get('name')}={c.get('value')}" for c in cookies
                )
                await ctx.dispose()
                _QR_SESSIONS.pop(qr_id, None)
                return {"phase": "success", "ok": True, "cookie_header": cookie_header}
            return {
                "phase": "login_failed",
                "message": dd.get("message") or f"登录失败(code {dd.get('code')})",
            }
        return {"phase": "waiting_confirm"}

    if phase == "success":
        return {"phase": "success", "ok": True}

    return {"phase": phase}


@app.post("/login/qr/start")
async def start_qr_login(req: LoginReq) -> dict[str, Any]:
    """启动 BOSS 扫码登录，返回第一张二维码（内容=qrId）+ qr_id。"""
    if req.site != "boss":
        raise HTTPException(404, f"站点 {req.site} 未注册扫码登录")
    try:
        return await _boss_qr_start(await _get_pw())
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"扫码登录启动失败: {e}")


@app.post("/login/qr/status")
async def status_qr_login(req: StatusLoginReq) -> dict[str, Any]:
    """轮询扫码状态机，返回当前 phase（及第二张码 / 登录 Cookie）。"""
    if req.site != "boss":
        raise HTTPException(404, f"站点 {req.site} 未注册扫码登录")
    try:
        result = await _boss_qr_status(await _get_pw(), req.qr_id)
        # 登录成功：把 Cookie 持久化，供后续采集使用
        if result.get("phase") == "success" and result.get("cookie_header"):
            data = _read_cookies()
            data[req.site] = {
                "cookie_header": result["cookie_header"],
                "updated_at": _now(),
            }
            _write_cookies(data)
            _log(f"Cookie 已持久化到 /data/cookies.json site={req.site}")
        return result
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"扫码状态查询失败: {e}")


# ---------------- 可插拔采集器 ----------------

SCRAPERS: dict[str, Any] = {}


def _register_scraper(site: str):
    def deco(fn):
        SCRAPERS[site] = fn
        return fn
    return deco


@_register_scraper("boss")
async def _scrape_boss(pw, cookie_header: str, params: dict) -> list[dict]:
    """BOSS 直聘采集：用 Cookie 打开搜索页并抽取职位。"""
    keyword = params.get("keyword", "")
    city = params.get("city", "")
    limit = params.get("limit", 20)
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(locale="zh-CN", user_agent=_UA)
    if cookie_header:
        await context.add_cookies(_parse_cookie_header(cookie_header, "https://www.zhipin.com"))
    page = await context.new_page()
    query = keyword if city in ("", "全国") else f"{keyword} {city}"
    url = f"https://www.zhipin.com/web/geek/jobs?query={query}&city=101010100"
    try:
        await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        cards = await page.query_selector_all(".job-card-wrapper, .job-list-box li, .job-card")
        jobs: list[dict] = []
        for card in cards[:limit]:
            title_el = await card.query_selector(".job-name, .job-title, a[ka='job-detail']")
            company_el = await card.query_selector(".company-name, .boss-name, .company-text")
            salary_el = await card.query_selector(".salary, .job-salary")
            area_el = await card.query_selector(".job-area, .job-area-wrapper")
            title = (await title_el.inner_text()).strip() if title_el else ""
            if not title:
                continue
            jobs.append({
                "job_id": "",
                "title": title,
                "company": (await company_el.inner_text()).strip() if company_el else "",
                "salary": (await salary_el.inner_text()).strip() if salary_el else "",
                "city": (await area_el.inner_text()).strip() if area_el else "",
                "job_url": url,
                "jd_text": "",
                "source": "BOSS直聘",
            })
        return jobs
    finally:
        await browser.close()


def _parse_cookie_header(cookie_header: str, url: str) -> list[dict]:
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    cookies = []
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies.append({"name": name.strip(), "value": value.strip(), "domain": domain, "path": "/"})
    return cookies


@app.post("/scrape")
async def scrape(req: ScrapeReq) -> dict[str, Any]:
    """按站点采集职位。"""
    scraper = SCRAPERS.get(req.site)
    if scraper is None:
        raise HTTPException(404, f"站点 {req.site} 未注册采集器")
    cookie_header = ""
    data = _read_cookies()
    if req.site in data:
        cookie_header = data[req.site].get("cookie_header", "")
    params = {"keyword": req.keyword, "city": req.city, "page": req.page, "limit": req.limit}
    try:
        jobs = await scraper(await _get_pw(), cookie_header, params)
        return {"site": req.site, "count": len(jobs), "jobs": jobs}
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"采集失败: {e}")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
