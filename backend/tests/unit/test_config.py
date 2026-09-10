"""
配置加载与校验的单元测试

测试内容：
- _expand_env_vars 函数（${VAR} 展开）
- load_config 正常加载
- 配置校验失败（如 max_tokens >= hard_token_limit）
- L1 必须启用
"""

from __future__ import annotations

import pytest
import yaml

from app.core.config import (
    AppConfig,
    L1MemoryConfig,
    LLMConfig,
    _expand_env_vars,
    load_config,
)
from app.core.exceptions import ConfigError

# ============================================================
# _expand_env_vars 测试
# ============================================================

class TestExpandEnvVars:
    """测试环境变量展开函数"""

    def test_expand_simple_var(self, monkeypatch):
        """测试 ${VAR} 被正确替换为环境变量值"""
        monkeypatch.setenv("TEST_CONFIG_VAR", "my-value")
        result = _expand_env_vars("prefix-${TEST_CONFIG_VAR}-suffix")
        assert result == "prefix-my-value-suffix"

    def test_expand_unset_var_to_empty(self, monkeypatch):
        """测试未设置的环境变量被替换为空字符串"""
        monkeypatch.delenv("TEST_UNSET_VAR", raising=False)
        result = _expand_env_vars("value-${TEST_UNSET_VAR}-end")
        assert result == "value--end"

    def test_expand_no_vars(self):
        """测试不含 ${VAR} 的字符串原样返回"""
        result = _expand_env_vars("普通字符串 no vars")
        assert result == "普通字符串 no vars"

    def test_expand_nested_dict(self, monkeypatch):
        """测试嵌套字典中所有字符串值都被展开"""
        monkeypatch.setenv("TEST_NESTED_KEY", "expanded")
        data = {
            "level1": {
                "level2": "${TEST_NESTED_KEY}",
                "plain": "no-var",
                "number": 42,
            },
            "top": "${TEST_NESTED_KEY}",
        }
        result = _expand_env_vars(data)
        assert result["level1"]["level2"] == "expanded"
        assert result["level1"]["plain"] == "no-var"
        assert result["level1"]["number"] == 42
        assert result["top"] == "expanded"

    def test_expand_list(self, monkeypatch):
        """测试列表中的字符串元素被展开"""
        monkeypatch.setenv("TEST_LIST_VAR", "list-value")
        data = ["${TEST_LIST_VAR}", "plain", 123, True]
        result = _expand_env_vars(data)
        assert result[0] == "list-value"
        assert result[1] == "plain"
        assert result[2] == 123
        assert result[3] is True

    def test_expand_non_string_types(self):
        """测试非字符串类型（int、bool、None）原样返回"""
        data = {"int": 42, "bool": True, "none": None, "float": 3.14}
        result = _expand_env_vars(data)
        assert result["int"] == 42
        assert result["bool"] is True
        assert result["none"] is None
        assert result["float"] == 3.14

    def test_expand_multiple_vars_in_one_string(self, monkeypatch):
        """测试同一字符串中多个 ${VAR} 被分别展开"""
        monkeypatch.setenv("VAR_A", "aaa")
        monkeypatch.setenv("VAR_B", "bbb")
        result = _expand_env_vars("${VAR_A}-${VAR_B}-${VAR_A}")
        assert result == "aaa-bbb-aaa"

    def test_expand_default_syntax_unset(self, monkeypatch):
        """${VAR:-default} 未设置时返回 default"""
        monkeypatch.delenv("TEST_DEFAULT_VAR", raising=False)
        result = _expand_env_vars("${TEST_DEFAULT_VAR:-fallback}")
        assert result == "fallback"

    def test_expand_default_syntax_set(self, monkeypatch):
        """${VAR:-default} 已设置时返回环境变量值"""
        monkeypatch.setenv("TEST_DEFAULT_VAR", "real-value")
        result = _expand_env_vars("${TEST_DEFAULT_VAR:-fallback}")
        assert result == "real-value"

    def test_expand_default_syntax_empty(self, monkeypatch):
        """${VAR:-default} 为空串时返回 default"""
        monkeypatch.setenv("TEST_DEFAULT_VAR", "")
        result = _expand_env_vars("${TEST_DEFAULT_VAR:-fallback}")
        assert result == "fallback"

    def test_expand_colon_default_syntax(self, monkeypatch):
        """${VAR:default}（简写）未设置时返回 default"""
        monkeypatch.delenv("TEST_COLON_VAR", raising=False)
        result = _expand_env_vars("${TEST_COLON_VAR:qwen-vl-plus}")
        assert result == "qwen-vl-plus"


