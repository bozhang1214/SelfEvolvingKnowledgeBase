"""
知识库自迭代引擎模块。

KnowledgeIngester 负责在对话结束后将对话中的有价值知识自动入库，
实现知识库的"自迭代"闭环：

    对话结束 → 重要性过滤 → LLM 提取事实 → 冲突检测 → 写入 L3 知识库

完整流程：
1. 重要性评分低于阈值 → 跳过，不入库
2. 使用 LLM（复用 scribe 角色）从对话中提取事实/结论
3. 冲突检测：检索相似条目
4. 根据冲突结果决定动作：
   - 无相似（< 0.80）  → 新增（inserted）
   - 高相似（> 0.95）  → 合并替换（merged，旧条目删除，新条目 supersedes 旧 ID）
   - 中相似（0.80~0.95）→ 并存（coexist，新旧条目都保留）
5. 写入 L3 长期知识库

设计要点：
- knowledge_base 为 None 时优雅降级（status="disabled"）
- LLM 调用使用 llm_factory.ainvoke_with_stats("scribe", messages)
- 重要性评分使用 config.memory.l3_knowledge.importance_threshold 作为阈值
- 冲突检测使用 kb.find_similar()
- 使用 structlog 结构化日志
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.core.config import AppConfig
from app.core.llm_factory import LLMFactory
from app.core.logging import get_logger
from app.memory.base import KnowledgeBaseBackend
from app.memory.knowledge_entry import KnowledgeEntry

# 匹配 ```json ... ``` 形式的代码块（与 BaseAgent 保持一致）
_JSON_CODE_BLOCK_PATTERN = re.compile(
    r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```",
    re.DOTALL,
)

# 事实提取 Prompt：从对话中提取可入库的事实性知识
FACT_EXTRACTION_PROMPT = ChatPromptTemplate.from_template("""
从以下对话中提取可入库的事实性知识。每条知识应为独立的、简洁的陈述。

用户问题：{user_input}
助手回答：{answer}

请提取 1-3 条关键事实，以 JSON 数组格式返回：
{{"facts": ["事实1", "事实2", "事实3"]}}

