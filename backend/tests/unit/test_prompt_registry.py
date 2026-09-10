"""Prompt 注册表（PromptRegistry）的单元测试。"""

from __future__ import annotations

from app.agents.prompts.registry import PromptRegistry


class TestPromptRegistry:
    def test_get_loads_and_caches(self, tmp_path):
        (tmp_path / "foo.md").write_text("hello prompt", encoding="utf-8")
        reg = PromptRegistry(tmp_path)
        assert reg.get("foo") == "hello prompt"
        # 缓存命中（版本已记录）
        assert reg.version("foo") is not None

    def test_get_missing_returns_none(self, tmp_path):
        reg = PromptRegistry(tmp_path)
        assert reg.get("nope") is None
        assert reg.version("nope") is None

    def test_reload_picks_up_changes(self, tmp_path):
        f = tmp_path / "bar.md"
        f.write_text("v1", encoding="utf-8")
        reg = PromptRegistry(tmp_path)
        assert reg.get("bar") == "v1"
        v1 = reg.version("bar")

        f.write_text("v2-changed", encoding="utf-8")
        reg.reload()
        assert reg.get("bar") == "v2-changed"
        v2 = reg.version("bar")
        assert v1 != v2

    def test_version_stable_for_same_content(self, tmp_path):
        f = tmp_path / "baz.md"
        f.write_text("same content", encoding="utf-8")
        reg = PromptRegistry(tmp_path)
        assert reg.version("baz") == reg.version("baz")
