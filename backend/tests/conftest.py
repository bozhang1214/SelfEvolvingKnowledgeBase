"""
Pytest 全局夹具模块

为所有测试提供可复用的测试夹具，包括：
- 临时数据目录
- 测试用 AppConfig（不依赖真实 API Key）
- 测试用 GraphState
- Mock LLM 工厂
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import AppConfig
from app.graph.state import create_initial_state

# ============================================================
# 目录与路径夹具
# ============================================================

@pytest.fixture
def test_data_dir(tmp_path):
    """临时数据目录，用于存储测试产生的文件"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


# ============================================================
# 配置夹具
# ============================================================

@pytest.fixture
def sample_config() -> AppConfig:
    """
    测试用配置（不依赖真实 API Key）。

    构造一个完整的 AppConfig 实例，使用 mock 值，
    所有字段均有合法默认值，可直接用于测试各模块。
    """
    config_dict = {
        "app": {
            "name": "test-app",
            "version": "0.1.0",
            "environment": "test",
            "debug": False,
        },
        "llm": {
            "provider": "deepseek",
            "api_key": "test-api-key-mock",
            "base_url": "https://api.test.com/v1",
            "timeout_seconds": 30,
            "max_retries": 2,
            "retry_backoff_seconds": [1, 2],
            "roles": {
                "supervisor": {
                    "model": "deepseek-flash",
                    "temperature": 0.1,
                    "max_tokens": 500,
                    "response_format": "json",
                },
                "planner": {
                    "model": "deepseek-reasoner",
                    "temperature": 0.2,
                    "max_tokens": 2000,
                    "response_format": "json",
                },
                "executor": {
                    "model": "deepseek-flash",
                    "temperature": 0.3,
                    "max_tokens": 2000,
                },
                "critic": {
                    "model": "deepseek-flash",
                    "temperature": 0.0,
                    "max_tokens": 1000,
                    "response_format": "json",
                },
                "critic_complex": {
                    "model": "deepseek-reasoner",
                    "temperature": 0.0,
                    "max_tokens": 2000,
                    "response_format": "json",
                },
                "scribe": {
                    "model": "deepseek-flash",
                    "temperature": 0.2,
                    "max_tokens": 800,
                    "response_format": "json",
                },
                "chat_simple": {
                    "model": "deepseek-flash",
                    "temperature": 0.7,
                    "max_tokens": 1000,
                },
            },
            "fallback": {
                "reasoner_to_chat": True,
                "chat_to_error": True,
            },
        },
        "memory": {
            "l1_working": {
                "enabled": True,
                "max_turns": 8,
                "max_tokens": 3000,
                "hard_token_limit": 4000,
                "compress_strategy": "hierarchical",
                "max_compressed_summaries": 3,
            },
        },
        "reflection": {
            "policy": "always",
            "max_replan": 2,
            "model_switch_threshold": 0.7,
        },
        "cost_control": {
            "pricing": {
                "deepseek-flash": {"input": 0.00014, "output": 0.00028},
                "deepseek-reasoner": {"input": 0.00055, "output": 0.00219},
            },
        },
    }
    return AppConfig(**config_dict)


@pytest.fixture
def small_l1_config(sample_config):
    """
    小窗口 L1 记忆配置（max_turns=2），用于测试滑动窗口与压缩。

    返回一个独立的 L1MemoryConfig 实例，不影响 sample_config。
    """
    from app.core.config import L1MemoryConfig

    return L1MemoryConfig(
        enabled=True,
        max_turns=2,
        max_tokens=100,
        hard_token_limit=200,
        compress_strategy="hierarchical",
        max_compressed_summaries=3,
    )


# ============================================================
# GraphState 夹具
# ============================================================

@pytest.fixture
def sample_graph_state():
    """测试用 GraphState，使用 create_initial_state 创建"""
    return create_initial_state("测试输入", "test-conv-id")


# ============================================================
# Mock LLM 工厂夹具
# ============================================================

@pytest.fixture
def mock_llm_factory():
    """
    Mock LLM 工厂。

    使用 MagicMock 模拟 LLMFactory，ainvoke_with_stats 返回一个
    带 content 属性的 Mock 响应。可在测试中自定义返回值。
    """
    factory = MagicMock()

    # 默认返回一个带 content 的 Mock 响应
    default_response = MagicMock()
    default_response.content = "这是一个 Mock LLM 响应"
    factory.ainvoke_with_stats = AsyncMock(return_value=default_response)
    factory.get = MagicMock(return_value=MagicMock())
    factory.stats = MagicMock()
    factory.stats.total_calls = 0
    factory.stats.total_cost_usd = 0.0

    return factory
