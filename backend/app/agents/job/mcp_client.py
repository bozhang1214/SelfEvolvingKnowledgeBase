"""JobCopilot 内核的 MCP 客户端接线（P4）。

**为什么走 MCP 而不是直接 import**：让 SEKB 变成内核的*客户端*而不是*调用方*——
内核可以独立发版、独立部署，SEKB 不需要跟着改代码、也不需要重新构建镜像。

设计要点
--------
- **常驻连接**：每次分析都拉起一个子进程会多 ~1s 冷启动，且进程数随并发线性增长。
  这里维持一条长连接，断线自动重连。
- **错误必须清晰**：子进程起不来 / 调用超时 / 内核返回结构化错误，都要转成
  带排查方向的异常，而不是让路由抛 500 堆栈（P4 DoD 3）。
- **按请求传画像**：SEKB 是多用户系统，画像必须逐请求注入（不能依赖内核的全局画像）。
- **提示词目录**：把 SEKB 的 ``prompt/job`` 通过 ``JOBCOPILOT_PROMPTS_DIR`` 传给子进程，
  保证现网提示词热改仍然生效（行为与直连时期一致）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.tools.mcp.client import MCPClient

logger = get_logger(__name__)

#: 与内核约定：单职位 / 批量分析的工具名
TOOL_ANALYZE_JOB = "analyze_job"
TOOL_ANALYZE_JOBS_BATCH = "analyze_jobs_batch"


class KernelMCPError(RuntimeError):
    """内核 MCP 调用失败（连接、超时、工具错误），文案面向排障。"""


class JobCopilotMCP:
    """jobcopilot-mcp 的常驻 stdio 客户端。

    Args:
        command: 子进程命令（默认 ``jobcopilot-mcp``）。
        prompts_dir: 传给内核的本地提示词目录（SEKB 的 ``prompt/job``）。
        timeout_s: 单次工具调用超时。
        connect_timeout_s: 建连超时（含子进程冷启动）。
        env_extra: 额外环境变量。
    """

    def __init__(
        self,
        command: str = "jobcopilot-mcp",
        prompts_dir: str | Path | None = None,
        timeout_s: float = 180.0,
        connect_timeout_s: float = 30.0,
        env_extra: dict[str, str] | None = None,
    ) -> None:
        self._command = command
        self._prompts_dir = str(prompts_dir) if prompts_dir else None
        self._timeout = timeout_s
        self._connect_timeout = connect_timeout_s
        self._env_extra = env_extra or {}
        self._client: MCPClient | None = None
        self._lock = asyncio.Lock()

    # ---------- 连接管理 ----------

    def _build_client(self) -> MCPClient:
        """构造 MCPClient（先把 SEKB 的提示词目录等传给子进程）。"""
        # 注意：MCPClient 不支持 timeout 参数，超时由本类用 asyncio.wait_for 控制；
        # env 会与 MCP SDK 的默认环境合并，但默认环境**不含** DEEPSEEK_API_KEY 之类，
        # 所以这里传完整环境（dict(os.environ) + 覆盖项）。
        return MCPClient(server_command=[self._resolve_command()], env=self._child_env())

    def _resolve_command(self) -> str:
        """定位内核可执行文件。

        先查 PATH；查不到就试**当前解释器同目录**——直接跑虚拟环境里的
        python（如 ``.venv/bin/python -m pytest``、supervisor 直接拉起 venv python）
        时，venv 的 bin 目录往往不在 PATH 上，裸命令会找不到。
        两者都失败就原样返回，交给调用方报「无法连接内核」。
        """
        found = shutil.which(self._command)
        if found:
            return found
        sibling = Path(sys.executable).parent / self._command
        if sibling.exists():
            return str(sibling)
        return self._command

    def _child_env(self) -> dict[str, str]:
        """子进程环境：显式把提示词目录与数据目录指过去。

        提示词目录必须是 SEKB 的 ``prompt/job``（bind mount，运营可热改），
        否则内核会用包内 base，现网提示词改动就失效了。
        """
        env = dict(os.environ)
        if self._prompts_dir:
            env["JOBCOPILOT_PROMPTS_DIR"] = self._prompts_dir
        env.setdefault("JOBCOPILOT_DATA_DIR", str(Path("data") / "jobcopilot"))
        env.update(self._env_extra)
        return env

    async def _ensure_client(self) -> MCPClient:
        """确保连接可用（双重检查 + 锁，避免并发重复建连）。"""
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is not None:
                return self._client
            client = self._build_client()
            try:
                await asyncio.wait_for(client.connect(), timeout=self._connect_timeout)
            except Exception as e:  # noqa: BLE001
                raise KernelMCPError(
                    f"无法连接内核 {self._command}：{str(e)[:200]}。"
                    "排查：容器内是否装了 jobcopilot（pip install jobcopilot[mcp]）、"
                    f"命令是否在 PATH 中、JOBCOPILOT_LLM_API_KEY 是否已设置。"
                ) from e
            self._client = client
            logger.info("内核 MCP 连接已建立", command=self._command)
            return client

    async def close(self) -> None:
        """关闭连接（应用停机时调用）。"""
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception as e:  # noqa: BLE001
                logger.warning("关闭内核 MCP 连接失败（忽略）", error=str(e)[:120])

    async def health_check(self) -> bool:
        """探活：能否连上并列出工具。"""
        try:
            client = await self._ensure_client()
            tools = await client.list_tools()
            return any(t.get("name") == TOOL_ANALYZE_JOB for t in tools)
        except Exception as e:  # noqa: BLE001
            logger.warning("内核 MCP 健康检查失败", error=str(e)[:160])
            return False

    # ---------- 调用 ----------

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用内核工具，返回解析后的字典。

        Raises:
            KernelMCPError: 连接失败、超时、或内核返回了结构化错误
                （内核在「LLM 全挂」等情况下会返回 ``{"error": ...}``，
                这里要显式转成异常，绝不能让它当成正常结果继续往下走）。
        """
        try:
            client = await self._ensure_client()
        except KernelMCPError:
            raise

        try:
            result = await asyncio.wait_for(
                client.call_tool(tool, arguments), timeout=self._timeout
            )
        except asyncio.TimeoutError as e:
            await self._drop_client()
            raise KernelMCPError(
                f"内核调用超时（{self._timeout:.0f}s）：tool={tool}。"
                "批量分析可尝试减少职位数，或调大 job.mcp_timeout_s。"
            ) from e
        except Exception as e:  # noqa: BLE001
            await self._drop_client()  # 连接可能已坏，下次重建
            raise KernelMCPError(
                f"内核调用失败：tool={tool} error={str(e)[:200]}。"
                "排查：内核子进程是否存活、LLM Key/余额是否正常。"
            ) from e

        payload = self._as_dict(result)
        if payload.get("error"):
            # 内核的结构化错误（例如「7 段全空 = LLM 不可用」）
            raise KernelMCPError(f"内核返回错误：{payload['error']}")
        return payload

    async def _drop_client(self) -> None:
        """丢弃当前连接（下次调用会重建）。"""
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _as_dict(result: Any) -> dict[str, Any]:
        """把 MCP 返回值规整成字典。

        MCPClient 在「单个文本内容且是 JSON」时已经解析好了，但纯文本 / 多段内容
        会返回 str 或 list——这里统一兜住，避免上层拿到非预期类型。
        """
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                return parsed if isinstance(parsed, dict) else {"result": parsed}
            except json.JSONDecodeError:
                return {"result": result}
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        parsed = json.loads(item.get("text", ""))
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        continue
            return {"result": result}
        return {"result": result}


# ============================================================
# 进程内共享连接
# ============================================================
# 单职位与批量分析都需要内核，各起一个子进程既浪费又难管理；
# 这里维持**每进程一条**长连接（并发调用由 MCP session 内部按 JSON-RPC id 区分）。

_shared: JobCopilotMCP | None = None


def get_shared_kernel(config: Any) -> JobCopilotMCP:
    """返回进程内共享的 JobCopilotMCP（首次调用时按配置构造）。"""
    global _shared
    if _shared is None:
        from app.agents.job.generator import _kernel_llm_env, _resolve_prompt_dir

        _shared = JobCopilotMCP(
            command=getattr(config, "mcp_command", "jobcopilot-mcp"),
            prompts_dir=_resolve_prompt_dir(),
            timeout_s=getattr(config, "mcp_timeout_s", 180.0),
            connect_timeout_s=getattr(config, "mcp_connect_timeout_s", 30.0),
            env_extra=_kernel_llm_env(config),
        )
    return _shared


async def close_shared_kernel() -> None:
    """关闭共享连接（应用停机 / 测试清理时调用）。"""
    global _shared
    if _shared is not None:
        await _shared.close()
        _shared = None
