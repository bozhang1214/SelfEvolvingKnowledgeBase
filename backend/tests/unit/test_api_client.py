"""
API 路由 TestClient 测试（Batch 2 测试工具集成）

用 FastAPI TestClient（httpx + ASGI transport）直接打真实路由，
验证：
- 知识库列表/搜索的请求参数透传与响应结构（mock AppContext）
- kb 未启用时的空结果降级
- 认证失败 401（mock get_current_user 抛 401）
- 白名单门禁 403（preview 用户被 require_full_access 拦截）

技术要点：
- 挂载真实 knowledge.router 到迷你 FastAPI app，避免启动完整应用
- 通过 dependency_overrides 替换 get_current_user / require_full_access
- 通过 monkeypatch 替换路由模块内的 get_app_context 引用
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from app.api.routes import knowledge as knowledge_route
from app.core.auth import get_current_user
from app.core.access import require_full_access
from app.memory.knowledge_entry import KnowledgeEntry


def _make_entry(content: str = "测试知识", entry_id: str = "e-1") -> KnowledgeEntry:
    """构造可被 _entry_to_dict 消费的 KnowledgeEntry"""
    return KnowledgeEntry(
        content=content,
        source="document",
        source_id="file.pdf",
        importance_score=0.8,
        entry_id=entry_id,
        topic="python",
    )


@pytest.fixture
def client():
    """迷你 FastAPI app：挂 knowledge 路由，默认放行认证与白名单"""
    app = FastAPI()
    app.include_router(knowledge_route.router)
    app.dependency_overrides[get_current_user] = lambda: "user-test-1"
    app.dependency_overrides[require_full_access] = lambda: None
    return TestClient(app)


def _patch_ctx(monkeypatch, kb=None):
    """把路由模块里的 get_app_context 指向 mock ctx"""
    ctx = MagicMock()
    ctx.knowledge_base = kb
    monkeypatch.setattr(knowledge_route, "get_app_context", lambda: ctx)
    return ctx


# ============================================================
# 知识列表
# ============================================================

class TestListKnowledge:
    def test_list_returns_entries_and_total(self, client, monkeypatch):
        kb = MagicMock()
        kb.list_entries = AsyncMock(return_value=[_make_entry(entry_id="e-1"), _make_entry(content="B", entry_id="e-2")])
        kb.count_entries = AsyncMock(return_value=2)
        _patch_ctx(monkeypatch, kb=kb)

        resp = client.get("/api/v1/knowledge?page=2&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["page"] == 2
        assert data["page_size"] == 5
        assert [e["entry_id"] for e in data["entries"]] == ["e-1", "e-2"]

        # 参数透传：分页换算成 offset/limit，且用户隔离
        kb.list_entries.assert_awaited_once()
        call_kwargs = kb.list_entries.await_args.kwargs
        assert call_kwargs["user_id"] == "user-test-1"
        assert call_kwargs["offset"] == 5  # (page-1)*page_size
        assert call_kwargs["limit"] == 5

    def test_list_kb_disabled_returns_empty(self, client, monkeypatch):
        _patch_ctx(monkeypatch, kb=None)
        resp = client.get("/api/v1/knowledge")
        assert resp.status_code == 200
        assert resp.json() == {"entries": [], "total": 0, "page": 1, "page_size": 20}

    def test_list_passes_category_and_source_filters(self, client, monkeypatch):
        kb = MagicMock()
        kb.list_entries = AsyncMock(return_value=[])
        kb.count_entries = AsyncMock(return_value=0)
        _patch_ctx(monkeypatch, kb=kb)

        resp = client.get(
            "/api/v1/knowledge?source=document&category_l1=AI&category_l2=LLM&category_l3=Agent"
        )
        assert resp.status_code == 200
        kwargs = kb.list_entries.await_args.kwargs
        assert kwargs["source"] == "document"
        assert kwargs["category_l1"] == "AI"
        assert kwargs["category_l2"] == "LLM"
        assert kwargs["category_l3"] == "Agent"


# ============================================================
# 知识搜索
# ============================================================

class TestSearchKnowledge:
    def test_search_passes_query_and_top_k(self, client, monkeypatch):
        kb = MagicMock()
        entry = _make_entry(content="RAG 原理", entry_id="s-1")
        entry.metadata["distance"] = 0.1
        kb.retrieve = AsyncMock(return_value=[entry])
        _patch_ctx(monkeypatch, kb=kb)

        resp = client.get("/api/v1/knowledge/search?q=RAG&top_k=3")
        assert resp.status_code == 200
        assert len(resp.json()["entries"]) == 1

        kwargs = kb.retrieve.await_args.kwargs
        assert kwargs["query"] == "RAG"
        assert kwargs["user_id"] == "user-test-1"
        assert kwargs["top_k"] == 3

    def test_search_requires_query(self, client, monkeypatch):
        _patch_ctx(monkeypatch, kb=None)
        resp = client.get("/api/v1/knowledge/search")  # 缺 q → 422
        assert resp.status_code == 422


# ============================================================
# 认证与白名单门禁
# ============================================================

class TestAuthAndAccessGate:
    def test_unauthorized_returns_401(self, client, monkeypatch):
        _patch_ctx(monkeypatch, kb=None)

        def denied():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未认证")

        client.app.dependency_overrides[get_current_user] = denied
        resp = client.get("/api/v1/knowledge")
        assert resp.status_code == 401

    def test_preview_user_blocked_by_access_gate(self, client, monkeypatch):
        _patch_ctx(monkeypatch, kb=None)

        def preview_denied():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="预览用户无此权限",
            )

        # 覆盖为「只允许 full access」的门禁行为
        client.app.dependency_overrides[require_full_access] = preview_denied
        resp = client.get("/api/v1/knowledge")
        assert resp.status_code == 403
