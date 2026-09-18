"""P4 接线测试：SEKB ↔ jobcopilot-mcp（真实子进程，stdio 协议）。

这些用例会**真的拉起** ``jobcopilot-mcp`` 子进程，走完整 MCP 握手。
用假 Key 构造（内核只在被调用时才发 LLM 请求），因此不花钱、可离线跑。

覆盖 P4 DoD：
- DoD 2：新增 MCP 接线测试（工具可列、可调）
- DoD 3：子进程异常时给出**清晰错误**，而不是 500 堆栈
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents.job import mcp_client as mc

#: 内核子进程需要 Key 才能启动（构造期即校验）；给个假 Key 即可
FAKE_KEY = "sk-fake-for-wiring-test"


@pytest.fixture(autouse=True)
def _kernel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离环境：假 Key + 独立的提示词/数据目录，避免污染真实配置。"""
    monkeypatch.setenv("JOBCOPILOT_LLM_API_KEY", FAKE_KEY)
    monkeypatch.setenv("JOBCOPILOT_DATA_DIR", "data/jobcopilot-test")


@pytest.fixture
async def kernel() -> mc.JobCopilotMCP:
    """一个用完即关的内核客户端。"""
    client = mc.JobCopilotMCP(command="jobcopilot-mcp")
    yield client
    await client.close()


@pytest.mark.asyncio
async def test_kernel_process_starts_and_lists_tools(kernel: mc.JobCopilotMCP) -> None:
    """子进程能起来，且内核暴露了分析工具（DoD 2）。"""
    assert await kernel.health_check() is True


@pytest.mark.asyncio
async def test_kernel_prompts_dir_is_forwarded(kernel: mc.JobCopilotMCP) -> None:
    """SEKB 的 prompt/job 必须传给内核子进程。

    否则内核会用包内 base 提示词，**现网提示词热改（bind mount）就失效了**——
    这是切换 MCP 后最容易悄悄丢掉的既有能力。
    """
    from app.agents.job.generator import _resolve_prompt_dir

    prompt_dir = _resolve_prompt_dir()
    assert prompt_dir is not None, "测试环境应有 prompt/job 目录"

    client = mc.JobCopilotMCP(command="jobcopilot-mcp", prompts_dir=prompt_dir)
    try:
        out = await client.call("list_prompt_packs", {})
    finally:
        await client.close()
    assert out["local_dir"] == str(prompt_dir)
    # 本地目录已存在时，实测生效来源应为 local
    assert out["local_files"], "内核没有看到 SEKB 的本地提示词文件"


@pytest.mark.asyncio
async def test_llm_failure_raises_clear_error(kernel: mc.JobCopilotMCP) -> None:
    """假 Key → 内核 LLM 全失败 → 必须抛**可读**的 KernelMCPError（DoD 3）。

    绝不能让「7 段全空」当成正常结果返回——那正是 P2 发现并堵掉的静默失败。
    """
    with pytest.raises(mc.KernelMCPError) as ei:
        await kernel.call("analyze_job", {"jd_text": "招聘 Agent 工程师"})
    msg = str(ei.value)
    assert "内核返回错误" in msg
    assert any(k in msg for k in ("LLM", "API Key", "不可用", "余额")), msg


@pytest.mark.asyncio
async def test_argument_error_is_actionable(kernel: mc.JobCopilotMCP) -> None:
    """参数错误（既无 jd_text 也无 source_path）→ 错误里要说明怎么修。"""
    with pytest.raises(mc.KernelMCPError) as ei:
        await kernel.call("analyze_job", {})
    assert "jd_text" in str(ei.value) and "source_path" in str(ei.value)


