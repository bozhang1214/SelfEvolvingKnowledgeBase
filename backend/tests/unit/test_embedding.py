"""
Embedding 函数封装模块的单元测试

测试内容：
- LocalEmbeddingFunction 创建
- __call__ 返回正确维度（强制使用降级哈希向量，不依赖真实模型）
- 哈希向量归一化（模长为 1）
- get_embedding_function 单例缓存
- 不同文本生成不同向量

技术要点：
- 通过手动设置 _loaded=True、_model=None 强制走哈希降级路径，
  避免依赖 sentence-transformers 是否安装及模型下载。
"""

from __future__ import annotations

import pytest

from app.core import embedding as embedding_module
from app.core.embedding import LocalEmbeddingFunction, get_embedding_function

# ============================================================
# 辅助函数
# ============================================================

def _make_hash_embed_fn(model_name: str = "test-hash-model") -> LocalEmbeddingFunction:
    """
    构造一个强制走哈希降级路径的 LocalEmbeddingFunction。

    通过设置 _loaded=True、_model=None 跳过 _ensure_loaded 的真实模型加载，
    确保 __call__ 走 _hash_embedding 分支。
    """
    fn = LocalEmbeddingFunction(model_name)
    fn._loaded = True
    fn._model = None
    return fn


# ============================================================
# LocalEmbeddingFunction 创建测试
# ============================================================

class TestLocalEmbeddingFunctionCreation:
    """测试 LocalEmbeddingFunction 创建"""

    def test_create_with_default_model(self):
        """默认模型名为 BAAI/bge-small-zh-v1.5"""
        fn = LocalEmbeddingFunction()
        assert fn.model_name == "BAAI/bge-small-zh-v1.5"
        assert fn._loaded is False
        assert fn._model is None

    def test_create_with_custom_model(self):
        """自定义模型名可正确赋值"""
        fn = LocalEmbeddingFunction("custom/model-name")
        assert fn.model_name == "custom/model-name"


# ============================================================
# __call__ 测试（哈希降级路径）
# ============================================================

class TestEmbeddingCall:
    """测试 __call__ 向量化"""

    def test_returns_list_of_vectors(self):
        """返回值为向量列表"""
        fn = _make_hash_embed_fn()
        result = fn(["你好", "世界"])
        assert isinstance(result, list)
        assert len(result) == 2
        for vec in result:
            assert isinstance(vec, list)
            assert all(isinstance(v, float) for v in vec)

    def test_hash_vector_dimension_is_256(self):
        """降级哈希向量维度为 256"""
        fn = _make_hash_embed_fn()
        result = fn(["测试文本"])
        assert len(result) == 1
        assert len(result[0]) == 256

    def test_empty_input_returns_empty_list(self):
        """空文本列表返回空列表"""
        fn = _make_hash_embed_fn()
        assert fn([]) == []

    def test_single_text_returns_single_vector(self):
        """单条文本返回单条向量"""
        fn = _make_hash_embed_fn()
        result = fn(["单条文本"])
        assert len(result) == 1

    def test_multiple_texts_return_matching_count(self):
        """多条文本返回等长向量列表"""
        fn = _make_hash_embed_fn()
        texts = ["文本一", "文本二", "文本三", "文本四"]
        result = fn(texts)
        assert len(result) == len(texts)


# ============================================================
# 哈希向量归一化测试
# ============================================================

class TestHashVectorNormalization:
    """测试哈希向量归一化"""

    def test_vector_is_normalized(self):
        """哈希向量模长为 1（L2 归一化）"""
        fn = _make_hash_embed_fn()
        result = fn(["一段用于归一化测试的文本"])
        vec = result[0]
        norm = sum(v * v for v in vec) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-9)

    def test_multiple_vectors_all_normalized(self):
        """多条文本的向量均归一化"""
        fn = _make_hash_embed_fn()
        result = fn(["文本A", "文本B", "文本C"])
        for vec in result:
            norm = sum(v * v for v in vec) ** 0.5
            assert norm == pytest.approx(1.0, abs=1e-9)

    def test_hash_embedding_static_method_normalized(self):
        """直接调用 _hash_embedding 静态方法也归一化"""
        vec = LocalEmbeddingFunction._hash_embedding("任意文本")
        norm = sum(v * v for v in vec) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-9)

    def test_custom_dimension_via_static_method(self):
        """_hash_embedding 支持自定义维度"""
        vec = LocalEmbeddingFunction._hash_embedding("文本", dim=128)
        assert len(vec) == 128


