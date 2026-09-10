"""RAG 检索质量评测运行器。

加载检索黄金数据集，对每条用例执行「检索 → 生成 → 四指标裁判」，
输出含逐条分数与聚合均值的报告。与 ``eval/runner.py``（端到端断言）互补：
前者测「检索质量本身」，后者测「端到端对话行为」。

使用方式：
    from app.eval.rag_runner import RagEvalRunner

    runner = RagEvalRunner(vector_store, llm_factory, config)
    report = await runner.run_dataset("app/eval/datasets/rag_golden.json")
    print(report.to_markdown())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import AppConfig
from app.core.exceptions import EvaluationError
from app.core.logging import get_logger
from app.eval import ragas_metrics as ragas

logger = get_logger(__name__)

_ANSWER_SYSTEM = (
    "你是知识库问答助手。请仅依据给定的知识库上下文回答问题；"
    "若上下文不足以回答，请明确说明「知识库中暂无相关信息」。"
)


@dataclass
class RagEvalCase:
    """单条用例的评测结果。"""

    test_id: str
    query: str
    context_recall: float | None
    context_precision: float | None
    faithfulness: float | None
    answer_relevance: float | None
    retrieved_count: int = 0


@dataclass
class RagEvalReport:
    """RAG 评测报告。"""

    cases: list[RagEvalCase] = field(default_factory=list)
    aggregated: dict[str, float] = field(default_factory=dict)

    def to_markdown(self) -> str:
        lines = ["# RAG 检索质量评测报告", ""]
        if self.aggregated:
            lines.append("## 聚合均值")
            for name, v in self.aggregated.items():
                lines.append(f"- **{name}**: {v:.3f}")
            lines.append("")
        lines.append("## 逐条结果")
        lines.append("| 用例 | context_recall | context_precision | faithfulness | answer_relevance |")
        lines.append("|---|---|---|---|---|")
        for c in self.cases:
            lines.append(
                f"| {c.test_id} | {_fmt(c.context_recall)} | {_fmt(c.context_precision)} "
                f"| {_fmt(c.faithfulness)} | {_fmt(c.answer_relevance)} |"
            )
        return "\n".join(lines) + "\n"


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.3f}"


class RagEvalRunner:
    """检索质量评测运行器（检索 → 生成 → 四指标 LLM 裁判）。"""

    def __init__(
        self,
        vector_store: Any,
        llm_factory: Any,
        config: AppConfig | None = None,
        top_k: int = 5,
        judge_role: str = "ragas",
        answer_role: str = "executor",
    ) -> None:
        self.vector_store = vector_store
        self.llm_factory = llm_factory
        self.top_k = top_k
        self.judge_role = judge_role
        self.answer_role = answer_role
        if config is not None:
            ragas_cfg = getattr(config.evaluation, "ragas", None)
            if ragas_cfg is not None:
                self.top_k = getattr(ragas_cfg, "top_k", top_k) or top_k
                self.judge_role = getattr(ragas_cfg, "role", judge_role) or judge_role

    async def run_dataset(self, dataset_path: str) -> RagEvalReport:
        """运行黄金数据集，返回评测报告。"""
        test_cases = self._load_dataset(dataset_path)
        report = RagEvalReport()
        for case in test_cases:
            try:
                report.cases.append(await self._run_case(case))
            except Exception as e:  # noqa: BLE001 - 单条失败不阻断整体评测
                logger.error("RAG 用例执行失败", test_id=case.get("test_id"), error=str(e))
                report.cases.append(
                    RagEvalCase(
                        test_id=str(case.get("test_id", "unknown")),
                        query=str(case.get("query", "")),
                        context_recall=None,
                        context_precision=None,
                        faithfulness=None,
                        answer_relevance=None,
                    )
                )
        report.aggregated = self._aggregate(report.cases)
        return report

    async def _run_case(self, case: dict[str, Any]) -> RagEvalCase:
        test_id = str(case.get("test_id", "unknown"))
        query = str(case.get("query", ""))
        ground_truth = str(case.get("ground_truth", ""))
        if not query:
            raise EvaluationError(f"用例缺少 query 字段: {test_id}")

        # 1. 检索
        contexts: list[str] = []
        if self.vector_store is not None:
            results = await self.vector_store.search(query, top_k=self.top_k)
            contexts = [r.get("content", "") for r in results if r.get("content")]

        # 2. 生成答案（仅当有上下文或 LLM 可用）
        answer = await self._generate_answer(query, contexts)

        # 3. 四指标裁判
        recall = await ragas.context_recall(
            self.llm_factory, query, contexts, ground_truth, role=self.judge_role
        ) if ground_truth else None
        precision = await ragas.context_precision(
            self.llm_factory, query, contexts, role=self.judge_role
        ) if contexts else None
        faith = await ragas.faithfulness(
            self.llm_factory, query, contexts, answer, role=self.judge_role
        ) if answer else None
        relevance = await ragas.answer_relevance(
            self.llm_factory, query, answer, role=self.judge_role
        ) if answer else None

        return RagEvalCase(
            test_id=test_id,
            query=query,
            context_recall=recall,
            context_precision=precision,
            faithfulness=faith,
            answer_relevance=relevance,
            retrieved_count=len(contexts),
        )

    async def _generate_answer(self, query: str, contexts: list[str]) -> str:
        """用检索上下文生成答案；LLM 不可用或失败时返回空串。"""
        if self.llm_factory is None:
            return ""
        ctx_text = "\n\n".join(contexts) if contexts else "（无上下文）"
        try:
            resp = await self.llm_factory.ainvoke_with_stats(
                self.answer_role,
                [
                    SystemMessage(content=_ANSWER_SYSTEM),
                    HumanMessage(content=f"查询：{query}\n\n知识库上下文：\n{ctx_text}"),
                ],
            )
            return resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:  # noqa: BLE001
            logger.warning("RAG 答案生成失败", error=str(e))
            return ""

    @staticmethod
    def _aggregate(cases: list[RagEvalCase]) -> dict[str, float]:
        """对四个指标分别求均值（忽略 None）。"""
        names = ("context_recall", "context_precision", "faithfulness", "answer_relevance")
        out: dict[str, float] = {}
        for name in names:
            values = [getattr(c, name) for c in cases if getattr(c, name) is not None]
            if values:
                out[name] = round(sum(values) / len(values), 4)
        return out

    @staticmethod
    def _load_dataset(dataset_path: str) -> list[dict[str, Any]]:
        path = Path(dataset_path)
        if not path.exists():
            raise EvaluationError(f"数据集文件不存在: {dataset_path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise EvaluationError(f"数据集 JSON 解析失败: {e}") from e
        if not isinstance(data, list):
            raise EvaluationError("数据集应为 JSON 数组")
        return data