@pytest.mark.asyncio
async def test_missing_command_gives_install_hint() -> None:
    """内核命令不存在时，报错要说清「怎么装」，而不是裸的 FileNotFoundError。"""
    client = mc.JobCopilotMCP(command="jobcopilot-mcp-does-not-exist")
    try:
        with pytest.raises(mc.KernelMCPError) as ei:
            await client.call("list_prompt_packs", {})
        msg = str(ei.value)
        assert "无法连接内核" in msg
        assert "pip install" in msg or "PATH" in msg
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_timeout_is_reported_clearly() -> None:
    """超时要报出工具名与超时值，并指出可调参数。"""
    client = mc.JobCopilotMCP(command="jobcopilot-mcp", timeout_s=0.001)
    try:
        with pytest.raises(mc.KernelMCPError) as ei:
            await client.call("list_prompt_packs", {})
        msg = str(ei.value)
        assert "超时" in msg
        assert "job.mcp_timeout_s" in msg
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_connection_is_reused(kernel: mc.JobCopilotMCP) -> None:
    """多次调用复用同一条连接（不是每次都起子进程）。"""
    await kernel.call("list_prompt_packs", {})
    first = kernel._client
    await kernel.call("list_prompt_packs", {})
    assert kernel._client is first


@pytest.mark.asyncio
async def test_connect_runs_in_caller_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """建连必须在**调用方 task** 里完成（否则关闭时 anyio 报跨 task 错）。

    回归背景（2026-09-18 实测）：`_ensure_client` 曾用
    `asyncio.wait_for(client.connect(), ...)`，而 wait_for 会把协程丢进**新 task**。
    MCPClient 内部是 anyio 的 cancel scope（stdio_client / ClientSession），要求
    「进入」与「退出」同一个 task —— 于是每次关闭内核 MCP 都会刷
    `Attempted to exit cancel scope in a different task than it was entered in`。
    """
    seen: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def connect(self) -> None:
            seen["task"] = asyncio.current_task()

        async def list_tools(self) -> list[dict]:
            return []

    monkeypatch.setattr(mc, "MCPClient", _FakeClient)
    kernel = mc.JobCopilotMCP(command="jobcopilot-mcp")

    await kernel.health_check()  # 触发 _ensure_client → connect

    assert seen.get("task") is asyncio.current_task(), (
        "建连跑到了别的 task（asyncio.wait_for 的老毛病）"
    )


@pytest.mark.asyncio
async def test_shared_kernel_is_singleton() -> None:
    """单职位与批量分析共用**同一条**内核连接（避免每处都起子进程）。"""
    from types import SimpleNamespace

    cfg = SimpleNamespace(mcp_command="jobcopilot-mcp", mcp_timeout_s=60.0,
                          mcp_connect_timeout_s=20.0)
    try:
        a = mc.get_shared_kernel(cfg)
        b = mc.get_shared_kernel(cfg)
        assert a is b
    finally:
        await mc.close_shared_kernel()


@pytest.mark.asyncio
async def test_as_dict_normalizes_shapes() -> None:
    """返回值规整：dict 原样、JSON 文本解析、纯文本包成 result。"""
    assert mc.JobCopilotMCP._as_dict({"a": 1}) == {"a": 1}
    assert mc.JobCopilotMCP._as_dict('{"a": 1}') == {"a": 1}
    assert mc.JobCopilotMCP._as_dict("普通文本") == {"result": "普通文本"}
    assert mc.JobCopilotMCP._as_dict([{"type": "text", "text": '{"b": 2}'}]) == {"b": 2}


@pytest.mark.asyncio
async def test_child_env_forwards_key_and_prompts_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """子进程环境必须带上 LLM Key 与提示词目录。

    内核是独立进程、自己调 LLM；漏传 Key 会让它在运行时以「LLM 不可用」失败，
    而不是启动时就报错——那种失败很难定位。
    """
    from types import SimpleNamespace

    from app.agents.job.generator import _kernel_llm_env

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-sekb")
    monkeypatch.delenv("JOBCOPILOT_LLM_API_KEY", raising=False)
    extra = _kernel_llm_env(SimpleNamespace())

    client = mc.JobCopilotMCP(
        command="jobcopilot-mcp",
        prompts_dir="/tmp/job-prompts",
        env_extra=extra,
    )
    env = client._child_env()
    assert env["JOBCOPILOT_LLM_API_KEY"] == "sk-from-sekb"
    assert env["JOBCOPILOT_LLM_PROVIDER"] == "deepseek"
    assert env["JOBCOPILOT_PROMPTS_DIR"] == "/tmp/job-prompts"