# ============================================================
# 不同文本生成不同向量测试
# ============================================================

class TestDistinctTextVectors:
    """测试不同文本生成不同向量"""

    def test_different_texts_different_vectors(self):
        """不同文本生成不同向量"""
        fn = _make_hash_embed_fn()
        result = fn(["你好世界", "你好地球"])
        assert result[0] != result[1]

    def test_same_text_same_vector(self):
        """相同文本生成相同向量（确定性）"""
        fn = _make_hash_embed_fn()
        r1 = fn(["相同的文本内容"])
        r2 = fn(["相同的文本内容"])
        assert r1[0] == r2[0]

    def test_different_texts_in_batch_distinct(self):
        """批量中多条不同文本两两不同"""
        fn = _make_hash_embed_fn()
        texts = ["苹果", "香蕉", "橙子", "葡萄"]
        result = fn(texts)
        for i in range(len(result)):
            for j in range(i + 1, len(result)):
                assert result[i] != result[j], f"向量 {i} 与 {j} 不应相同"


# ============================================================
# get_embedding_function 单例缓存测试
# ============================================================

class TestGetEmbeddingFunction:
    """测试 get_embedding_function 工厂与单例缓存"""

    def test_same_model_name_returns_same_instance(self):
        """相同模型名返回同一实例（单例缓存）"""
        # 使用唯一模型名避免与其他测试污染
        model_name = "test-singleton-cache-model"
        try:
            fn1 = get_embedding_function(model_name)
            fn2 = get_embedding_function(model_name)
            assert fn1 is fn2
        finally:
            embedding_module._embedding_cache.pop(model_name, None)

    def test_different_model_names_return_different_instances(self):
        """不同模型名返回不同实例"""
        m1 = "test-different-model-a"
        m2 = "test-different-model-b"
        try:
            fn1 = get_embedding_function(m1)
            fn2 = get_embedding_function(m2)
            assert fn1 is not fn2
            assert fn1.model_name == m1
            assert fn2.model_name == m2
        finally:
            embedding_module._embedding_cache.pop(m1, None)
            embedding_module._embedding_cache.pop(m2, None)

    def test_default_model_name(self):
        """不传模型名时使用默认值"""
        model_name = "BAAI/bge-small-zh-v1.5"
        fn = get_embedding_function()
        assert fn.model_name == model_name

    def test_cached_instance_is_local_embedding_function(self):
        """缓存返回的是 LocalEmbeddingFunction 实例"""
        model_name = "test-type-check-model"
        try:
            fn = get_embedding_function(model_name)
            assert isinstance(fn, LocalEmbeddingFunction)
        finally:
            embedding_module._embedding_cache.pop(model_name, None)


# ============================================================
# 哈希降级开关测试（严格模式 vs 降级模式）
# ============================================================

class TestHashFallbackToggle:
    """测试 allow_hash_fallback 开关行为"""

    def test_strict_mode_raises_on_load_failure(self, monkeypatch):
        """allow_hash_fallback=False 时模型加载失败应抛异常（不静默降级）"""
        import sentence_transformers

        def _boom(*args, **kwargs):
            raise RuntimeError("model download failed")

        monkeypatch.setattr(sentence_transformers, "SentenceTransformer", _boom)
        fn = LocalEmbeddingFunction("test-strict-model", allow_hash_fallback=False)
        with pytest.raises(RuntimeError):
            fn(["文本"])

    def test_fallback_mode_uses_hash_when_enabled(self, monkeypatch):
        """allow_hash_fallback=True 时模型加载失败降级为哈希向量（256 维）"""
        import sentence_transformers

        def _boom(*args, **kwargs):
            raise RuntimeError("model download failed")

        monkeypatch.setattr(sentence_transformers, "SentenceTransformer", _boom)
        fn = LocalEmbeddingFunction("test-fallback-model", allow_hash_fallback=True)
        result = fn(["文本"])
        assert len(result) == 1
        assert len(result[0]) == 256

    def test_strict_mode_raises_on_import_error(self, monkeypatch):
        """sentence-transformers 未安装时，严格模式同样抛异常"""
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("No module named 'sentence_transformers'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        fn = LocalEmbeddingFunction("test-strict-import", allow_hash_fallback=False)
        with pytest.raises(RuntimeError):
            fn(["文本"])
