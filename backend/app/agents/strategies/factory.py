"""
反思策略工厂

根据配置文件中的 reflection.policy 创建对应的策略实例。
"""

from __future__ import annotations

from app.agents.strategies.always import AlwaysReflectStrategy
from app.agents.strategies.base import ReflectionStrategy
from app.core.config import AppConfig
from app.core.exceptions import ConfigError


def create_reflection_strategy(config: AppConfig) -> ReflectionStrategy:
    """
    根据配置创建反思策略实例。

    Args:
        config: 应用配置

    Returns:
        ReflectionStrategy 实例

    Raises:
        ConfigError: 未知的策略名称
    """
    policy = config.reflection.policy

    if policy == "always":
        return AlwaysReflectStrategy(config)
    elif policy == "adaptive":
        # Phase 2 扩展：AdaptiveReflectStrategy
        # 暂时降级为 always
        return AlwaysReflectStrategy(config)
    elif policy == "sampling":
        # Phase 2 扩展：SamplingReflectStrategy
        # 暂时降级为 always
        return AlwaysReflectStrategy(config)
    else:
        raise ConfigError(f"未知的反思策略: {policy}")