只提取有价值的、可复用的知识。如果对话不包含有价值的知识，返回空数组。
""")


@dataclass
class IngestResult:
    """
    知识入库结果。

    Attributes:
        status: 入库状态
            - "inserted": 新增入库
            - "merged": 与高相似度旧条目合并替换
            - "coexist": 与中相似度旧条目并存
            - "skipped": 因重要性不足或无事实而跳过
            - "disabled": L3 知识库未启用
        entry_id: 新写入条目的 ID（若有）
        merged_entry_id: 被合并（删除）的旧条目 ID，仅 status="merged" 时有值
        conflict_count: 检测到的冲突条目数
        importance_score: 经算法精炼后的最终重要性评分
        reason: 跳过或入库原因说明
    """
    status: str  # "inserted" | "merged" | "coexist" | "skipped" | "disabled"
    entry_id: str = ""
    merged_entry_id: str = ""  # 被合并的旧条目 ID
    conflict_count: int = 0
    importance_score: float = 0.0
    reason: str = ""


@dataclass
class ConflictResult:
    """
    冲突检测结果。

    Attributes:
        action: 冲突处理动作
            - "insert": 无冲突，新增
            - "replace": 高相似度，合并替换旧条目
            - "coexist": 中相似度，新旧并存
        old_entry: 检测到的最相似旧条目（None 表示无冲突）
    """
    action: str  # "insert" | "replace" | "coexist"
    old_entry: KnowledgeEntry | None = None


class KnowledgeIngester:
    """知识库自迭代引擎：从对话中提取知识并入库。"""

    def __init__(
        self,
        llm_factory: LLMFactory,
        config: AppConfig,
    ) -> None:
        """
        初始化知识入库器。

        Args:
            llm_factory: LLM 工厂实例，用于调用 scribe 角色提取事实
            config: 应用全局配置，从中读取 importance_threshold 等参数
        """
        self.llm_factory: LLMFactory = llm_factory
        self.config: AppConfig = config
        self.logger = get_logger(self.__class__.__name__)

    async def ingest_conversation(
        self,
        user_input: str,
        final_answer: str,
        summary: str,
        importance_score: float,
        conv_id: str,
        user_id: str,
        knowledge_base: KnowledgeBaseBackend | None,
    ) -> IngestResult:
        """
        将对话知识入库。

        流程：
        1. 重要性评分 < threshold → 跳过，不入库
        2. 从对话中提取事实/结论（LLM 调用）
        3. 冲突检测：检索相似条目
        4. 根据冲突结果决定：新增/合并/并存
        5. 写入知识库

        如果 knowledge_base 为 None，直接返回 skipped。

        Args:
            user_input: 用户原始输入
            final_answer: 助手最终回答
            summary: 对话摘要（来自 ScribeAgent）
            importance_score: ScribeAgent 评估的重要性评分（0.0~1.0）
            conv_id: 会话 ID（写入 source_id）
            user_id: 用户 ID（用于多用户隔离）
            knowledge_base: L3 知识库后端实例，None 表示未启用

        Returns:
            IngestResult: 入库结果
        """
        # 生成唯一流程追踪 ID，贯穿整个入库链路
        ingest_id = uuid.uuid4().hex[:8]
        task_name = asyncio.current_task().get_name() if asyncio.current_task() else "no-task"
        t_start = time.perf_counter()

        self.logger.info(
            "[INGEST-TRACE] ingest/start",
            ingest_id=ingest_id,
            conv_id=conv_id,
            user_id=user_id,
            task=task_name,
            raw_importance=importance_score,
        )

        # 1. 知识库未启用 → 优雅降级
        if knowledge_base is None:
            self.logger.info(
                "[INGEST-TRACE] ingest/disabled",
                ingest_id=ingest_id,
                conv_id=conv_id,
                user_id=user_id,
            )
            return IngestResult(
                status="disabled",
                reason="L3 知识库未启用",
            )

        # 2. 估算事实性信息（基于摘要非空作为启发式判断）
        # 此处先估算，后续 LLM 提取会做精确判断
        has_factual_info = bool(summary and summary.strip())
        refined_score = self._calculate_importance(
            importance_score=importance_score,
            summary=summary,
            has_factual_info=has_factual_info,
        )

        threshold = self.config.memory.l3_knowledge.importance_threshold
        if refined_score < threshold:
            self.logger.info(
                "[INGEST-TRACE] ingest/skipped_low_importance",
                ingest_id=ingest_id,
                conv_id=conv_id,
                importance_score=refined_score,
                threshold=threshold,
                latency_ms=int((time.perf_counter() - t_start) * 1000),
            )
            return IngestResult(
                status="skipped",
                importance_score=refined_score,
                reason=f"重要性评分 {refined_score:.3f} 低于阈值 {threshold}",
            )

        # 3. 提取事实/结论（LLM 调用）
        self.logger.info(
            "[INGEST-TRACE] extract_facts/start",
            ingest_id=ingest_id,
            conv_id=conv_id,
            task=task_name,
        )
        t_extract = time.perf_counter()
        try:
            facts = await self._extract_facts(user_input, final_answer)
        except Exception as e:
            self.logger.error(
                "[INGEST-TRACE] extract_facts/failed",
                ingest_id=ingest_id,
                conv_id=conv_id,
                error=str(e),
                latency_ms=int((time.perf_counter() - t_extract) * 1000),
                exc_info=True,
            )
            return IngestResult(
                status="skipped",
                importance_score=refined_score,
                reason=f"事实提取失败: {e}",
            )

        self.logger.info(
            "[INGEST-TRACE] extract_facts/done",
            ingest_id=ingest_id,
            conv_id=conv_id,
            facts_count=len(facts),
            latency_ms=int((time.perf_counter() - t_extract) * 1000),
        )

        if not facts:
            self.logger.info(
                "[INGEST-TRACE] ingest/skipped_no_facts",
                ingest_id=ingest_id,
                conv_id=conv_id,
                latency_ms=int((time.perf_counter() - t_start) * 1000),
            )
            return IngestResult(
                status="skipped",
                importance_score=refined_score,
                reason="未提取到有价值的事实",
            )

        # 4. 拼接事实为入库内容
        content = "\n".join(f"- {fact}" for fact in facts)

        # 5. 冲突检测
        self.logger.info(
            "[INGEST-TRACE] detect_conflicts/start",
            ingest_id=ingest_id,
            conv_id=conv_id,
            content_len=len(content),
        )
        t_conflict = time.perf_counter()
        try:
            conflict = await self._detect_conflicts(
                content, user_id, knowledge_base
            )
        except Exception as e:
            self.logger.error(
                "[INGEST-TRACE] detect_conflicts/failed",
                ingest_id=ingest_id,
                conv_id=conv_id,
                error=str(e),
                latency_ms=int((time.perf_counter() - t_conflict) * 1000),
                exc_info=True,
            )
            return IngestResult(
                status="skipped",
                importance_score=refined_score,
                reason=f"冲突检测失败: {e}",
            )

        self.logger.info(
            "[INGEST-TRACE] detect_conflicts/done",
            ingest_id=ingest_id,
            conv_id=conv_id,
            conflict_action=conflict.action,
            old_entry_id=conflict.old_entry.entry_id if conflict.old_entry else "",
            old_similarity=conflict.old_entry.similarity_score if conflict.old_entry else 0.0,
            latency_ms=int((time.perf_counter() - t_conflict) * 1000),
        )

        # 6. 根据冲突结果写入知识库
        t_write = time.perf_counter()
        try:
            if conflict.action == "replace" and conflict.old_entry is not None:
                old = conflict.old_entry
                self.logger.info(
                    "[INGEST-TRACE] merge/start",
                    ingest_id=ingest_id,
                    conv_id=conv_id,
                    old_entry_id=old.entry_id,
                    old_version=old.version,
                    old_similarity=old.similarity_score,
                )
                new_entry = KnowledgeEntry(
                    content=content,
                    source="conversation",
                    source_id=conv_id,
                    user_id=user_id,
                    importance_score=refined_score,
                    version=old.version + 1,
                    supersedes=old.entry_id,
                    metadata={"facts_count": len(facts)},
                )
                # 先插入新条目，再删除旧条目：降低数据丢失风险
                # 若 add 失败，旧条目仍在；若 delete 失败，如实返回 coexist
                new_id = await knowledge_base.add(new_entry)
                self.logger.info(
                    "[INGEST-TRACE] merge/add_done",
                    ingest_id=ingest_id,
                    conv_id=conv_id,
                    new_entry_id=new_id,
                    old_entry_id=old.entry_id,
                )
                delete_failed = False
                try:
                    await knowledge_base.delete(old.entry_id)
                except Exception as del_e:
                    delete_failed = True
                    self.logger.warning(
                        "[INGEST-TRACE] merge/delete_failed",
                        ingest_id=ingest_id,
                        old_entry_id=old.entry_id,
                        new_entry_id=new_id,
                        error=str(del_e),
                    )
                if delete_failed:
                    # delete 失败：新条目已入库，旧条目仍在，语义上就是并存
                    self.logger.info(
                        "[INGEST-TRACE] merge/degraded_to_coexist",
                        ingest_id=ingest_id,
                        new_entry_id=new_id,
                        old_entry_id=old.entry_id,
                        latency_ms=int((time.perf_counter() - t_write) * 1000),
                    )
                    return IngestResult(
                        status="coexist",
                        entry_id=new_id,
                        merged_entry_id=old.entry_id,
                        conflict_count=1,
                        importance_score=refined_score,
                        reason=(
                            f"与现有条目相似度 {old.similarity_score:.3f}，"
                            f"合并后删除旧条目失败，退化为并存"
                        ),
                    )
                self.logger.info(
                    "[INGEST-TRACE] merge/done",
                    ingest_id=ingest_id,
                    old_entry_id=old.entry_id,
                    new_entry_id=new_id,
                    similarity=old.similarity_score,
                    latency_ms=int((time.perf_counter() - t_write) * 1000),
                )
                return IngestResult(
                    status="merged",
                    entry_id=new_id,
                    merged_entry_id=old.entry_id,
                    conflict_count=1,
                    importance_score=refined_score,
                    reason=(
                        f"与现有条目相似度 {old.similarity_score:.3f} "
                        f"(>0.95)，已合并替换"
                    ),
                )

            if conflict.action == "coexist" and conflict.old_entry is not None:
                old = conflict.old_entry
                new_entry = KnowledgeEntry(
                    content=content,
                    source="conversation",
                    source_id=conv_id,
                    user_id=user_id,
                    importance_score=refined_score,
                    version=1,
                    supersedes=None,
                    metadata={
                        "coexists_with": old.entry_id,
                        "facts_count": len(facts),
                    },
                )
                new_id = await knowledge_base.add(new_entry)
                self.logger.info(
                    "[INGEST-TRACE] coexist/done",
                    ingest_id=ingest_id,
                    old_entry_id=old.entry_id,
                    new_entry_id=new_id,
                    similarity=old.similarity_score,
                    conv_id=conv_id,
                    latency_ms=int((time.perf_counter() - t_write) * 1000),
                )
                return IngestResult(
                    status="coexist",
                    entry_id=new_id,
                    merged_entry_id="",
                    conflict_count=1,
                    importance_score=refined_score,
                    reason=(
                        f"与现有条目相似度 {old.similarity_score:.3f} "
                        f"(0.80~0.95)，并存保留"
                    ),
                )

            # 默认：无冲突，新增
            new_entry = KnowledgeEntry(
                content=content,
                source="conversation",
                source_id=conv_id,
                user_id=user_id,
                importance_score=refined_score,
                version=1,
                supersedes=None,
                metadata={"facts_count": len(facts)},
            )
            new_id = await knowledge_base.add(new_entry)
            self.logger.info(
                "[INGEST-TRACE] insert/done",
                ingest_id=ingest_id,
                entry_id=new_id,
                conv_id=conv_id,
                fact_count=len(facts),
                latency_ms=int((time.perf_counter() - t_write) * 1000),
            )
            return IngestResult(
                status="inserted",
                entry_id=new_id,
                merged_entry_id="",
                conflict_count=0,
                importance_score=refined_score,
                reason="无冲突，新增入库",
            )
        except Exception as e:
            self.logger.error(
                "[INGEST-TRACE] write/failed",
                ingest_id=ingest_id,
                error=str(e),
                conv_id=conv_id,
                latency_ms=int((time.perf_counter() - t_write) * 1000),
                exc_info=True,
            )
            return IngestResult(
                status="skipped",
                importance_score=refined_score,
                reason=f"知识写入失败: {e}",
            )

    def _calculate_importance(
        self,
        importance_score: float,
        summary: str,
        has_factual_info: bool,
    ) -> float:
        """
        重要性评分算法（参考 Phase 2 设计文档）：

            score = 0.25 * factual_info_weight
                  + 0.20 * timeliness
                  + 0.55 * original_importance_score

        - factual_info_weight: 1.0 if 包含事实性信息 else 0.0
        - timeliness: 1.0（对话刚发生，时效性最强）
        - original_importance_score: ScribeAgent 给出的原始评分

        Args:
            importance_score: 原始重要性评分（0.0~1.0）
            summary: 对话摘要（用于启发式判断是否含事实）
            has_factual_info: 是否包含事实性信息

        Returns:
            精炼后的重要性评分，裁剪到 [0.0, 1.0] 区间
        """
        factual_info_weight = 1.0 if has_factual_info else 0.0
        # timeliness: 刚发生的对话时效性最强（1.0），后续可按时间衰减扩展
        # 当前 Phase 2 固定为 1.0，后续可基于 created_at 与当前时间差计算
        timeliness = 1.0
        score = (
            0.25 * factual_info_weight
            + 0.20 * timeliness
            + 0.55 * importance_score
        )
        return max(0.0, min(1.0, score))

    async def _extract_facts(self, user_input: str, answer: str) -> list[str]:
        """
        使用 LLM 从对话中提取事实/结论列表。

        复用 "scribe" 角色调用 LLM，使用 FACT_EXTRACTION_PROMPT
        引导模型返回 JSON 数组格式的事实列表。

        Args:
            user_input: 用户原始输入
            answer: 助手回答

        Returns:
            事实字符串列表（1~3 条），无价值时返回空列表
        """
        messages = FACT_EXTRACTION_PROMPT.format_messages(
            user_input=user_input,
            answer=answer,
        )
        response = await self.llm_factory.ainvoke_with_stats("scribe", messages)
        facts = self._parse_facts_response(response)
        self.logger.debug(
            "事实提取完成",
            fact_count=len(facts),
            user_input_preview=user_input[:80],
        )
        return facts

    async def _detect_conflicts(
        self,
        content: str,
        user_id: str,
        kb: KnowledgeBaseBackend,
    ) -> ConflictResult:
        """
        检测知识冲突（语义相似度 > 0.95 合并，0.80~0.95 并存）。

        使用 kb.find_similar() 检索相似条目，根据最相似条目的相似度
        决定处理动作：
            - similarity > 0.95      → action="replace"（合并替换）
            - 0.80 <= sim <= 0.95    → action="coexist"（并存）
            - similarity < 0.80      → action="insert"（新增，无冲突）

        Args:
            content: 待入库的知识内容
            user_id: 用户 ID（用于多用户隔离检索）
            kb: 知识库后端实例

        Returns:
            ConflictResult: 冲突检测结果
        """
        # find_similar 内部已按相似度降序返回，threshold=0.80 过滤低相似条目
        # 使用 getattr 兼容抽象接口未声明 find_similar 的情况
        find_similar = getattr(kb, "find_similar", None)
        if find_similar is not None:
            candidates = await find_similar(
                query=content,
                user_id=user_id,
                threshold=0.80,
                top_k=5,
            )
        else:
            # 降级：使用 retrieve，显式传 min_score=0.80 与主路径 threshold 对齐
            candidates = await kb.retrieve(
                query=content,
                user_id=user_id,
                top_k=5,
                min_score=0.80,
            )

        if not candidates:
            return ConflictResult(action="insert", old_entry=None)

        # 取相似度最高的条目（防御性 max 计算）
        top = max(candidates, key=lambda e: e.similarity_score)
        sim = top.similarity_score

        self.logger.debug(
            "冲突检测命中",
            top_entry_id=top.entry_id,
            similarity=sim,
            candidate_count=len(candidates),
        )

        if sim > 0.95:
            return ConflictResult(action="replace", old_entry=top)
        # 0.80 ~ 0.95：并存
        return ConflictResult(action="coexist", old_entry=top)

    def _parse_facts_response(self, response: Any) -> list[str]:
        """
        解析 LLM 返回的事实列表 JSON。

        容错处理 LLM 可能返回的格式：
        1. 纯 JSON 字符串
        2. ```json ... ``` 包裹的代码块
        3. 带前后说明文字的 JSON
        4. 非法 JSON（返回空列表）

        Args:
            response: LLM 响应对象（BaseMessage 或字符串）

        Returns:
            事实字符串列表
        """
        content = (
            response.content
            if hasattr(response, "content")
            else str(response)
        )
        if not isinstance(content, str):
            content = str(content)

        text = content.strip()

        data: dict | None = None

        # 1. 尝试直接解析
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                data = result
        except (json.JSONDecodeError, ValueError):
            pass

        # 2. 尝试从 ```json ... ``` 代码块提取
        if data is None:
            match = _JSON_CODE_BLOCK_PATTERN.search(text)
            if match:
                try:
                    result = json.loads(match.group(1))
                    if isinstance(result, dict):
                        data = result
                except (json.JSONDecodeError, ValueError):
                    pass

        # 3. 兜底：截取首个 { 到末个 } 之间的内容
        if data is None:
            first_brace = text.find("{")
            last_brace = text.rfind("}")
            if (
                first_brace != -1
                and last_brace != -1
                and last_brace > first_brace
            ):
                candidate = text[first_brace : last_brace + 1]
                try:
                    result = json.loads(candidate)
                    if isinstance(result, dict):
                        data = result
                except (json.JSONDecodeError, ValueError):
                    pass

        if data is None:
            self.logger.warning(
                "事实响应 JSON 解析失败，返回空列表",
                raw_preview=text[:200],
            )
            return []

        facts = data.get("facts", [])
        if not isinstance(facts, list):
            return []
        return [str(f).strip() for f in facts if str(f).strip()]