@pytest.mark.asyncio
async def test_kernel_llm_env_warns_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """没 Key 时内核必然失败，所以这里必须留下明确日志（而不是静默）。"""
    from types import SimpleNamespace

    from app.agents.job.generator import _kernel_llm_env

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert _kernel_llm_env(SimpleNamespace()) == {}


@pytest.mark.asyncio
async def test_generator_uses_mcp_transport_by_default() -> None:
    """默认 transport=mcp：JobAnalysisGenerator 必须走 MCP 而不是静默退回直连。"""
    from types import SimpleNamespace

    from app.agents.job.generator import JobAnalysisGenerator

    cfg = SimpleNamespace(transport="mcp", mcp_command="jobcopilot-mcp",
                          mcp_timeout_s=60.0, mcp_connect_timeout_s=20.0)
    gen = JobAnalysisGenerator(llm_factory=None, config=cfg)
    assert gen.transport == "mcp"
    assert gen.analyzer is None, "mcp 模式不应创建直连分析器"


@pytest.mark.asyncio
async def test_generator_direct_transport_still_available() -> None:
    """回滚开关：transport=direct 时仍能构造直连分析器。"""
    from types import SimpleNamespace

    from app.agents.job.generator import JobAnalysisGenerator

    cfg = SimpleNamespace(transport="direct")
    gen = JobAnalysisGenerator(llm_factory=SimpleNamespace(), config=cfg)
    assert gen.transport == "direct"
    assert gen.analyzer is not None


@pytest.mark.asyncio
async def test_job_agent_passes_config_to_generator() -> None:
    """JobAgent 必须把 config 传下去，否则 transport 会静默退回 direct。"""
    from types import SimpleNamespace

    from app.agents.job.service import JobAgent

    cfg = SimpleNamespace(
        llm_role="job_analysis",
        default_keyword="Agent",
        default_city="北京",
        default_city_code="010",
        default_min_salary_k=50,
        exclude_companies=[],
        transport="mcp",
        mcp_command="jobcopilot-mcp",
        mcp_timeout_s=60.0,
        mcp_connect_timeout_s=20.0,
    )
    agent = JobAgent(cfg, llm_factory=None)
    assert agent._generator.transport == "mcp"


def test_prompt_dir_resolution_matches_legacy() -> None:
    """提示词目录解析逻辑与 P0 抽取前一致（容器内 /app/prompt/job）。"""
    from app.agents.job.generator import _resolve_prompt_dir

    p = _resolve_prompt_dir()
    assert p is None or (p.name == "job" and (p / "批量职位分析.md").exists())


def test_prompt_files_unchanged_by_p4() -> None:
    """P4 不该动提示词：SEKB 的 prompt/job 与包内 base 仍逐字节一致。"""
    from jobcopilot.core.prompts import base_dir

    from app.agents.job.generator import _resolve_prompt_dir

    prompt_dir = _resolve_prompt_dir()
    if prompt_dir is None:
        pytest.skip("无 SEKB 提示词目录")
    mismatched = [
        name
        for name in (p.name for p in base_dir().glob("*.md"))
        if (prompt_dir / name).read_bytes() != (base_dir() / name).read_bytes()
    ]
    assert not mismatched, f"提示词出现差异：{mismatched}"


def test_default_transport_is_mcp() -> None:
    """配置默认值必须是 mcp（P4 的目标状态）。"""
    from app.core.config import JobConfig

    assert JobConfig().transport == "mcp"
    assert JobConfig().mcp_command == "jobcopilot-mcp"
    assert JobConfig().mcp_timeout_s >= 180


def test_config_yaml_has_transport() -> None:
    """config.yaml 里也要有，否则运维改了不生效。"""

    text = Path("config.yaml").read_text(encoding="utf-8")
    assert "transport: mcp" in text
