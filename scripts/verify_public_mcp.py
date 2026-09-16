"""公网端到端验证：从本机经 https://bos-studio.tech/jobcopilot 连 MCP，验证 4 件事。

1. streamable HTTP 握手 + 列工具
2. 调一个**不需要 LLM** 的工具（list_prompt_packs）→ 证明完整往返可用
3. **不**带 BYOK Key 调 analyze_job → 应返回可操作错误（证明服务端没配 Key）
4. 带 BYOK Key 调 analyze_job → 应真的产出七段（证明 BYOK 从公网可用）

令牌与 LLM Key 都从环境变量读，不打印明文。
跑法：JOBCOPILOT_HTTP_TOKEN=... DEEPSEEK_API_KEY=... python scripts/verify_public_mcp.py

（2026-09-16 从本地讨论区 docs/tmp/ 移入版本库：`docs/ops/15-MCP-ENDPOINT.md`
以它作为「公网端点全链路复现」的正式步骤，放在 gitignore 目录里别人拿不到。）
"""
from __future__ import annotations

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

BASE = "https://bos-studio.tech/jobcopilot"
TOKEN = os.environ["JOBCOPILOT_HTTP_TOKEN"]
KEY = os.environ.get("DEEPSEEK_API_KEY", "")
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _payload(res: object) -> dict:
    """把工具返回的 content 解析成 dict（工具返回的是 JSON 文本）。"""
    for c in getattr(res, "content", []) or []:
        text = getattr(c, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"_raw": text[:200]}
    return {}


async def main() -> None:
    # ---- 1) 握手 + 列工具（streamable HTTP）----
    async with streamablehttp_client(f"{BASE}/mcp", headers=AUTH, timeout=30) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            print(f"1) 工具列表（{len(names)} 个）: {', '.join(names)}")

            # ---- 2) 不需要 LLM 的工具 ----
            packs = _payload(await s.call_tool("list_prompt_packs", {}))
            source = (packs.get("meta") or {}).get("source", "?")
            print(f"2) list_prompt_packs: packs={packs.get('packs')}  源={source}")

            # ---- 3) 不带 Key 调 LLM 工具 ----
            no_key = _payload(
                await s.call_tool("analyze_job", {"jd_text": "【测试】某公司 | 20-30K | 北京\n职责：写代码。"})
            )
            err = str(no_key.get("error", ""))[:120]
            print(f"3) 不带 Key 调 analyze_job → error={'有' if err else '无'} : {err}")

    # ---- 4) 带 BYOK Key（必须用独立会话：header 不同）----
    if KEY:
        async with streamablehttp_client(
            f"{BASE}/mcp",
            headers={**AUTH, "X-JobCopilot-Api-Key": KEY, "X-JobCopilot-Provider": "deepseek"},
            timeout=180,
        ) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                out = _payload(
                    await s.call_tool(
                        "analyze_job",
                        {
                            "jd_text": "【AI Agent 平台开发工程师】某公司 | 50-80K | 北京\n"
                            "职责：负责 Agent 编排框架与工具调用协议的设计与落地。\n"
                            "要求：3 年以上后端经验，熟悉 Python / LangGraph / RAG。"
                        },
                    )
                )
                sections = [
                    k
                    for k in (
                        "job_analysis",
                        "knowledge_priority",
                        "interview_qa",
                        "gap_analysis",
                        "resume_advice",
                        "project_iteration",
                        "job_strategy",
                    )
                    if out.get(k)
                ]
                print(f"4) 带 BYOK Key 调 analyze_job → 非空段落 {len(sections)}/7: {sections}")
                print(f"   用量: {out.get('usage')}  提示词来源: {(out.get('prompt_meta') or {}).get('pack', 'base')}")

    # ---- 5) SSE 也验一次（千帆只支持这条）----
    async with sse_client(f"{BASE}/sse", headers=AUTH, timeout=30, sse_read_timeout=30) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print(f"5) SSE 传输工具列表（{len(tools.tools)} 个）: OK")


if __name__ == "__main__":
    asyncio.run(main())
