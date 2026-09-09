"""
Embedding 函数封装模块。

提供统一的文本向量化接口，支持：
- sentence-transformers（本地模型，默认）
- DeepSeek/OpenAI 兼容 API（远程模型，可选）

本地模型优先策略：
- 首次使用 sentence-transformers 加载本地模型（首次需下载，后续从缓存加载）
- 加载失败时降级为哈希向量（仅用于测试/开发，不推荐生产）

使用方式：
    from app.core.embedding import get_embedding_function

    embed_fn = get_embedding_function(model_name="BAAI/bge-small-zh-v1.5")
    vectors = embed_fn(["你好", "世界"])  # → list[list[float]]
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class EmbeddingFunction(Protocol):
    """Embedding 函数协议：输入文本列表，返回向量列表。"""

    def __call__(self, texts: list[str]) -> list[list[float]]: ...


# ============================================================
# 本地模型 Embedding（sentence-transformers）
# ============================================================

class LocalEmbeddingFunction:
    """
    基于 sentence-transformers 的本地 Embedding 函数。

    模型懒加载：首次调用 __call__ 时才加载模型，避免模块导入时的重开销。
    线程安全：模型加载后只读，可并发调用。
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        allow_hash_fallback: bool = False,
    ):
        """
        Args:
            model_name: HuggingFace 模型名称。
                        推荐中文场景使用 BAAI/bge-small-zh-v1.5（512 维，体积小）。
            allow_hash_fallback: 模型加载失败时是否降级为哈希向量（256 维）。
                                 默认 False（严格模式）：失败即抛异常。
                                 哈希向量维度与真实模型不一致，混用会破坏向量索引，
                                 生产环境务必保持 False。
        """
        self.model_name = model_name
        self.allow_hash_fallback = allow_hash_fallback
        self._model: Any = None
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """懒加载模型。"""
        if self._loaded:
            return
        try:
            from sentence_transformers import SentenceTransformer

            logger.info("加载 Embedding 模型", extra={"model": self.model_name})
            self._model = SentenceTransformer(self.model_name)
            self._loaded = True
            logger.info("Embedding 模型加载完成", extra={"model": self.model_name})
        except ImportError as e:
            if self.allow_hash_fallback:
                logger.warning(
                    "sentence-transformers 未安装，降级为哈希向量（仅限开发/测试）",
                )
                self._model = None
                self._loaded = True
            else:
                raise RuntimeError(
                    "sentence-transformers 未安装，且已禁用哈希降级（allow_hash_fallback=false）"
                ) from e
        except Exception as e:
            if self.allow_hash_fallback:
                logger.warning(
                    "Embedding 模型加载失败，降级为哈希向量",
                    extra={"model": self.model_name, "error": str(e)},
                )
                self._model = None
                self._loaded = True
            else:
                raise RuntimeError(
                    f"Embedding 模型加载失败（已禁用哈希降级）: {e}"
                ) from e

    def __call__(self, texts: list[str]) -> list[list[float]]:
        """将文本列表转换为向量列表。"""
        self._ensure_loaded()
        if self._model is not None:
            embeddings = self._model.encode(texts, normalize_embeddings=True)
            return embeddings.tolist()
        # 降级：哈希向量（固定 256 维，仅用于测试）
        return [self._hash_embedding(t) for t in texts]

    @staticmethod
    def _hash_embedding(text: str, dim: int = 256) -> list[float]:
        """哈希向量（降级方案，仅用于测试/开发环境）。"""
        vec = [0.0] * dim
        for i, byte in enumerate(text.encode("utf-8")):
            vec[i % dim] += byte / 255.0
        # 归一化
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


# ============================================================
# 工厂函数
# ============================================================

_embedding_cache: dict[str, EmbeddingFunction] = {}


def get_embedding_function(
    model_name: str = "BAAI/bge-small-zh-v1.5",
    allow_hash_fallback: bool = False,
) -> EmbeddingFunction:
    """
    获取 Embedding 函数实例（单例缓存）。

    Args:
        model_name: 模型名称
        allow_hash_fallback: 模型加载失败时是否降级为哈希向量（默认 False）

    Returns:
        EmbeddingFunction 实例
    """
    if model_name not in _embedding_cache:
        _embedding_cache[model_name] = LocalEmbeddingFunction(
            model_name, allow_hash_fallback
        )
    return _embedding_cache[model_name]
