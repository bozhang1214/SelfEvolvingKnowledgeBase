"""
六个「免登录明文 JSON」招聘采集源（统一归一化接口）。

字节 / 腾讯 / 百度 / 小米 / 阿里 / 小红书 六家官网职位列表接口的薄封装。
所有接口均已在抓取调研阶段用 curl 实测跑通，本文件直接采用实测通过时的
精确 headers + body，不做猜测。

通用约定：
- 每个采集源类提供 ``async def fetch(keyword, page=0, limit=20) -> list[dict]``，
  返回统一归一化的职位字典：:

      {
        "job_id": str, "title": str, "company": str, "salary": str, "city": str,
        "job_url": str, "jd_text": str, "source": str,
      }

- ``page`` 为 0 基页码（0 = 第一页），内部转换为各站自身的分页字段。
- 任一步失败（网络错误 / 非 200 / 结构不符 / 解析异常）都返回空列表并记录
  warning，绝不抛异常中断调用方。
- 薪资字段：这 6 家官网列表接口大多不返回薪资，``salary`` 通常为空字符串；
  需要薪资时由上层（filter_jobs 等）结合其它源（如猎聘）补齐。

采用 ``requests``（同步）在线程池中执行（与 fetcher.LiepinJobFetcher 一致）。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import requests

from app.core.logging import get_logger

logger = get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _join(*parts: Any) -> str:
    """拼接非空文本片段，用空格分隔（用于 jd_text）。"""
    return " ".join(str(p).strip() for p in parts if p and str(p).strip())


class _BaseSource:
    """采集源基类：提供异步包装 + 归一化统一入口。"""

    name: str = ""

    def __init__(self, timeout: int = 20) -> None:
        self._timeout = timeout

    async def fetch(
        self, keyword: str, page: int = 0, limit: int = 20
    ) -> list[dict[str, Any]]:
        """按关键词采集职位，返回归一化列表；失败返回空列表。"""
        if not keyword or not keyword.strip():
            return []
        try:
            jobs = await asyncio.to_thread(self._fetch_sync, keyword.strip(), page, limit)
            for j in jobs:
                j["source"] = self.name
            logger.info(
                "职位采集成功", source=self.name, keyword=keyword, count=len(jobs)
            )
            return jobs
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "职位采集失败", source=self.name, keyword=keyword, error=str(e)[:200]
            )
            return []

    # 子类实现
    def _fetch_sync(self, keyword: str, page: int, limit: int) -> list[dict[str, Any]]:
        raise NotImplementedError


class BytedanceSource(_BaseSource):
    """字节跳动社招（飞书招聘 ATSX）。北京 CT_11。"""

    name = "字节"

    _API = "https://jobs.bytedance.com/api/v1/search/job/posts"

    def _fetch_sync(self, keyword: str, page: int, limit: int) -> list[dict[str, Any]]:
        headers = {
            "User-Agent": _UA,
            "Content-Type": "application/json",
            "portal-channel": "society",  # 社招
            "portal-platform": "pc",
            "website-path": "society",
            "Origin": "https://jobs.bytedance.com",
            "Referer": "https://jobs.bytedance.com/experienced/position",
        }
        body = {
            "keyword": keyword,
            "limit": max(1, min(100, limit)),
            "offset": page * max(1, min(100, limit)),
            "portal_type": 3,
            "portal_entrance": 1,
            "language": "zh",
            "recruitment_id_list": ["101"],  # 101 = 社招正式
            "location_code_list": ["CT_11"],  # CT_11 = 北京
        }
        resp = requests.post(self._API, headers=headers, json=body, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(
                "字节接口返回异常", code=data.get("code"), message=data.get("message")
            )
            return []
        items = (data.get("data") or {}).get("job_post_list") or []
        return [self._normalize(it) for it in items if isinstance(it, dict)]

    @staticmethod
    def _normalize(it: dict[str, Any]) -> dict[str, Any]:
        pid = str(it.get("id") or "")
        city = ""
        ci = it.get("city_info") or {}
        if ci.get("name"):
            city = str(ci["name"])
        else:
            cl = it.get("city_list") or []
            city = " / ".join(
                str(c.get("name")) for c in cl if isinstance(c, dict) and c.get("name")
            )
        return {
            "job_id": pid,
            "title": it.get("title") or "",
            "company": "字节跳动",
            "salary": "",
            "city": city,
            "job_url": (
                f"https://jobs.bytedance.com/experienced/position/{pid}/detail"
                if pid
                else ""
            ),
            "jd_text": _join(it.get("description"), it.get("requirement")),
        }


class TencentSource(_BaseSource):
    """腾讯社招。GET Query（注意接口名是 Query，不是 QueryPost）。"""

    name = "腾讯"

    _API = "https://careers.tencent.com/tencentcareer/api/post/Query"

    def _fetch_sync(self, keyword: str, page: int, limit: int) -> list[dict[str, Any]]:
        params = {
            "timestamp": str(int(time.time() * 1000)),
            "keyword": keyword,
            "pageIndex": page + 1,  # 1 基
            "pageSize": max(1, min(100, limit)),
            "language": "zh-cn",
            "area": "cn",
        }
        url = f"{self._API}?{urlencode(params)}"
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("Code") != 200:
            logger.warning("腾讯接口返回异常", code=data.get("Code"))
            return []
        posts = (data.get("Data") or {}).get("Posts") or []
        return [self._normalize(p) for p in posts if isinstance(p, dict)]

    @staticmethod
    def _normalize(p: dict[str, Any]) -> dict[str, Any]:
        pid = str(p.get("PostId") or "")
        job_url = p.get("PostURL") or (
            f"https://careers.tencent.com/jobdesc.html?postId={pid}" if pid else ""
        )
        return {
            "job_id": pid,
            "title": p.get("RecruitPostName") or "",
            "company": "腾讯",
            "salary": "",
            "city": p.get("LocationName") or "",
            "job_url": job_url,
            "jd_text": p.get("Responsibility") or "",
        }


class BaiduSource(_BaseSource):
    """百度社招。表单编码 POST，必须带 Referer，分页字段 curPage，pageSize≤20。"""

    name = "百度"

    _API = "https://talent.baidu.com/httservice/getPostListNew"

    def _fetch_sync(self, keyword: str, page: int, limit: int) -> list[dict[str, Any]]:
        headers = {
            "User-Agent": _UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://talent.baidu.com/jobs/social-list",
            "Origin": "https://talent.baidu.com",
        }
        data = {
            "recruitType": "SOCIAL",
            "keyWord": keyword,
            "curPage": page + 1,  # 1 基；注意字段名是 curPage，不是 pageNum
            "pageSize": max(1, min(20, limit)),  # 硬上限 20
            "workPlace": "1100",  # 1100 = 北京市
        }
        resp = requests.post(self._API, headers=headers, data=data, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "ok":
            logger.warning(
                "百度接口返回异常", status=payload.get("status"), message=payload.get("message")
            )
            return []
        items = (payload.get("data") or {}).get("list") or []
        return [self._normalize(it) for it in items if isinstance(it, dict)]

    @staticmethod
    def _normalize(it: dict[str, Any]) -> dict[str, Any]:
        pid = str(it.get("postId") or "")
        return {
            "job_id": pid,
            "title": it.get("name") or "",
            "company": "百度",
            "salary": "",
            "city": it.get("workPlace") or "",
            "job_url": (
                f"https://talent.baidu.com/jobs/detail/SOCIAL/{pid}" if pid else ""
            ),
            "jd_text": _join(it.get("serviceCondition"), it.get("workContent")),
        }


class XiaomiSource(_BaseSource):
    """小米社招（飞书招聘 ATSX，与字节同构）。社招须省略 portal-channel。"""

    name = "小米"

    _API = "https://xiaomi.jobs.f.mioffice.cn/api/v1/search/job/posts"

    def _fetch_sync(self, keyword: str, page: int, limit: int) -> list[dict[str, Any]]:
        headers = {
            "User-Agent": _UA,
            "Content-Type": "application/json",
            "portal-platform": "pc",
            # 社招：不发送 portal-channel / website-path（带了会 site not exist）
            "Origin": "https://xiaomi.jobs.f.mioffice.cn",
        }
        body = {
            "keyword": keyword,
            "limit": max(1, min(100, limit)),
            "offset": page * max(1, min(100, limit)),
            "portal_type": 3,
            "portal_entrance": 1,
            "language": "zh",
        }
        resp = requests.post(self._API, headers=headers, json=body, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(
                "小米接口返回异常", code=data.get("code"), message=data.get("message")
            )
            return []
        items = (data.get("data") or {}).get("job_post_list") or []
        return [self._normalize(it) for it in items if isinstance(it, dict)]

    @staticmethod
    def _normalize(it: dict[str, Any]) -> dict[str, Any]:
        pid = str(it.get("id") or "")
        city = ""
        ci = it.get("city_info") or {}
        if ci.get("name"):
            city = str(ci["name"])
        else:
            cl = it.get("city_list") or []
            city = " / ".join(
                str(c.get("name")) for c in cl if isinstance(c, dict) and c.get("name")
            )
        return {
            "job_id": pid,
            "title": it.get("title") or "",
            "company": "小米",
            "salary": "",
            "city": city,
            "job_url": (
                f"https://xiaomi.jobs.f.mioffice.cn/index/position/{pid}/detail"
                if pid
                else ""
            ),
            "jd_text": _join(it.get("description"), it.get("requirement")),
        }


class AlibabaSource(_BaseSource):
    """阿里社招（talent.alibaba.com）。自签 XSRF（Cookie 与 header 一致即可）。"""

    name = "阿里"

    _API = "https://talent.alibaba.com/position/search"

    def _fetch_sync(self, keyword: str, page: int, limit: int) -> list[dict[str, Any]]:
        token = str(uuid.uuid4())  # 自签 XSRF：Cookie 与 header 相同即可（double-submit）
        headers = {
            "User-Agent": _UA,
            "Content-Type": "application/json",
            "X-XSRF-TOKEN": token,
            "Cookie": f"XSRF-TOKEN={token}",
            "Origin": "https://talent.alibaba.com",
            "Referer": "https://talent.alibaba.com/",
        }
        body = {
            "channel": "group_official_site",
            "language": "zh",
            "key": keyword,  # 关键词字段名是 key
            "pageIndex": page + 1,  # 1 基
            "pageSize": max(1, min(100, limit)),
            "batchId": "",
            "categories": "",
            "deptCodes": [],
            # 服务端城市过滤（regions 传城市名文本）实测返回 0，故留空；
            # 城市信息见每个职位的 workLocations 字段，可上层客户端过滤。
            "regions": "",
            "subCategories": "",
        }
        resp = requests.post(self._API, headers=headers, json=body, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("success"):
            logger.warning(
                "阿里接口返回异常", errorCode=payload.get("errorCode"),
                errorMsg=payload.get("errorMsg"),
            )
            return []
        items = ((payload.get("content") or {}).get("datas")) or []
        return [self._normalize(it) for it in items if isinstance(it, dict)]

    @staticmethod
    def _normalize(it: dict[str, Any]) -> dict[str, Any]:
        iid = str(it.get("id") or "")
        locs = it.get("workLocations") or []
        city = " / ".join(str(x) for x in locs if x)
        return {
            "job_id": iid,
            "title": it.get("name") or "",
            "company": "阿里巴巴",
            "salary": "",
            "city": city,
            "job_url": (
                f"https://talent.alibaba.com/off-campus/position-detail?positionId={iid}"
                if iid
                else ""
            ),
            "jd_text": _join(it.get("description"), it.get("requirement")),
        }


class XiaohongshuSource(_BaseSource):
    """小红书社招。关键词字段 positionName（单 term）；城市需客户端过滤。"""

    name = "小红书"

    _API = "https://job.xiaohongshu.com/websiterecruit/position/pageQueryPosition"

    def _fetch_sync(self, keyword: str, page: int, limit: int) -> list[dict[str, Any]]:
        headers = {
            "User-Agent": _UA,
            "Content-Type": "application/json",
            "Origin": "https://job.xiaohongshu.com",
        }
        body = {
            "recruitType": "social",
            "positionName": keyword,
            "pageNum": page + 1,  # 1 基；字段名是 pageNum
            "pageSize": max(1, min(100, limit)),
        }
        resp = requests.post(self._API, headers=headers, json=body, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("statusCode") != 200:
            logger.warning(
                "小红书接口返回异常", statusCode=payload.get("statusCode"),
                alertMsg=payload.get("alertMsg"),
            )
            return []
        items = ((payload.get("data") or {}).get("list")) or []
        return [self._normalize(it) for it in items if isinstance(it, dict)]

    @staticmethod
    def _normalize(it: dict[str, Any]) -> dict[str, Any]:
        pid = str(it.get("positionId") or "")
        return {
            "job_id": pid,
            "title": it.get("positionName") or "",
            "company": "小红书",
            "salary": "",
            "city": it.get("workplace") or "",
            "job_url": (
                f"https://job.xiaohongshu.com/social/position/{pid}" if pid else ""
            ),
            "jd_text": _join(it.get("duty"), it.get("qualification")),
        }


# 统一注册表：源名 -> 采集源类
SOURCES: dict[str, type[_BaseSource]] = {
    cls.name: cls
    for cls in (
        BytedanceSource,
        TencentSource,
        BaiduSource,
        XiaomiSource,
        AlibabaSource,
        XiaohongshuSource,
    )
}