# ============================================================
# load_config 正常加载测试
# ============================================================

class TestLoadConfig:
    """测试 load_config 函数"""

    def test_load_valid_config(self, tmp_path, monkeypatch):
        """测试正常加载配置文件"""
        # 设置环境变量以通过 api_key 校验
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-123")

        config_yaml = {
            "app": {"name": "test-app", "version": "0.1.0"},
            "llm": {
                "provider": "deepseek",
                "api_key": "${DEEPSEEK_API_KEY}",
                "base_url": "https://api.deepseek.com/v1",
                "roles": {
                    "supervisor": {
                        "model": "deepseek-chat",
                        "temperature": 0.1,
                        "max_tokens": 500,
                    },
                },
            },
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_yaml), encoding="utf-8")

        config = load_config(str(config_file))

        assert isinstance(config, AppConfig)
        assert config.app.name == "test-app"
        assert config.app.version == "0.1.0"
        assert config.llm.api_key == "test-key-123"
        assert config.llm.roles["supervisor"].model == "deepseek-chat"
        # 默认值验证
        assert config.memory.l1_working.enabled is True
        assert config.reflection.policy == "always"

    def test_load_config_file_not_found(self):
        """测试配置文件不存在时抛出 ConfigError"""
        with pytest.raises(ConfigError, match="配置文件不存在"):
            load_config("/nonexistent/path/config.yaml")

    def test_load_config_invalid_yaml(self, tmp_path):
        """测试 YAML 解析失败时抛出 ConfigError"""
        config_file = tmp_path / "bad.yaml"
        config_file.write_text("invalid: yaml: [unclosed", encoding="utf-8")
        with pytest.raises(ConfigError, match="YAML 解析失败"):
            load_config(str(config_file))


# ============================================================
# 配置校验失败测试
# ============================================================

class TestConfigValidation:
    """测试配置校验逻辑"""

    def test_max_tokens_must_be_less_than_hard_limit(self):
        """测试 max_tokens >= hard_token_limit 时校验失败"""
        invalid_l1 = L1MemoryConfig(
            enabled=True,
            max_turns=8,
            max_tokens=4000,
            hard_token_limit=4000,
        )
        # 直接构造 L1MemoryConfig 不触发 MemoryConfig 的 field_validator
        # 需要通过 AppConfig 或 MemoryConfig 触发校验
        with pytest.raises(Exception, match="max_tokens.*必须小于.*hard_token_limit"):
            from app.core.config import MemoryConfig
            MemoryConfig(l1_working=invalid_l1)

    def test_l1_must_be_enabled(self):
        """测试 Phase 1 中 L1 必须启用"""
        disabled_l1 = L1MemoryConfig(
            enabled=False,
            max_turns=8,
            max_tokens=3000,
            hard_token_limit=4000,
        )
        with pytest.raises(Exception, match="L1 短期记忆必须启用"):
            from app.core.config import MemoryConfig
            MemoryConfig(l1_working=disabled_l1)

    def test_invalid_reflection_policy(self):
        """测试无效的反思策略名称校验失败"""
        from app.core.config import ReflectionConfig
        with pytest.raises(Exception, match="reflection.policy 必须是"):
            ReflectionConfig(policy="invalid_policy")

    def test_api_key_empty_raises_error(self):
        """测试空 API Key 校验失败"""
        with pytest.raises(Exception, match="API Key 未设置"):
            LLMConfig(api_key="", roles={})

    def test_api_key_unset_env_var_raises_error(self, monkeypatch):
        """测试 ${VAR} 引用未设置的环境变量时校验失败"""
        monkeypatch.delenv("NONEXISTENT_API_KEY", raising=False)
        with pytest.raises(Exception, match="API Key 未设置"):
            LLMConfig(api_key="${NONEXISTENT_API_KEY}", roles={})
