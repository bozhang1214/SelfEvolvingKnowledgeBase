"""Prompt 模板注册表：路径配置化 + 缓存 + 热重载 + 版本。

把硬编码在 ``templates.py`` 里的提示词逐步迁移到 ``prompt/`` 目录下的
markdown 文件，由本注册表统一加载，支持：
- ``get(name)``：按名加载并缓存（文件不存在返回 None）
- ``reload()``：清空缓存，下次读取重新从磁盘加载（热重载，无需重启）
- ``version(name)``：当前内容的 sha256 短哈希，用于版本追踪 / A/B

使用方式：
    registry = PromptRegistry("prompt")
    text = registry.get("news/daily_report")       # prompt/news/daily_report.md
    registry.reload()                               # 热重载
    registry.version("news/daily_report")           # 版本短哈希
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)


class PromptRegistry:
    """Prompt 模板文件注册表（缓存 + 热重载 + 版本哈希）。"""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self._cache: dict[str, str] = {}
        self._versions: dict[str, str] = {}

    def _path(self, name: str) -> Path:
        return self.base_dir / f"{name}.md"

    def get(self, name: str) -> str | None:
        """读取并缓存 prompt；文件不存在返回 None。"""
        if name in self._cache:
            return self._cache[name]
        path = self._path(name)
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Prompt 读取失败", name=name, error=str(e))
            return None
        self._cache[name] = text
        self._versions[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return text

    def version(self, name: str) -> str | None:
        """返回当前缓存版本的短哈希（未加载则触发加载）。"""
        if name not in self._versions:
            self.get(name)
        return self._versions.get(name)

    def reload(self) -> None:
        """清空缓存，下次 get 重新从磁盘加载（热重载）。"""
        self._cache.clear()
        self._versions.clear()
        logger.info("Prompt 注册表已重载")
