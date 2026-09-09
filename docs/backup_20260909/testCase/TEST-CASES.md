# SEKB 测试用例汇总文档

> 项目：自迭代个人知识库 Agent（SEKB）—— 基于 LangGraph 的多智能体知识助手
> 文档版本：v1.0.0
> 最后更新：2026-08-19
> 维护者：SEKB Team
> 关联文档：[03-evaluation-and-testing.md](../03-evaluation-and-testing.md)、[01-architecture.md](../01-architecture.md)

---

## 目录

1. [概述](#1-概述)
2. [测试环境准备](#2-测试环境准备)
3. [运行测试](#3-运行测试)
4. [单元测试用例清单](#4-单元测试用例清单)
5. [集成测试用例清单](#5-集成测试用例清单)
6. [评估黄金数据集](#6-评估黄金数据集)
7. [Phase 3 验证脚本](#7-phase-3-验证脚本)
8. [测试 Fixtures 与 Mock 数据](#8-测试-fixtures-与-mock-数据)
9. [测试覆盖矩阵](#9-测试覆盖矩阵)
10. [自动化测试指南](#10-自动化测试指南)

---

## 1. 概述

### 1.1 测试金字塔架构

SEKB 项目采用经典测试金字塔架构，按测试粒度与运行环境分为三层：

```
            ┌─────────────┐
            │     E2E     │  ← 真实 API，端到端验收（少量，成本高）
            ├─────────────┤
            │ Integration │  ← 跨模块协作，不依赖真实 API（中等）
        ┌───┴─────────────┴───┐
        │      Unit Tests      │  ← 单模块隔离，最大量，最快
        └─────────────────────┘
```

### 1.2 测试分层说明

| 层级 | 标记 | 目录 | 是否依赖真实 API | 说明 |
|---|---|---|---|---|
| 单元测试 | `@pytest.mark.unit` | `backend/tests/unit/` | 否 | 单一模块/函数隔离测试，使用 Mock 替代外部依赖 |
| 集成测试 | `@pytest.mark.integration` | `backend/tests/integration/` | 否 | 多模块协作测试，验证模块间接口与数据流 |
| 端到端测试 | `@pytest.mark.e2e` | 通过 eval 流水线触发 | 是 | 调用真实 LLM 与搜索 API，验证端到端质量 |

### 1.3 测试规模概览

| 类型 | 文件数 | 测试类数 | 测试函数数 |
|---|---|---|---|
| 单元测试 | 15 | 50+ | 150+ |
| 集成测试 | 2 | 2 | 17 |
| 评估黄金数据集 | 1 | - | 10 |
| Phase 3 验证脚本 | 1 | - | 12 项检查 |
| **合计** | **19** | **52+** | **189+** |

---

## 2. 测试环境准备

### 2.1 系统要求

- **Python**：≥ 3.11（项目 `requires-python = ">=3.11"`）
- **操作系统**：macOS / Linux / Windows（CI 使用 ubuntu-latest）
- **Node.js**：≥ 20（仅前端构建需要）

### 2.2 依赖安装

所有测试依赖通过 `pyproject.toml` 的 `[project.optional-dependencies].dev` 声明：

```bash
# 进入后端目录
cd backend

# 安装项目及开发依赖（包含 pytest、pytest-asyncio、pytest-cov、respx、ruff、mypy）
pip install -e ".[dev]"

# 安装运行时依赖
pip install -r requirements.txt
```

### 2.3 测试依赖清单

| 依赖 | 版本要求 | 用途 |
|---|---|---|
| pytest | ≥ 8.0 | 测试框架 |
| pytest-asyncio | ≥ 0.23 | 异步测试支持（asyncio_mode = "auto"） |
| pytest-cov | ≥ 4.1 | 覆盖率统计 |
| respx | ≥ 0.21 | HTTP 请求 Mock |
| ruff | ≥ 0.5 | 代码静态检查 |
| mypy | ≥ 1.10 | 类型检查 |

### 2.4 环境变量配置

单元测试和集成测试**不依赖真实 API**，但需要设置占位环境变量以通过配置校验：

```bash
# 测试用占位环境变量（无需真实 Key）
export DEEPSEEK_API_KEY="test-key"
export BOCHA_API_KEY="test-key"
export JWT_SECRET="test-secret"
```

如需运行 e2e 评估测试，需配置真实的 API Key：

```bash
export DEEPSEEK_API_KEY="<真实 DeepSeek API Key>"
export BOCHA_API_KEY="<真实博查 API Key>"
export JWT_SECRET="<生产 JWT 密钥>"
```

### 2.5 pytest 配置

配置文件位于 `backend/pyproject.toml`：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "unit: 单元测试",
    "integration: 集成测试",
    "e2e: 端到端测试（需要真实 API）",
]
```

---

## 3. 运行测试

### 3.1 常用运行命令

```bash
# ============ 全部单元测试 + 集成测试 ============
cd backend
pytest tests/unit tests/integration -v

# ============ 带覆盖率报告 ============
pytest tests/unit tests/integration --cov=app --cov-report=term-missing

# 生成 XML 覆盖率报告（CI 使用）
pytest tests/unit tests/integration --cov=app --cov-report=xml --cov-report=term

# ============ 仅运行单元测试 ============
pytest tests/unit -v

# ============ 仅运行集成测试 ============
pytest tests/integration -v

# ============ 按标记筛选 ============
pytest -m unit -v          # 仅单元测试
pytest -m integration -v  # 仅集成测试
pytest -m e2e -v          # 仅端到端测试（需真实 API）

# ============ 指定测试文件 ============
pytest tests/unit/test_state.py -v

# ============ 指定测试类 ============
pytest tests/unit/test_assertion.py::TestAssertIntent -v

# ============ 指定单个测试函数 ============
pytest tests/unit/test_assertion.py::TestAssertIntent::test_intent_match_passes -v

# ============ 首次失败即停止 ============
pytest tests/unit -x -q

# ============ 评估黄金数据集（需要真实 API Key）============
sekb eval --dataset app/eval/datasets/golden_qa.json
# 或通过 Python 模块调用
python -m app.cli.main eval --dataset app/eval/datasets/golden_qa.json
```

### 3.2 代码质量检查命令

```bash
# Ruff 静态检查
ruff check backend/app/

# Mypy 类型检查（CI 中设为非阻断）
mypy backend/app/ --ignore-missing-imports
```

### 3.3 Phase 3 集成验证脚本

```bash
cd backend
python scripts/verify_phase3.py
```

---

## 4. 单元测试用例清单

> 所有单元测试位于 `backend/tests/unit/`，使用 Mock 替代真实 API 调用，**不需要真实 API Key**。

### 4.1 test_assertion.py — 断言引擎测试

> 路径：`backend/tests/unit/test_assertion.py`
> 模块：`app.eval.assertion`
> 测试类数：9 | 测试函数数：22

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-AS-01 | TestAssertIntent | test_intent_match_passes | 意图匹配通过 | 否 |
| UT-AS-02 | TestAssertIntent | test_intent_mismatch_fails | 意图不匹配失败 | 否 |
| UT-AS-03 | TestAssertIntentConfidence | test_confidence_above_min_passes | 置信度高于阈值通过 | 否 |
| UT-AS-04 | TestAssertIntentConfidence | test_confidence_below_min_fails | 置信度低于阈值失败 | 否 |
| UT-AS-05 | TestAssertIntentConfidence | test_confidence_equal_to_min_passes | 置信度等于阈值通过 | 否 |
| UT-AS-06 | TestAssertShouldNotCallTools | test_no_tools_called_passes | 未调用工具通过 | 否 |
| UT-AS-07 | TestAssertShouldNotCallTools | test_tools_called_fails | 调用工具失败 | 否 |
| UT-AS-08 | TestAssertShouldNotCallTools | test_should_not_call_tools_false_allows_tools | 标志为 False 时允许工具 | 否 |
| UT-AS-09 | TestAssertMaxReplan | test_replan_under_max_passes | 重规划次数低于上限通过 | 否 |
| UT-AS-10 | TestAssertMaxReplan | test_replan_over_max_fails | 重规划次数超上限失败 | 否 |
| UT-AS-11 | TestAssertMaxReplan | test_replan_equal_to_max_passes | 重规划次数等于上限通过 | 否 |
| UT-AS-12 | TestAssertShouldCallWebSearch | test_web_search_called_passes | 调用 web_search 通过 | 否 |
| UT-AS-13 | TestAssertShouldCallWebSearch | test_web_search_not_called_fails | 未调用 web_search 失败 | 否 |
| UT-AS-14 | TestAssertMinGroundedness | test_groundedness_above_min_passes | 锚定度高于阈值通过 | 否 |
| UT-AS-15 | TestAssertMinGroundedness | test_groundedness_below_min_fails | 锚定度低于阈值失败 | 否 |
| UT-AS-16 | TestCheckBatch | test_batch_all_pass | 批量全部通过 | 否 |
| UT-AS-17 | TestCheckBatch | test_batch_mixed_results | 批量混合结果 | 否 |
| UT-AS-18 | TestCheckBatch | test_batch_length_mismatch_raises | 批量长度不一致抛异常 | 否 |
| UT-AS-19 | TestCheckBatch | test_batch_empty | 批量空列表处理 | 否 |
| UT-AS-20 | TestCheckBatch | test_batch_preserves_order | 批量结果保持顺序 | 否 |
| UT-AS-21 | TestAssertionResult | test_result_has_test_id | 结果包含 test_id | 否 |
| UT-AS-22 | TestAssertionResult | test_result_default_test_id | 默认 test_id | 否 |
| UT-AS-23 | TestAssertionResult | test_result_contains_metric_levels | 结果包含指标等级 | 否 |
| UT-AS-24 | TestAssertionResult | test_result_error_none_when_passed | 通过时 error 为 None | 否 |

### 4.2 test_eval_metrics.py — 评估指标测试

> 路径：`backend/tests/unit/test_eval_metrics.py`
> 模块：`app.eval.metrics`
> 测试类数：4 | 测试函数数：26

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-EM-01 | TestFromState | test_extract_from_metrics_field | 从 metrics 字段提取指标 | 否 |
| UT-EM-02 | TestFromState | test_extract_from_other_fields_when_metrics_empty | metrics 为空时从其他字段提取 | 否 |
| UT-EM-03 | TestFromState | test_tool_success_rate_from_tool_calls | 从 tool_calls 计算工具成功率 | 否 |
| UT-EM-04 | TestFromState | test_tool_success_rate_no_tool_calls | 无工具调用时成功率默认值 | 否 |
| UT-EM-05 | TestFromState | test_non_dict_state_raises | 非 dict 状态抛异常 | 否 |
| UT-EM-06 | TestFromState | test_default_values_for_empty_state | 空状态默认值 | 否 |
| UT-EM-07 | TestEvaluateThreshold | test_higher_is_better_good | higher_is_better 达到 good | 否 |
| UT-EM-08 | TestEvaluateThreshold | test_higher_is_better_warn | higher_is_better 达到 warn | 否 |
| UT-EM-09 | TestEvaluateThreshold | test_higher_is_better_bad | higher_is_better 达到 bad | 否 |
| UT-EM-10 | TestEvaluateThreshold | test_lower_is_better_good | lower_is_better 达到 good | 否 |
| UT-EM-11 | TestEvaluateThreshold | test_lower_is_better_warn | lower_is_better 达到 warn | 否 |
| UT-EM-12 | TestEvaluateThreshold | test_lower_is_better_bad | lower_is_better 达到 bad | 否 |
| UT-EM-13 | TestEvaluateThreshold | test_none_threshold_returns_good | 无阈值返回 good | 否 |
| UT-EM-14 | TestEvaluateThreshold | test_dict_threshold | 字典形式阈值 | 否 |
| UT-EM-15 | TestEvaluateThreshold | test_value_equal_to_good_is_good | 值等于 good 阈值时为 good | 否 |
| UT-EM-16 | TestEvaluateThreshold | test_value_equal_to_warn_is_warn | 值等于 warn 阈值时为 warn | 否 |
| UT-EM-17 | TestEvaluateThreshold | test_invalid_threshold_type_raises | 无效阈值类型抛异常 | 否 |
| UT-EM-18 | TestToJsonl | test_valid_json_output | 有效 JSON 输出 | 否 |
| UT-EM-19 | TestToJsonl | test_single_line_output | 单行输出 | 否 |
| UT-EM-20 | TestToJsonl | test_preserves_chinese | 保留中文字符 | 否 |
| UT-EM-21 | TestToJsonl | test_empty_metrics | 空 metrics 处理 | 否 |
| UT-EM-22 | TestAggregate | test_empty_list_returns_empty | 空列表聚合返回空 | 否 |
| UT-EM-23 | TestAggregate | test_mean_calculation | 均值计算 | 否 |
| UT-EM-24 | TestAggregate | test_median_calculation | 中位数计算 | 否 |
| UT-EM-25 | TestAggregate | test_p95_calculation | P95 计算 | 否 |
| UT-EM-26 | TestAggregate | test_single_item | 单项聚合 | 否 |
| UT-EM-27 | TestAggregate | test_multiple_fields | 多字段聚合 | 否 |
| UT-EM-28 | TestAggregate | test_skips_non_numeric_fields | 跳过非数值字段 | 否 |
| UT-EM-29 | TestAggregate | test_skips_missing_fields | 跳过缺失字段 | 否 |

### 4.3 test_p0_fixes.py — P0 修复验证测试

> 路径：`backend/tests/unit/test_p0_fixes.py`
> 模块：多模块 P0 修复回归
> 测试类数：12 | 测试函数数：18+

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-P0-01 | TestSEKBMemoErrorNaming | test_sekb_memo_error_exists | SEKBMemoError 存在 | 否 |
| UT-P0-02 | TestSEKBMemoErrorNaming | test_builtin_memory_error_not_shadowed | 内置 MemoryError 未被遮蔽 | 否 |
| UT-P0-03 | TestSEKBMemoErrorNaming | test_sekb_memo_error_catchable_as_sekb_error | SEKBMemoError 可作为 SEKBError 捕获 | 否 |
| UT-P0-04 | TestPerRequestStatsIsolation | （多请求统计隔离） | 多请求间统计数据隔离 | 否 |
| UT-P0-05 | TestLLMRetryAndResponseInit | （LLM 重试和响应初始化） | LLM 重试机制与响应初始化 | 否 |
| UT-P0-06 | TestWorkflowExceptionFallback | （工作流异常回退） | 工作流异常时优雅回退 | 否 |
| UT-P0-07 | TestE2ELatencyBackfill | （端到端延迟回填） | 端到端延迟指标回填 | 否 |
| UT-P0-08 | TestDeadCodeCleanup | test_chat_simple_node_no_llm_factory_get | chat_simple 节点不调用 llm_factory.get | 否 |
| UT-P0-09 | TestDeadCodeCleanup | test_clarify_node_no_llm_factory_get | clarify 节点不调用 llm_factory.get | 否 |
| UT-P0-10 | TestApiKeyValidator | test_valid_api_key_passes | 有效 API Key 通过 | 否 |
| UT-P0-11 | TestApiKeyValidator | test_empty_api_key_rejected | 空 API Key 被拒 | 否 |
| UT-P0-12 | TestApiKeyValidator | test_unexpanded_env_var_rejected | 未展开的环境变量被拒 | 否 |
| UT-P0-13 | TestApiKeyValidator | test_env_var_resolved_passes | 已解析的环境变量通过 | 否 |
| UT-P0-14 | TestCreateAppLazy | test_import_server_does_not_create_app | 导入 server 不创建 app 实例 | 否 |
| UT-P0-15 | TestCreateAppLazy | test_get_app_creates_instance | get_app 创建实例 | 否 |
| UT-P0-16 | TestCreateAppLazy | test_reset_app_clears_instance | reset_app 清除实例 | 否 |
| UT-P0-17 | TestRetryDegradationVisibility | test_llm_record_contains_retry_fields | LLM 记录包含重试字段 | 否 |
| UT-P0-18 | TestRetryDegradationVisibility | test_stats_has_retry_and_degradation_rate | 统计包含重试与降级率 | 否 |
| UT-P0-19 | TestRetryDegradationVisibility | test_metrics_includes_quality_fields | 指标包含质量字段 | 否 |
| UT-P0-20 | TestSafeFloatConversion | （安全浮点转换） | 非数值字段安全转换 | 否 |
| UT-P0-21 | TestCompressAtomicity | （压缩原子性） | L1 记忆压缩原子性 | 否 |
| UT-P0-22 | TestPersistedRetryDegradation | （持久化重试降级） | 重试降级数据持久化 | 否 |

### 4.4 test_embedding.py — Embedding 函数测试

> 路径：`backend/tests/unit/test_embedding.py`
> 模块：`app.core.embedding`
> 测试类数：5 | 测试函数数：18

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-EMB-01 | TestLocalEmbeddingFunctionCreation | test_create_with_default_model | 默认模型创建 | 否 |
| UT-EMB-02 | TestLocalEmbeddingFunctionCreation | test_create_with_custom_model | 自定义模型创建 | 否 |
| UT-EMB-03 | TestEmbeddingCall | test_returns_list_of_vectors | 返回向量列表 | 否 |
| UT-EMB-04 | TestEmbeddingCall | test_hash_vector_dimension_is_256 | hash 向量维度为 256 | 否 |
| UT-EMB-05 | TestEmbeddingCall | test_empty_input_returns_empty_list | 空输入返回空列表 | 否 |
| UT-EMB-06 | TestEmbeddingCall | test_single_text_returns_single_vector | 单文本返回单向量 | 否 |
| UT-EMB-07 | TestEmbeddingCall | test_multiple_texts_return_matching_count | 多文本返回匹配数量 | 否 |
| UT-EMB-08 | TestHashVectorNormalization | test_vector_is_normalized | 向量已归一化 | 否 |
| UT-EMB-09 | TestHashVectorNormalization | test_multiple_vectors_all_normalized | 多向量均归一化 | 否 |
| UT-EMB-10 | TestHashVectorNormalization | test_hash_embedding_static_method_normalized | 静态方法归一化 | 否 |
| UT-EMB-11 | TestHashVectorNormalization | test_custom_dimension_via_static_method | 自定义维度静态方法 | 否 |
| UT-EMB-12 | TestDistinctTextVectors | test_different_texts_different_vectors | 不同文本生成不同向量 | 否 |
| UT-EMB-13 | TestDistinctTextVectors | test_same_text_same_vector | 相同文本生成相同向量 | 否 |
| UT-EMB-14 | TestDistinctTextVectors | test_different_texts_in_batch_distinct | 批量内不同文本向量不同 | 否 |
| UT-EMB-15 | TestGetEmbeddingFunction | test_same_model_name_returns_same_instance | 相同模型名返回同一实例 | 否 |
| UT-EMB-16 | TestGetEmbeddingFunction | test_different_model_names_return_different_instances | 不同模型名返回不同实例 | 否 |
| UT-EMB-17 | TestGetEmbeddingFunction | test_default_model_name | 默认模型名 | 否 |
| UT-EMB-18 | TestGetEmbeddingFunction | test_cached_instance_is_local_embedding_function | 缓存实例为 LocalEmbeddingFunction | 否 |

### 4.5 test_rag_retriever.py — RAG 检索器测试

> 路径：`backend/tests/unit/test_rag_retriever.py`
> 模块：`app.tools.rag.retriever`
> 测试类数：6 | 测试函数数：28

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-RAG-01 | TestRAGRetrieverCreation | test_create_with_defaults | 默认配置创建 | 否 |
| UT-RAG-02 | TestRAGRetrieverCreation | test_create_with_custom_config | 自定义配置创建 | 否 |
| UT-RAG-03 | TestRAGRetrieverCreation | test_create_with_none_vector_store | 向量库为 None 创建 | 否 |
| UT-RAG-04 | TestRAGRetrieverCreation | test_auxiliary_top_k_minimum_is_two | auxiliary top_k 最小为 2 | 否 |
| UT-RAG-05 | TestIntentRouting | test_chitchat_skips_rag | chitchat 意图跳过 RAG | 否 |
| UT-RAG-06 | TestIntentRouting | test_chat_simple_skips_rag | chat_simple 意图跳过 RAG | 否 |
| UT-RAG-07 | TestIntentRouting | test_none_vector_store_returns_disabled | 向量库为 None 返回禁用 | 否 |
| UT-RAG-08 | TestIntentRouting | test_kb_strict_returns_strict_mode | kb_strict 意图返回 strict 模式 | 否 |
| UT-RAG-09 | TestIntentRouting | test_kb_prefer_returns_prefer_mode | kb_prefer 意图返回 prefer 模式 | 否 |
| UT-RAG-10 | TestIntentRouting | test_web_default_returns_auxiliary_mode | web_default 意图返回 auxiliary 模式 | 否 |
| UT-RAG-11 | TestIntentRouting | test_task_plan_returns_auxiliary_mode | task_plan 意图返回 auxiliary 模式 | 否 |
| UT-RAG-12 | TestIntentRouting | test_unknown_intent_skips_rag | 未知意图跳过 RAG | 否 |
| UT-RAG-13 | TestFallbackMessage | test_strict_empty_results_fallback | strict 模式空结果回退 | 否 |
| UT-RAG-14 | TestFallbackMessage | test_prefer_empty_results_fallback | prefer 模式空结果回退 | 否 |
| UT-RAG-15 | TestFallbackMessage | test_auxiliary_empty_results_no_fallback | auxiliary 模式空结果无回退 | 否 |
| UT-RAG-16 | TestFallbackMessage | test_strict_with_results_no_fallback | strict 模式有结果无回退 | 否 |
| UT-RAG-17 | TestImportanceFilter | test_filters_low_importance_entries | 过滤低重要性条目 | 否 |
| UT-RAG-18 | TestImportanceFilter | test_no_filter_when_threshold_zero | 阈值为 0 时不过滤 | 否 |
| UT-RAG-19 | TestLatencyTracking | test_latency_ms_is_non_negative | 延迟非负 | 否 |
| UT-RAG-20 | TestFormatRagContext | test_empty_results_returns_empty_string | 空结果返回空字符串 | 否 |
| UT-RAG-21 | TestFormatRagContext | test_none_results_returns_empty_string | None 结果返回空字符串 | 否 |
| UT-RAG-22 | TestFormatRagContext | test_single_result_format | 单结果格式化 | 否 |
| UT-RAG-23 | TestFormatRagContext | test_multiple_results_indexed | 多结果带索引格式化 | 否 |
| UT-RAG-24 | TestFormatRagContext | test_unknown_source_label_passthrough | 未知 source 标签透传 | 否 |
| UT-RAG-25 | TestFormatRagContext | test_importance_formatted_two_decimals | 重要性保留两位小数 | 否 |
| UT-RAG-26 | TestFormatRagContext | test_empty_content_still_included | 空内容仍包含 | 否 |
| UT-RAG-27 | TestRAGResult | test_default_values | RAGResult 默认值 | 否 |
| UT-RAG-28 | TestRAGResult | test_with_context | RAGResult 带上下文 | 否 |

### 4.6 test_state.py — GraphState 测试

> 路径：`backend/tests/unit/test_state.py`
> 模块：`app.graph.state`
> 测试类数：4 | 测试函数数：30

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-ST-01 | TestCreateInitialState | test_returns_correct_user_input | 返回正确的 user_input | 否 |
| UT-ST-02 | TestCreateInitialState | test_returns_correct_conversation_id | 返回正确的 conversation_id | 否 |
| UT-ST-03 | TestCreateInitialState | test_default_user_id | 默认 user_id | 否 |
| UT-ST-04 | TestCreateInitialState | test_custom_user_id | 自定义 user_id | 否 |
| UT-ST-05 | TestCreateInitialState | test_auto_generated_trace_id | 自动生成 trace_id | 否 |
| UT-ST-06 | TestCreateInitialState | test_custom_trace_id | 自定义 trace_id | 否 |
| UT-ST-07 | TestCreateInitialState | test_trace_id_unique | trace_id 唯一性 | 否 |
| UT-ST-08 | TestCreateInitialState | test_initial_collections_empty | 初始集合为空 | 否 |
| UT-ST-09 | TestCreateInitialState | test_initial_defaults | 初始默认值 | 否 |
| UT-ST-10 | TestCreateInitialState | test_custom_history | 自定义历史 | 否 |
| UT-ST-11 | TestCreateInitialState | test_returns_graph_state_type | 返回 GraphState 类型 | 否 |
| UT-ST-12 | TestIntentType | test_chitchat_value | chitchat 意图值 | 否 |
| UT-ST-13 | TestIntentType | test_kb_strict_value | kb_strict 意图值 | 否 |
| UT-ST-14 | TestIntentType | test_kb_prefer_value | kb_prefer 意图值 | 否 |
| UT-ST-15 | TestIntentType | test_web_default_value | web_default 意图值 | 否 |
| UT-ST-16 | TestIntentType | test_task_plan_value | task_plan 意图值 | 否 |
| UT-ST-17 | TestIntentType | test_clarify_value | clarify 意图值 | 否 |
| UT-ST-18 | TestIntentType | test_all_intents_count | 全部意图数量 | 否 |
| UT-ST-19 | TestIntentType | test_intent_is_string_enum | 意图为字符串枚举 | 否 |
| UT-ST-20 | TestTaskStatus | test_pending_value | pending 状态值 | 否 |
| UT-ST-21 | TestTaskStatus | test_in_progress_value | in_progress 状态值 | 否 |
| UT-ST-22 | TestTaskStatus | test_done_value | done 状态值 | 否 |
| UT-ST-23 | TestTaskStatus | test_failed_value | failed 状态值 | 否 |
| UT-ST-24 | TestTaskStatus | test_skipped_value | skipped 状态值 | 否 |
| UT-ST-25 | TestTaskStatus | test_all_statuses_count | 全部状态数量 | 否 |
| UT-ST-26 | TestReflectionResult | test_pass_value | pass 结果值 | 否 |
| UT-ST-27 | TestReflectionResult | test_needs_replan_value | needs_replan 结果值 | 否 |
| UT-ST-28 | TestReflectionResult | test_needs_rewrite_value | needs_rewrite 结果值 | 否 |
| UT-ST-29 | TestReflectionResult | test_all_results_count | 全部结果数量 | 否 |

### 4.7 test_config.py — 配置加载测试

> 路径：`backend/tests/unit/test_config.py`
> 模块：`app.core.config`
> 测试类数：3 | 测试函数数：17

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-CF-01 | TestExpandEnvVars | test_expand_simple_var | 展开简单环境变量 | 否 |
| UT-CF-02 | TestExpandEnvVars | test_expand_unset_var_to_empty | 未设置变量展开为空 | 否 |
| UT-CF-03 | TestExpandEnvVars | test_expand_no_vars | 无变量字符串 | 否 |
| UT-CF-04 | TestExpandEnvVars | test_expand_nested_dict | 嵌套字典展开 | 否 |
| UT-CF-05 | TestExpandEnvVars | test_expand_list | 列表展开 | 否 |
| UT-CF-06 | TestExpandEnvVars | test_expand_non_string_types | 非字符串类型 | 否 |
| UT-CF-07 | TestExpandEnvVars | test_expand_multiple_vars_in_one_string | 单字符串多变量展开 | 否 |
| UT-CF-08 | TestLoadConfig | test_load_valid_config | 加载有效配置 | 否 |
| UT-CF-09 | TestLoadConfig | test_load_config_file_not_found | 文件不存在抛异常 | 否 |
| UT-CF-10 | TestLoadConfig | test_load_config_invalid_yaml | 无效 YAML 抛异常 | 否 |
| UT-CF-11 | TestConfigValidation | test_max_tokens_must_be_less_than_hard_limit | max_tokens 须小于 hard_limit | 否 |
| UT-CF-12 | TestConfigValidation | test_l1_must_be_enabled | L1 必须启用 | 否 |
| UT-CF-13 | TestConfigValidation | test_invalid_reflection_policy | 无效反思策略抛异常 | 否 |
| UT-CF-14 | TestConfigValidation | test_api_key_empty_raises_error | 空 API Key 抛异常 | 否 |
| UT-CF-15 | TestConfigValidation | test_api_key_unset_env_var_raises_error | 未设置环境变量抛异常 | 否 |

### 4.8 test_file_processor.py — 文件解析处理器测试

> 路径：`backend/tests/unit/test_file_processor.py`
> 模块：`app.tools.file_processor`
> 测试类数：6 | 测试函数数：22

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-FP-01 | TestChunkTextBasic | test_empty_text_returns_empty_list | 空文本返回空列表 | 否 |
| UT-FP-02 | TestChunkTextBasic | test_whitespace_only_text_returns_empty_list | 纯空白文本返回空列表 | 否 |
| UT-FP-03 | TestChunkTextBasic | test_short_text_returns_single_chunk | 短文本返回单块 | 否 |
| UT-FP-04 | TestChunkTextBasic | test_paragraph_splitting | 段落分割 | 否 |
| UT-FP-05 | TestChunkTextBasic | test_multiple_paragraphs_split_when_exceeding_chunk_size | 多段落超限分割 | 否 |
| UT-FP-06 | TestChunkTextBasic | test_sentence_splitting | 句子分割 | 否 |
| UT-FP-07 | TestChunkTextBasic | test_hard_cut_for_long_sentence | 长句硬切分 | 否 |
| UT-FP-08 | TestChunkTextBasic | test_chinese_sentence_end_punctuation | 中文句末标点 | 否 |
| UT-FP-09 | TestChunkTextOverlap | test_overlap_creates_context_overlap | overlap 创建上下文重叠 | 否 |
| UT-FP-10 | TestChunkTextOverlap | test_zero_overlap_no_overlap | overlap=0 无重叠 | 否 |
| UT-FP-11 | TestChunkTextOverlap | test_overlap_zero_vs_positive_total_length | overlap 0 与正值的总长度对比 | 否 |
| UT-FP-12 | TestChunkTextParameterValidation | test_chunk_size_zero_raises_value_error | chunk_size=0 抛 ValueError | 否 |
| UT-FP-13 | TestChunkTextParameterValidation | test_chunk_size_negative_raises_value_error | chunk_size 负值抛 ValueError | 否 |
| UT-FP-14 | TestChunkTextParameterValidation | test_overlap_negative_raises_value_error | overlap 负值抛 ValueError | 否 |
| UT-FP-15 | TestChunkTextParameterValidation | test_overlap_equal_to_chunk_size_raises_value_error | overlap 等于 chunk_size 抛异常 | 否 |
| UT-FP-16 | TestChunkTextParameterValidation | test_overlap_greater_than_chunk_size_raises_value_error | overlap 大于 chunk_size 抛异常 | 否 |
| UT-FP-17 | TestChunkTextParameterValidation | test_overlap_just_below_chunk_size_is_valid | overlap 略小于 chunk_size 有效 | 否 |
| UT-FP-18 | TestFileProcessorCreation | test_default_supported_extensions | 默认支持扩展名 | 否 |
| UT-FP-19 | TestFileProcessorCreation | test_custom_supported_extensions | 自定义支持扩展名 | 否 |
| UT-FP-20 | TestFileProcessorCreation | test_extensions_normalized_to_lowercase | 扩展名归一为小写 | 否 |
| UT-FP-21 | TestProcessResult | test_success_result_fields | 成功结果字段 | 否 |
| UT-FP-22 | TestProcessResult | test_error_result_has_error_message | 错误结果含错误消息 | 否 |
| UT-FP-23 | TestProcessResult | test_error_default_empty | 错误默认为空 | 否 |

> 注：TestParseFile 与 TestProcessFile 为类级别测试组，包含多个子测试场景。

### 4.9 test_graph_routing.py — Graph 路由测试

> 路径：`backend/tests/unit/test_graph_routing.py`
> 模块：`app.graph.builder`
> 测试类数：2 | 测试函数数：16

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-GR-01 | TestRouteAfterSupervisor | test_chitchat_routes_to_chat_simple | chitchat 路由到 chat_simple | 否 |
| UT-GR-02 | TestRouteAfterSupervisor | test_clarify_routes_to_clarify | clarify 路由到 clarify | 否 |
| UT-GR-03 | TestRouteAfterSupervisor | test_needs_clarification_routes_to_clarify | 需澄清路由到 clarify | 否 |
| UT-GR-04 | TestRouteAfterSupervisor | test_web_default_routes_to_planner | web_default 路由到 planner | 否 |
| UT-GR-05 | TestRouteAfterSupervisor | test_kb_strict_routes_to_planner | kb_strict 路由到 planner | 否 |
| UT-GR-06 | TestRouteAfterSupervisor | test_kb_prefer_routes_to_planner | kb_prefer 路由到 planner | 否 |
| UT-GR-07 | TestRouteAfterSupervisor | test_task_plan_routes_to_planner | task_plan 路由到 planner | 否 |
| UT-GR-08 | TestRouteAfterSupervisor | test_empty_intent_routes_to_planner | 空 intent 路由到 planner | 否 |
| UT-GR-09 | TestRouteAfterSupervisor | test_needs_clarification_takes_priority_over_chitchat | 澄清优先于 chitchat | 否 |
| UT-GR-10 | TestRouteAfterCritic | test_should_replan_true_routes_to_planner | 需重规划路由到 planner | 否 |
| UT-GR-11 | TestRouteAfterCritic | test_should_replan_false_routes_to_scribe | 无需重规划路由到 scribe | 否 |
| UT-GR-12 | TestRouteAfterCritic | test_replan_limit_reached_routes_to_scribe | 重规划到上限路由到 scribe | 否 |
| UT-GR-13 | TestRouteAfterCritic | test_needs_rewrite_routes_to_scribe | 需重写路由到 scribe | 否 |
| UT-GR-14 | TestRouteAfterCritic | test_mock_strategy_replan_true | Mock 策略返回需重规划 | 否 |
| UT-GR-15 | TestRouteAfterCritic | test_mock_strategy_replan_false | Mock 策略返回无需重规划 | 否 |

### 4.10 test_knowledge_entry.py — 知识条目模型测试

> 路径：`backend/tests/unit/test_knowledge_entry.py`
> 模块：`app.memory.knowledge_entry`
> 测试类数：5 | 测试函数数：23

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-KE-01 | TestKnowledgeEntryCreation | test_create_with_defaults | 默认值创建 | 否 |
| UT-KE-02 | TestKnowledgeEntryCreation | test_create_with_custom_values | 自定义值创建 | 否 |
| UT-KE-03 | TestKnowledgeEntryCreation | test_create_generates_unique_entry_ids | 生成唯一 entry_id | 否 |
| UT-KE-04 | TestKnowledgeEntryCreation | test_supersedes_none_by_default | supersedes 默认为 None | 否 |
| UT-KE-05 | TestKnowledgeEntryCreation | test_supersedes_with_value | supersedes 带值 | 否 |
| UT-KE-06 | TestToChromaMetadata | test_contains_all_expected_fields | 包含所有预期字段 | 否 |
| UT-KE-07 | TestToChromaMetadata | test_field_values_match_entry | 字段值匹配条目 | 否 |
| UT-KE-08 | TestToChromaMetadata | test_supersedes_none_becomes_empty_string | supersedes None 转空字符串 | 否 |
| UT-KE-09 | TestToChromaMetadata | test_supersedes_with_value_preserved | supersedes 带值保留 | 否 |
| UT-KE-10 | TestToChromaMetadata | test_does_not_include_content_or_entry_id | 不包含 content/entry_id | 否 |
| UT-KE-11 | TestFromChromaRecord | test_build_with_full_metadata | 完整 metadata 构建 | 否 |
| UT-KE-12 | TestFromChromaRecord | test_build_with_empty_supersedes | 空 supersedes 构建 | 否 |
| UT-KE-13 | TestFromChromaRecord | test_build_with_missing_supersedes_key | 缺失 supersedes 键 | 否 |
| UT-KE-14 | TestFromChromaRecord | test_build_with_missing_keys_uses_defaults | 缺失键使用默认值 | 否 |
| UT-KE-15 | TestFromChromaRecord | test_distance_stored_in_metadata | distance 存入 metadata | 否 |
| UT-KE-16 | TestFromChromaRecord | test_default_distance_is_zero | 默认 distance 为 0 | 否 |
| UT-KE-17 | TestFromChromaRecord | test_extra_metadata_preserved | 额外 metadata 保留 | 否 |
| UT-KE-18 | TestFromChromaRecord | test_round_trip | 往返转换一致 | 否 |
| UT-KE-19 | TestSimilarityScore | test_distance_zero_returns_one | distance=0 返回 1 | 否 |
| UT-KE-20 | TestSimilarityScore | test_distance_within_range | distance 在范围内 | 否 |
| UT-KE-21 | TestSimilarityScore | test_distance_one_returns_zero | distance=1 返回 0 | 否 |
| UT-KE-22 | TestSimilarityScore | test_distance_above_one_clamped_to_zero | distance>1 截断为 0 | 否 |
| UT-KE-23 | TestSimilarityScore | test_no_distance_defaults_to_zero | 无 distance 默认为 0 | 否 |
| UT-KE-24 | TestSimilarityScore | test_from_chroma_record_similarity | 从 chroma 记录计算相似度 | 否 |

### 4.11 test_knowledge_ingestor.py — 知识自迭代引擎测试

> 路径：`backend/tests/unit/test_knowledge_ingestor.py`
> 模块：`app.agents.knowledge_ingestor`
> 测试类数：7 | 测试函数数：25+

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-KI-01 | TestKnowledgeIngesterCreation | test_create_with_dependencies | 带依赖创建 | 否 |
| UT-KI-02 | TestKnowledgeIngesterCreation | test_logger_initialized | logger 初始化 | 否 |
| UT-KI-03 | TestDisabledAndSkipped | （禁用与跳过场景） | 引擎禁用与跳过 | 否 |
| UT-KI-04 | TestCalculateImportance | test_with_factual_info_and_high_score | 有事实信息且高分 | 否 |
| UT-KI-05 | TestCalculateImportance | test_with_factual_info_and_zero_score | 有事实信息且零分 | 否 |
| UT-KI-06 | TestCalculateImportance | test_without_factual_info | 无事实信息 | 否 |
| UT-KI-07 | TestCalculateImportance | test_without_factual_info_and_zero_score | 无事实信息且零分 | 否 |
| UT-KI-08 | TestCalculateImportance | test_max_score_clamped_to_one | 最高分截断为 1 | 否 |
| UT-KI-09 | TestCalculateImportance | test_negative_score_clamped_to_zero | 负分截断为 0 | 否 |
| UT-KI-10 | TestCalculateImportance | test_threshold_boundary_below | 阈值边界下方 | 否 |
| UT-KI-11 | TestIngestFlow | （Ingest 主流程） | 知识摄入主流程 | 否 |
| UT-KI-12 | TestDetectConflicts | （冲突检测） | 知识冲突检测 | 否 |
| UT-KI-13 | TestParseFactsResponse | test_parse_pure_json | 解析纯 JSON | 否 |
| UT-KI-14 | TestParseFactsResponse | test_parse_json_code_block | 解析 JSON 代码块 | 否 |
| UT-KI-15 | TestParseFactsResponse | test_parse_json_with_surrounding_text | 解析带环绕文本的 JSON | 否 |
| UT-KI-16 | TestParseFactsResponse | test_parse_empty_facts | 解析空事实 | 否 |
| UT-KI-17 | TestParseFactsResponse | test_parse_invalid_json_returns_empty | 无效 JSON 返回空 | 否 |
| UT-KI-18 | TestParseFactsResponse | test_parse_facts_not_list_returns_empty | facts 非列表返回空 | 否 |
| UT-KI-19 | TestParseFactsResponse | test_parse_strips_whitespace | 去除空白 | 否 |
| UT-KI-20 | TestParseFactsResponse | test_parse_filters_empty_strings | 过滤空字符串 | 否 |
| UT-KI-21 | TestParseFactsResponse | test_parse_missing_facts_key | 缺失 facts 键 | 否 |
| UT-KI-22 | TestIngestResult | test_default_values | IngestResult 默认值 | 否 |
| UT-KI-23 | TestIngestResult | test_inserted_result | inserted 结果 | 否 |
| UT-KI-24 | TestIngestResult | test_merged_result | merged 结果 | 否 |
| UT-KI-25 | TestIngestResult | test_disabled_result | disabled 结果 | 否 |
| UT-KI-26 | TestConflictResult | test_insert_action | insert 动作 | 否 |
| UT-KI-27 | TestConflictResult | test_replace_action | replace 动作 | 否 |
| UT-KI-28 | TestConflictResult | test_coexist_action | coexist 动作 | 否 |
| UT-KI-29 | TestConflictResult | test_default_old_entry_is_none | 默认 old_entry 为 None | 否 |

### 4.12 test_reflection_strategy.py — 反思策略测试

> 路径：`backend/tests/unit/test_reflection_strategy.py`
> 模块：`app.agents.strategies`
> 测试类数：3 | 测试函数数：19

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-RS-01 | TestShouldReflect | test_chitchat_returns_false | chitchat 不反思 | 否 |
| UT-RS-02 | TestShouldReflect | test_clarify_returns_false | clarify 不反思 | 否 |
| UT-RS-03 | TestShouldReflect | test_web_default_returns_true | web_default 反思 | 否 |
| UT-RS-04 | TestShouldReflect | test_kb_strict_returns_true | kb_strict 反思 | 否 |
| UT-RS-05 | TestShouldReflect | test_task_plan_returns_true | task_plan 反思 | 否 |
| UT-RS-06 | TestShouldReflect | test_empty_intent_returns_true | 空 intent 反思 | 否 |
| UT-RS-07 | TestShouldReplan | test_needs_replan_under_limit_returns_true | 需重规划且未达上限返回 True | 否 |
| UT-RS-08 | TestShouldReplan | test_needs_replan_at_limit_returns_false | 需重规划但达上限返回 False | 否 |
| UT-RS-09 | TestShouldReplan | test_needs_replan_over_limit_returns_false | 需重规划且超上限返回 False | 否 |
| UT-RS-10 | TestShouldReplan | test_pass_returns_false | pass 不重规划 | 否 |
| UT-RS-11 | TestShouldReplan | test_needs_rewrite_returns_false | needs_rewrite 不重规划 | 否 |
| UT-RS-12 | TestShouldReplan | test_empty_evaluation_returns_false | 空评估不重规划 | 否 |
| UT-RS-13 | TestShouldReplan | test_replan_just_below_limit_returns_true | 重规划次数略低于上限返回 True | 否 |
| UT-RS-14 | TestCreateReflectionStrategy | test_always_returns_always_strategy | always 策略 | 否 |
| UT-RS-15 | TestCreateReflectionStrategy | test_adaptive_falls_back_to_always | adaptive 回退到 always | 否 |
| UT-RS-16 | TestCreateReflectionStrategy | test_sampling_falls_back_to_always | sampling 回退到 always | 否 |
| UT-RS-17 | TestCreateReflectionStrategy | test_unknown_policy_raises_config_error | 未知策略抛 ConfigError | 否 |
| UT-RS-18 | TestCreateReflectionStrategy | test_strategy_inherits_max_replan | 策略继承 max_replan | 否 |
| UT-RS-19 | TestCreateReflectionStrategy | test_get_strategy_name | 获取策略名 | 否 |

### 4.13 test_short_term_memory.py — L1 短期记忆测试

> 路径：`backend/tests/unit/test_short_term_memory.py`
> 模块：`app.memory.short_term`
> 测试类数：6 | 测试函数数：11+

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-L1-01 | TestAddMessage | （添加消息场景） | 添加消息到短期记忆 | 否 |
| UT-L1-02 | TestSlidingWindow | （滑动窗口场景） | 滑动窗口管理 | 否 |
| UT-L1-03 | TestTokenCounting | test_count_text_tokens_positive | 正文本 token 计数 | 否 |
| UT-L1-04 | TestTokenCounting | test_count_text_tokens_empty | 空文本 token 计数 | 否 |
| UT-L1-05 | TestTokenCounting | test_count_text_tokens_chinese | 中文 token 计数 | 否 |
| UT-L1-06 | TestTokenCounting | test_count_messages_tokens | 消息列表 token 计数 | 否 |
| UT-L1-07 | TestTokenCounting | test_count_more_text_more_tokens | 文本越多 token 越多 | 否 |
| UT-L1-08 | TestGetContext | （获取上下文场景） | 获取上下文 | 否 |
| UT-L1-09 | TestClearMemory | （清空记忆场景） | 清空短期记忆 | 否 |
| UT-L1-10 | TestCompressIfNeeded | （压缩场景） | 必要时压缩 | 否 |

### 4.14 test_storage.py — 存储后端测试

> 路径：`backend/tests/unit/test_storage.py`
> 模块：`app.storage.json_storage`
> 测试类数：9 | 测试函数数：多

| 编号 | 测试类 | 测试目的 | 需要 API |
|---|---|---|---|
| UT-STO-01 | TestCreateConversation | 创建会话 | 否 |
| UT-STO-02 | TestGetConversation | 获取会话 | 否 |
| UT-STO-03 | TestListConversations | 列出会话 | 否 |
| UT-STO-04 | TestAppendMessage | 追加消息 | 否 |
| UT-STO-05 | TestGetMessages | 获取消息 | 否 |
| UT-STO-06 | TestDeleteConversation | 删除会话 | 否 |
| UT-STO-07 | TestUpdateConversation | 更新会话 | 否 |
| UT-STO-08 | TestUpdateConversationStats | 更新会话统计 | 否 |
| UT-STO-09 | TestAtomicWrite | 原子写入 | 否 |

### 4.15 test_vector_store.py — 向量存储测试

> 路径：`backend/tests/unit/test_vector_store.py`
> 模块：`app.tools.direct.vector_store`
> 测试类数：4 | 测试函数数：6+

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| UT-VS-01 | TestDirectVectorStoreCreation | test_create_with_kb | 带知识库创建 | 否 |
| UT-VS-02 | TestDirectVectorStoreCreation | test_create_accepts_none | 接受 None 创建 | 否 |
| UT-VS-03 | TestDirectVectorStoreSearch | （搜索场景） | 向量搜索 | 否 |
| UT-VS-04 | TestDirectVectorStoreAdd | （添加场景） | 向量添加 | 否 |
| UT-VS-05 | TestDirectVectorStoreCount | （计数场景） | 向量计数 | 否 |

---

## 5. 集成测试用例清单

> 所有集成测试位于 `backend/tests/integration/`，跨模块协作但不依赖真实 API。

### 5.1 test_eval_pipeline.py — 评估流水线集成测试

> 路径：`backend/tests/integration/test_eval_pipeline.py`
> 模块：`app.eval`（runner + assertion + metrics + reporter）
> 测试类数：1 | 测试函数数：10

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| IT-EP-01 | TestEvalPipelineIntegration | test_extract_metrics_then_assert_pass | 提取指标后断言通过 | 否 |
| IT-EP-02 | TestEvalPipelineIntegration | test_extract_metrics_then_assert_fail | 提取指标后断言失败 | 否 |
| IT-EP-03 | TestEvalPipelineIntegration | test_metric_levels_collected_in_result | 指标等级收集到结果 | 否 |
| IT-EP-04 | TestEvalPipelineIntegration | test_pipeline_with_tool_calls | 含工具调用的流水线 | 否 |
| IT-EP-05 | TestEvalPipelineIntegration | test_pipeline_should_not_call_tools | 不应调用工具的流水线 | 否 |
| IT-EP-06 | TestEvalPipelineIntegration | test_pipeline_multiple_states_batch | 多状态批量流水线 | 否 |
| IT-EP-07 | TestEvalPipelineIntegration | test_pipeline_jsonl_export | JSONL 导出 | 否 |
| IT-EP-08 | TestEvalPipelineIntegration | test_pipeline_replan_at_limit | 重规划达上限 | 否 |
| IT-EP-09 | TestEvalPipelineIntegration | test_pipeline_latency_threshold | 延迟阈值检查 | 否 |
| IT-EP-10 | TestEvalPipelineIntegration | test_full_pipeline_end_to_end | 完整流水线端到端 | 否 |

> 注：原文档说明有 11 个测试，实际代码中包含 10 个测试函数。

### 5.2 test_storage_memory_integration.py — 存储与记忆集成测试

> 路径：`backend/tests/integration/test_storage_memory_integration.py`
> 模块：`app.storage.json_storage` + `app.memory.short_term`
> 测试类数：1 | 测试函数数：6

| 编号 | 测试类 | 测试函数 | 测试目的 | 需要 API |
|---|---|---|---|---|
| IT-SM-01 | TestStorageMemoryIntegration | test_create_conversation_and_load_into_memory | 创建会话并载入记忆 | 否 |
| IT-SM-02 | TestStorageMemoryIntegration | test_conversation_switching | 会话切换 | 否 |
| IT-SM-03 | TestStorageMemoryIntegration | test_storage_stats_and_memory_consistency | 存储统计与记忆一致性 | 否 |
| IT-SM-04 | TestStorageMemoryIntegration | test_delete_conversation_then_clear_memory | 删除会话后清空记忆 | 否 |
| IT-SM-05 | TestStorageMemoryIntegration | test_reload_messages_after_restart | 重启后重新加载消息 | 否 |
| IT-SM-06 | TestStorageMemoryIntegration | test_context_built_from_storage_messages | 从存储消息构建上下文 | 否 |

---

## 6. 评估黄金数据集

> 路径：`backend/app/eval/datasets/golden_qa.json`
> 用途：端到端质量评估回归，需要真实 API Key
> 用例数：10（5 个功能用例 + 5 个指标用例）

### 6.1 功能测试用例（TC-01 ~ TC-05）

| 编号 | 描述 | 输入 | 期望 | 需要 API |
|---|---|---|---|---|
| TC-01 | 闲聊意图识别 - 验证 chitchat 意图且不调用工具 | "你好啊，今天天气真好" | intent=chitchat；should_not_call_tools=true；intent_confidence_min=0.7 | 是 |
| TC-02 | 知识库意图识别 - 命中 kb_strict 关键词，应严格基于知识库回答 | "根据我的笔记讲讲 Python 的装饰器原理" | intent=kb_strict；intent_confidence_min=0.7 | 是 |
| TC-03 | 联网意图识别 - 涉及实时信息，应调用 web_search 工具 | "2026年诺贝尔奖得主是谁" | intent=web_default；should_call_web_search=true；intent_confidence_min=0.7 | 是 |
| TC-04 | 复杂任务规划 - 对比类问题应触发 task_plan 多步规划 | "对比 React 和 Vue 的优缺点，从性能、生态、学习曲线三个维度展开" | intent=task_plan；intent_confidence_min=0.7 | 是 |
| TC-05 | 反思重试 - 逻辑陷阱问题，验证 replan_count 控制在合理范围 | "请证明：所有乌鸦都是黑色的。然后用反例推翻你的证明。" | max_replan=2 | 是 |

### 6.2 指标验证用例（TC-M01 ~ TC-M05）

| 编号 | 描述 | 输入 | 期望 | 需要 API |
|---|---|---|---|---|
| TC-M01 | 指标验证 - 意图置信度阈值检查（应 ≥ 0.9 视为 good） | "帮我总结一下今天会议的要点" | intent_confidence_min=0.7 | 是 |
| TC-M02 | 指标验证 - 端到端延迟不超过 10 秒（warn 阈值） | "什么是机器学习？" | max_latency_ms=10000 | 是 |
| TC-M03 | 指标验证 - 答案锚定度（groundedness）应达到 good 阈值 | "根据我的知识库说明，LangChain 的核心组件有哪些？" | intent=kb_strict；min_groundedness=0.6 | 是 |
| TC-M04 | 指标验证 - 工具调用成功率，调用 web_search 时应成功 | "2026 年最新的 AI 模型排行榜是什么" | should_call_web_search=true | 是 |
| TC-M05 | 指标验证 - 重规划次数不超过 2 次（与 config.reflection.max_replan 一致） | "请用三种不同的方法解决同一个问题：计算斐波那契数列第 10 项" | max_replan=2 | 是 |

### 6.3 运行评估

```bash
# 通过 CLI 运行（推荐）
cd backend
sekb eval --dataset app/eval/datasets/golden_qa.json

# 或通过 Python 模块运行
python -m app.cli.main eval --dataset app/eval/datasets/golden_qa.json
```

评估输出为 JSONL 格式，包含每条用例的指标值、阈值等级（good/warn/bad）和通过状态。

---

## 7. Phase 3 验证脚本

> 路径：`backend/scripts/verify_phase3.py`
> 用途：Phase 3 集成验证，检查模块导入与功能正确性
> 检查项总数：12

### 7.1 模块导入验证（7 项）

| 编号 | 检查项 | 期望 | 需要 API |
|---|---|---|---|
| PV-01 | `app.core.auth` 模块导入 | 成功导入 hash_password/verify_password/create_jwt/verify_jwt | 否 |
| PV-02 | `app.models.user` 模块导入 | 成功导入 User/UserPublic/RegisterRequest/LoginRequest | 否 |
| PV-03 | `app.storage.user_storage` 模块导入 | 成功导入 UserStorage | 否 |
| PV-04 | `app.api.routes.auth` 路由加载 | 成功加载 auth router | 否 |
| PV-05 | `app.api.routes.knowledge` 路由加载 | 成功加载 knowledge router | 否 |
| PV-06 | `app.api.routes.conversations` 路由加载 | 成功加载 conversations router | 否 |
| PV-07 | `app.api.middleware` 中间件加载 | 成功加载 setup_cors/RateLimitMiddleware | 否 |

### 7.2 功能验证（5 项）

| 编号 | 检查项 | 期望 | 需要 API |
|---|---|---|---|
| PV-08 | 密码哈希（PBKDF2-SHA256） | 正确密码验证通过，错误密码验证失败 | 否 |
| PV-09 | JWT 签发/验证 | 签发 token 后验证 sub 字段一致 | 否 |
| PV-10 | User 模型创建 | user_id 以 "user_" 开头，email 正确 | 否 |
| PV-11 | UserStorage CRUD | 创建/查询/更新/删除/公开信息全流程通过 | 否 |
| PV-12 | 配置加载（auth config） | token_expire_hours == 72 | 否 |

### 7.3 运行验证脚本

```bash
cd backend
python scripts/verify_phase3.py
```

输出示例：

```
==================================================
Phase 3 集成验证
==================================================

1. 模块导入验证
  ✓ app.core.auth
  ✓ app.models.user
  ✓ app.storage.user_storage
  ✓ app.api.routes.auth (N routes)
  ✓ app.api.routes.knowledge (N routes)
  ✓ app.api.routes.conversations (N routes)
  ✓ app.api.middleware

2. 功能验证
  ✓ 密码哈希 (PBKDF2-SHA256)
  ✓ JWT 签发/验证
  ✓ User 模型创建
  ✓ UserStorage CRUD
  ✓ 配置加载 (auth config)

==================================================
结果: 12 passed, 0 failed, 12 total
==================================================
Phase 3 后端验证全部通过!
```

---

## 8. 测试 Fixtures 与 Mock 数据

### 8.1 共享 Fixtures（`backend/tests/conftest.py`）

| Fixture 名 | 类型 | 用途 | 适用范围 |
|---|---|---|---|
| `test_data_dir` | 目录 | 临时数据目录（基于 tmp_path） | 全部测试 |
| `sample_config` | AppConfig | 测试用配置（mock API Key，不依赖真实 API） | 全部测试 |
| `small_l1_config` | L1MemoryConfig | 小窗口 L1 记忆配置（max_turns=2），用于测试滑动窗口与压缩 | 短期记忆测试 |
| `sample_graph_state` | GraphState | 测试用 GraphState（通过 create_initial_state 创建） | Graph 相关测试 |
| `mock_llm_factory` | MagicMock | Mock LLM 工厂，ainvoke_with_stats 返回带 content 的 Mock 响应 | LLM 相关测试 |

**`sample_config` 配置要点：**
- LLM provider：deepseek
- API Key：`test-api-key-mock`（占位，不真实调用）
- 模型角色：supervisor/planner/executor/critic/critic_complex/scribe/chat_simple
- 反思策略：always，max_replan=2
- L1 短期记忆：max_turns=8，压缩策略 hierarchical
- 成本控制：daily_budget_usd=1.0

### 8.2 Mock 响应数据（`backend/tests/fixtures/mock_responses.py`）

| Mock 常量 | 对应节点 | 字段 | 用途 |
|---|---|---|---|
| `SUPERVISOR_RESPONSE_CHITCHAT` | Supervisor | intent=chitchat, confidence=0.95 | chitchat 意图测试 |
| `SUPERVISOR_RESPONSE_WEB_DEFAULT` | Supervisor | intent=web_default, confidence=0.88 | 联网意图测试 |
| `SUPERVISOR_RESPONSE_CLARIFY` | Supervisor | intent=clarify, confidence=0.45, needs_clarification=True | 澄清意图测试 |
| `SUPERVISOR_RESPONSE_KB_STRICT` | Supervisor | intent=kb_strict, confidence=0.92 | 知识库意图测试 |
| `PLANNER_RESPONSE` | Planner | steps=[2 步], complexity=0.3 | 任务规划测试 |
| `CRITIC_RESPONSE_PASS` | Critic | passed=True, groundedness_score=0.9 | 审查通过测试 |
| `CRITIC_RESPONSE_FAIL` | Critic | passed=False, groundedness_score=0.4 | 审查失败/重规划测试 |
| `SCRIBE_RESPONSE` | Scribe | importance_score=0.5, should_persist=True | 记录测试 |
| `TOOL_CALL_WEB_SEARCH_SUCCESS` | Executor | success=True, latency_ms=500 | 工具调用成功测试 |
| `TOOL_CALL_WEB_SEARCH_FAILURE` | Executor | success=False, error="网络超时" | 工具调用失败测试 |

### 8.3 使用示例

```python
# 在测试中使用 sample_config
def test_with_config(sample_config):
    assert sample_config.llm.api_key == "test-api-key-mock"

# 在测试中使用 mock_llm_factory
@pytest.mark.asyncio
async def test_with_mock_llm(mock_llm_factory):
    response = await mock_llm_factory.ainvoke_with_stats(...)
    assert response.content == "这是一个 Mock LLM 响应"

# 在测试中使用 Mock 响应数据
from tests.fixtures.mock_responses import SUPERVISOR_RESPONSE_CHITCHAT

def test_chitchat_intent():
    assert SUPERVISOR_RESPONSE_CHITCHAT["intent"] == "chitchat"
    assert SUPERVISOR_RESPONSE_CHITCHAT["confidence"] == 0.95
```

---

## 9. 测试覆盖矩阵

### 9.1 模块测试覆盖情况

| 模块 | 单元测试文件 | 集成测试 | 评估数据集 | 覆盖状态 |
|---|---|---|---|---|
| `app.core.config` | test_config.py | - | - | ✅ 充分 |
| `app.core.auth` | - | - | - | ⚠️ 由 verify_phase3.py 覆盖 |
| `app.core.embedding` | test_embedding.py | - | - | ✅ 充分 |
| `app.core.llm_factory` | test_p0_fixes.py | - | - | ✅ 充分 |
| `app.graph.state` | test_state.py | - | - | ✅ 充分 |
| `app.graph.builder` | test_graph_routing.py | - | - | ✅ 充分 |
| `app.memory.short_term` | test_short_term_memory.py | test_storage_memory_integration.py | - | ✅ 充分 |
| `app.memory.knowledge_entry` | test_knowledge_entry.py | - | - | ✅ 充分 |
| `app.memory.knowledge_base` | - | - | - | ⚠️ 通过 ingestor 间接覆盖 |
| `app.storage.json_storage` | test_storage.py | test_storage_memory_integration.py | - | ✅ 充分 |
| `app.storage.user_storage` | - | - | - | ⚠️ 由 verify_phase3.py 覆盖 |
| `app.tools.file_processor` | test_file_processor.py | - | - | ✅ 充分 |
| `app.tools.direct.vector_store` | test_vector_store.py | - | - | ✅ 充分 |
| `app.tools.rag.retriever` | test_rag_retriever.py | - | - | ✅ 充分 |
| `app.agents.knowledge_ingestor` | test_knowledge_ingestor.py | - | - | ✅ 充分 |
| `app.agents.strategies` | test_reflection_strategy.py | - | - | ✅ 充分 |
| `app.eval.assertion` | test_assertion.py | test_eval_pipeline.py | golden_qa.json | ✅ 充分 |
| `app.eval.metrics` | test_eval_metrics.py | test_eval_pipeline.py | golden_qa.json | ✅ 充分 |
| `app.eval.runner` | - | test_eval_pipeline.py | golden_qa.json | ✅ 充分 |
| `app.api.routes.auth` | - | - | - | ⚠️ 由 verify_phase3.py 覆盖 |
| `app.api.middleware` | - | - | - | ⚠️ 由 verify_phase3.py 覆盖 |
| `app.models.user` | - | - | - | ⚠️ 由 verify_phase3.py 覆盖 |

### 9.2 测试类型覆盖统计

| 测试类型 | 覆盖模块数 | 用例数 | 是否阻断 CI |
|---|---|---|---|
| 单元测试 | 15 | 150+ | 是（unit-test job） |
| 集成测试 | 2 | 16 | 是（integration-test job） |
| 评估回归 | 1（eval pipeline） | 10 | 否（仅 main 分支触发，非阻断） |
| Phase 3 验证 | 7 | 12 | 否（手动运行） |

### 9.3 未覆盖/弱覆盖模块清单

以下模块建议补充测试：

| 模块 | 当前覆盖 | 建议补充 |
|---|---|---|
| `app.api.routes.auth` | verify_phase3.py 导入检查 | 补充路由级集成测试 |
| `app.api.routes.knowledge` | verify_phase3.py 导入检查 | 补充路由级集成测试 |
| `app.api.routes.conversations` | verify_phase3.py 导入检查 | 补充路由级集成测试 |
| `app.api.routes.chat` | 无 | 补充 chat 路由测试 |
| `app.api.routes.upload` | 无 | 补充文件上传路由测试 |
| `app.api.routes.metrics` | 无 | 补充 metrics 路由测试 |
| `app.core.auth` | verify_phase3.py 功能验证 | 补充单元测试（边界场景） |
| `app.models.user` | verify_phase3.py 创建验证 | 补充单元测试（字段校验） |
| `app.storage.user_storage` | verify_phase3.py CRUD | 补充单元测试（并发、异常） |

---

## 10. 自动化测试指南

### 10.1 CI/CD 流水线概览

> 配置文件：`.github/workflows/ci.yml`
> 触发条件：push 到 main/develop 分支、PR 到 main/develop、每周一 02:00 定时任务

```
push/PR/schedule
    │
    ├─→ lint (ruff + mypy)         # 代码质量
    ├─→ frontend-build             # 前端构建检查
    ├─→ unit-test                  # 单元测试（阻断）
    │       └─→ integration-test   # 集成测试（阻断，依赖单测通过）
    │               └─→ eval-test  # 评估回归（仅 main，非阻断）
    ├─→ build                      # 镜像构建（仅 main）
    │       └─→ deploy             # 生产部署（仅 main，依赖 build + eval-test）
```

### 10.2 CI Jobs 详解

| Job | 触发条件 | 阻断 | 环境变量 | 说明 |
|---|---|---|---|---|
| `lint` | 全部 | 是 | - | ruff check + mypy（mypy 非阻断） |
| `frontend-build` | 全部 | 是 | - | tsc 类型检查 + vite 构建 |
| `unit-test` | 全部 | 是 | DEEPSEEK_API_KEY=test-key 等 | pytest + 覆盖率，-x 首次失败即停 |
| `integration-test` | 全部 | 是 | DEEPSEEK_API_KEY=test-key 等 | 依赖 unit-test 通过 |
| `eval-test` | 仅 main push | 否 | 真实 DEEPSEEK_API_KEY/BOCHA_API_KEY | 评估黄金数据集回归 |
| `build` | 仅 main push | 是 | - | 构建 backend/frontend 镜像并推送到 GHCR |
| `deploy` | 仅 main push | 是 | SSH 相关 Secrets | 部署到生产，含健康检查与回滚 |

### 10.3 CI 中的测试命令

```yaml
# 单元测试
- name: Run unit tests
  working-directory: backend
  env:
    DEEPSEEK_API_KEY: test-key
    BOCHA_API_KEY: test-key
    JWT_SECRET: test-secret
  run: pytest tests/unit -x --cov=app --cov-report=xml --cov-report=term -q

# 集成测试
- name: Run integration tests
  working-directory: backend
  env:
    DEEPSEEK_API_KEY: test-key
    BOCHA_API_KEY: test-key
    JWT_SECRET: test-secret
  run: pytest tests/integration -x -q

# 评估回归（非阻断）
- name: Run eval on golden dataset
  working-directory: backend
  env:
    DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
    BOCHA_API_KEY: ${{ secrets.BOCHA_API_KEY }}
    JWT_SECRET: eval-secret
  run: |
    if [ -f tests/fixtures/golden_qa.json ] || [ -f app/eval/datasets/golden_qa.json ]; then
      python -m app.cli.main eval --dataset app/eval/datasets/golden_qa.json || echo "eval non-blocking"
    else
      echo "golden dataset not found, skipping eval"
    fi
```

### 10.4 本地开发流程建议

```bash
# 1. 提交前本地跑全部测试
cd backend
pytest tests/unit tests/integration -v

# 2. 检查代码质量
ruff check app/
mypy app/ --ignore-missing-imports

# 3. 如改动涉及 auth/用户模块，跑 Phase 3 验证
python scripts/verify_phase3.py

# 4. 如改动涉及评估流水线，本地跑评估（需真实 API Key）
sekb eval --dataset app/eval/datasets/golden_qa.json
```

### 10.5 后续自动化建议

1. **PR 预检 Bot**：集成评论机器人，PR 创建时自动跑 lint + unit-test 并在 PR 中反馈结果
2. **覆盖率门禁**：在 CI 中增加覆盖率阈值检查，低于 80% 阻断合并
3. **性能回归基线**：对 TC-M02（延迟）建立历史基线，单次 PR 延迟回归 > 20% 时告警
4. **评估趋势看板**：将每次 eval 结果上报到 Grafana，建立质量趋势图
5. **混沌测试**：对 LLM 重试/降级路径注入故障（mock 网络超时），验证降级策略
6. **契约测试**：对 `app.api.routes.*` 增加 OpenAPI 契约测试，防止接口破坏性变更
7. **定时全量回归**：每周一 02:00 已配置定时任务，建议增加评估结果邮件/飞书通知
8. **测试数据工厂**：建立 `tests/factories/` 模块，集中管理测试数据生成逻辑

### 10.6 Secrets 配置（仓库 Settings → Secrets）

| Secret 名 | 用途 | 是否必填 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 评估测试用 LLM Key | 是（评估回归需要） |
| `BOCHA_API_KEY` | 评估测试用搜索 Key | 是（评估回归需要） |
| `DEPLOY_HOST` | 部署服务器 IP | 是（生产部署需要） |
| `DEPLOY_USER` | SSH 用户 | 是（生产部署需要） |
| `DEPLOY_KEY` | SSH 私钥 | 是（生产部署需要） |
| `DEPLOY_PATH` | 部署路径（默认 /opt/self-evolving-kb） | 否 |

---

## 附录 A：测试命名约定

- **测试文件**：`test_<模块名>.py`
- **测试类**：`Test<功能名>`（如 `TestAssertIntent`）
- **测试函数**：`test_<行为描述>`（如 `test_intent_match_passes`）
- **测试编号**：本文档采用 `UT-<模块缩写>-NN` / `IT-<模块缩写>-NN` / `TC-NN` / `PV-NN` 格式
  - UT = Unit Test
  - IT = Integration Test
  - TC = Test Case（黄金数据集）
  - PV = Phase Verification

## 附录 B：相关文档索引

- [01-architecture.md](../01-architecture.md) - 系统架构设计
- [02-phase1-design.md](../02-phase1-design.md) - Phase 1 设计
- [03-evaluation-and-testing.md](../03-evaluation-and-testing.md) - 评估与测试体系
- [04-config-reference.md](../04-config-reference.md) - 配置参考
- [06-phase3-design.md](../06-phase3-design.md) - Phase 3 设计
- [DEPLOYMENT.md](../DEPLOYMENT.md) - 部署指南
- [ALERTING-TROUBLESHOOTING.md](../ALERTING-TROUBLESHOOTING.md) - 告警与故障排查
