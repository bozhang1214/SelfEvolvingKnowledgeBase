"""内核信息（运行时可见性）的测试。

这些断言保证「线上跑的是哪个内核」这件事在 `/health` 里始终可读——
子模块钉版本的价值只有在运行时可验证时才成立。
"""
from __future__ import annotations

from app.core.kernel_info import ENV_KERNEL_COMMIT, kernel_info, log_kernel_info


def test_kernel_info_shape() -> None:
    """返回结构包含版本、commit、提示词数量与健康位。"""
    kernel_info.cache_clear()
    info = kernel_info()
    assert info["name"] == "jobcopilot"
    assert info["version"]
    assert isinstance(info["prompts"], int)
    assert "commit" in info
    assert "healthy" in info


def test_kernel_info_reports_bundled_prompts() -> None:
    """包内提示词必须齐全（这正是构建期要装进镜像的东西）。"""
    kernel_info.cache_clear()
    assert kernel_info()["prompts"] >= 12


def test_kernel_info_reports_effective_prompt_source() -> None:
    """报告提示词**实际生效**来源：SEKB 的 prompt/job 应优先于包内 base。"""
    kernel_info.cache_clear()
    info = kernel_info()
    assert info["prompt_source"] in {"local", "base", "pack"}
    # SEKB 检出存在时必须是 local（运营可热改提示词）
    if info["prompt_dir"]:
        assert info["prompt_source"] == "local"


def test_kernel_info_reads_commit_from_env(monkeypatch) -> None:
    """commit 来自构建期烧入的环境变量（deploy.sh 传 docker build arg）。"""
    monkeypatch.setenv(ENV_KERNEL_COMMIT, "a4b1885f0c0d3fa5cd192e803ba110a5560c4842")
    kernel_info.cache_clear()
    assert kernel_info()["commit"] == "a4b1885f0c0d3fa5cd192e803ba110a5560c4842"


def test_kernel_info_unknown_commit_when_env_absent(monkeypatch) -> None:
    """本地直接跑（未经构建）时没有该变量，应显示 unknown 而不是崩。"""
    monkeypatch.delenv(ENV_KERNEL_COMMIT, raising=False)
    kernel_info.cache_clear()
    assert kernel_info()["commit"] == "unknown"


def test_log_kernel_info_does_not_raise() -> None:
    """启动日志函数不得抛异常（启动期异常会拖垮整个应用）。"""
    kernel_info.cache_clear()
    log_kernel_info()
