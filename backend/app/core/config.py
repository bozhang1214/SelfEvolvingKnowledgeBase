"""
配置加载与校验模块

负责从 config.yaml 加载配置，展开 ${ENV_VAR} 环境变量引用，
通过 Pydantic 进行强类型校验，并提供全局单例访问。

使用方式：
    from app.core.config import get_config
    config = get_config()
    print(config.llm.roles.supervisor.model)
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from app.core.exceptions import ConfigError


# ============================================================
# Pydantic 配置模型定义
# ============================================================

class AppConfigSection(BaseModel):
    """应用元信息"""
    name: str
    version: str
    environment: str = "development"
    debug: bool = True


class LLMRoleConfig(BaseModel):
    """单个 Agent 角色的 LLM 配置"""
    model: str
    temperature: float = Field(0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(1000, gt=0)
    response_format: str | None = None  # "json" 或 None


class LLMFallbackConfig(BaseModel):
    """LLM 降级策略"""
    reasoner_to_chat: bool = True
    chat_to_error: bool = True


class LLMConfig(BaseModel):
    """LLM 模型路由总配置"""
    provider: str = "deepseek"
    api_key: str
    base_url: str = "https://api.deepseek.com/v1"
    timeout_seconds: int = 60
    max_retries: int = 2
    retry_backoff_seconds: list[int] = [1, 2]
    roles: dict[str, LLMRoleConfig]
    fallback: LLMFallbackConfig = LLMFallbackConfig()

    @field_validator("api_key")
    @classmethod
    def api_key_not_empty(cls, v: str) -> str:
        # 显式加括号明确优先级：空值 OR (未展开的 ${ENV} 且对应环境变量也未设置)
        if (not v) or (v.startswith("${") and not os.getenv(v[2:-1])):
            raise ConfigError("LLM API Key 未设置，请检查 .env 文件中的 DEEPSEEK_API_KEY")
        return v


class IntentRoutingConfig(BaseModel):
    """意图路由配置"""
    default_intent: str = "web_default"
    clarify_threshold: float = Field(0.7, ge=0.0, le=1.0)
    kb_strict_keywords: list[str] = []
    kb_prefer_hit_threshold: float = Field(0.85, ge=0.0, le=1.0)
    realtime_keywords: list[str] = []
    enable_pre_retrieval: bool = True
    pre_retrieval_top_k: int = 3


class L1MemoryConfig(BaseModel):
    """L1 短期记忆配置"""
    enabled: bool = True
    max_turns: int = Field(8, gt=0)
    max_tokens: int = Field(3000, gt=0)
    hard_token_limit: int = Field(4000, gt=0)
    compress_strategy: str = "hierarchical"
    max_compressed_summaries: int = 3


class L2MemoryConfig(BaseModel):
    """L2 中期记忆配置（Phase 2）"""
    enabled: bool = False
    max_items: int = 50
    eviction: str = "lru"


class EvictionConfig(BaseModel):
    """L3 知识库驱逐配置"""
    max_items: int = 10000
    stale_days: int = 7
    similarity_merge_threshold: float = 0.95


class L3MemoryConfig(BaseModel):
    """L3 长期知识库配置（Phase 2）"""
    enabled: bool = False
    retrieval_top_k: int = 5
    importance_threshold: float = 0.3
    eviction: EvictionConfig = EvictionConfig()
    # 生产环境禁用哈希降级：embedding 模型加载失败时应报错而非静默降级为哈希向量
    # （哈希向量维度 256 与 bge 模型 512 不一致，混用会破坏 ChromaDB HNSW 索引）
    allow_hash_fallback: bool = False


class MemoryConfig(BaseModel):
    """三层记忆总配置"""
    l1_working: L1MemoryConfig = L1MemoryConfig()
    l2_session: L2MemoryConfig = L2MemoryConfig()
    l3_knowledge: L3MemoryConfig = L3MemoryConfig()

    @field_validator("l1_working")
    @classmethod
    def l1_must_be_enabled_in_phase1(cls, v: L1MemoryConfig) -> L1MemoryConfig:
        if not v.enabled:
            raise ConfigError("Phase 1 中 L1 短期记忆必须启用（memory.l1_working.enabled=true）")
        return v

    @field_validator("l1_working")
    @classmethod
    def validate_token_limits(cls, v: L1MemoryConfig) -> L1MemoryConfig:
        if v.max_tokens >= v.hard_token_limit:
            raise ConfigError(
                f"max_tokens({v.max_tokens}) 必须小于 hard_token_limit({v.hard_token_limit})"
            )
        return v


class AdaptiveReflectionConfig(BaseModel):
    """Adaptive 反思策略参数"""
    skip_if_rag_hit_above: float = 0.95
    skip_if_intent_in: list[str] = ["chitchat"]


class SamplingReflectionConfig(BaseModel):
    """Sampling 反思策略参数"""
    rate: float = Field(0.5, ge=0.0, le=1.0)


class ReflectionConfig(BaseModel):
    """反思策略配置"""
    policy: str = "always"  # always | adaptive | sampling
    max_replan: int = Field(2, ge=0)
    model_switch_threshold: float = Field(0.7, ge=0.0, le=1.0)
    adaptive: AdaptiveReflectionConfig = AdaptiveReflectionConfig()
    sampling: SamplingReflectionConfig = SamplingReflectionConfig()

    @field_validator("policy")
    @classmethod
    def policy_must_be_valid(cls, v: str) -> str:
        valid = {"always", "adaptive", "sampling"}
        if v not in valid:
            raise ConfigError(f"reflection.policy 必须是 {valid} 之一，当前为 {v}")
        return v


class MetricThreshold(BaseModel):
    """单指标阈值"""
    good: float
    warn: float


class EvaluationMetricsConfig(BaseModel):
    """评估指标阈值集合"""
    intent_confidence: MetricThreshold = MetricThreshold(good=0.9, warn=0.7)
    plan_step_count: MetricThreshold = MetricThreshold(good=5, warn=8)
    replan_count: MetricThreshold = MetricThreshold(good=0, warn=2)
    tool_success_rate: MetricThreshold = MetricThreshold(good=0.9, warn=0.7)
    answer_groundedness: MetricThreshold = MetricThreshold(good=0.8, warn=0.6)
    critic_coherence_score: MetricThreshold = MetricThreshold(good=8.0, warn=6.0)
    answer_relevance: MetricThreshold = MetricThreshold(good=0.8, warn=0.6)
    e2e_latency_ms: MetricThreshold = MetricThreshold(good=5000, warn=10000)


class RegressionConfig(BaseModel):
    """回归检测配置"""
    pass_rate_drop_threshold: float = 0.05
    metric_drop_threshold: float = 0.05


class EvaluationConfig(BaseModel):
    """评估体系配置"""
    enabled: bool = True
    log_to_file: bool = True
    log_path: str = "data/eval_logs"
    metrics: EvaluationMetricsConfig = EvaluationMetricsConfig()
    regression: RegressionConfig = RegressionConfig()


class PricingConfig(BaseModel):
    """单模型定价"""
    input: float
    output: float


class CostControlConfig(BaseModel):
    """成本控制配置"""
    per_conversation_token_limit: int = 20000
    daily_budget_usd: float = 1.0
    reasoner_ratio_alert_above: float = 0.3
    auto_downgrade_on_budget: bool = True
    pricing: dict[str, PricingConfig] = {}


class LangSmithConfig(BaseModel):
    """LangSmith 配置"""
    api_key: str = ""
    project: str = "self-evolving-kb"
    endpoint: str = "https://api.smith.langchain.com"


class LocalJSONTraceConfig(BaseModel):
    """本地 JSON trace 配置"""
    trace_dir: str = "data/traces"
    max_file_size_mb: int = 10


class TracingConfig(BaseModel):
    """链路追踪配置"""
    provider: str = "langsmith"
    fallback_to_local_on_failure: bool = True
    sample_rate: float = Field(1.0, ge=0.0, le=1.0)
    langsmith: LangSmithConfig = LangSmithConfig()
    local_json: LocalJSONTraceConfig = LocalJSONTraceConfig()


class WebSearchConfig(BaseModel):
    """联网搜索工具配置"""
    provider: str = "bocha"
    api_key: str = ""
    endpoint: str = "https://api.bochaai.com/v1/web-search"
    max_results: int = 5
    timeout_seconds: int = 10
    max_retries: int = 2


class FilesystemToolConfig(BaseModel):
    """文件系统工具配置"""
    enabled: bool = False
    allowed_paths: list[str] = []


class VectorStoreConfig(BaseModel):
    """向量库配置"""
    enabled: bool = False
    provider: str = "chroma"
    persist_path: str = "data/chroma_db"


class DocumentParserConfig(BaseModel):
    """文档解析工具配置"""
    enabled: bool = False


class ToolsConfig(BaseModel):
    """工具层总配置"""
    web_search: WebSearchConfig = WebSearchConfig()
    filesystem: FilesystemToolConfig = FilesystemToolConfig()
    vector_store: VectorStoreConfig = VectorStoreConfig()
    document_parser: DocumentParserConfig = DocumentParserConfig()


class StorageConfig(BaseModel):
    """存储后端配置"""
    backend: str = "local_json"
    data_dir: str = ""
    conversations_dir: str = "data/conversations"
    index_file: str = "data/index.json"
    postgres: dict[str, Any] = {}


class SecurityConfig(BaseModel):
    """安全配置"""
    pii_masking: bool = True
    prompt_injection_guard: bool = True
    max_input_length: int = 8000
    blocked_patterns: list[str] = []


class RateLimitConfig(BaseModel):
    """限流配置"""
    enabled: bool = False
    requests_per_minute: int = 60


class AuthConfig(BaseModel):
    """鉴权配置"""
    token_expire_hours: int = 72
    jwt_secret: str = ""
    password_min_length: int = 8
    rate_limit_login_per_minute: int = 5


class ApiConfig(BaseModel):
    """API 服务配置"""
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = []
    rate_limit: RateLimitConfig = RateLimitConfig()
    auth: AuthConfig = AuthConfig()


class LoggingConfig(BaseModel):
    """日志配置"""
    level: str = "INFO"
    format: str = "json"
    log_dir: str = "data/logs"
    max_file_size_mb: int = 50
    backup_count: int = 7
    redact_fields: list[str] = ["api_key", "authorization", "token"]


class CategoryConfig(BaseModel):
    """科技资讯大类配置：名称 + 分类关键词。"""
    name: str = ""
    keywords: list[str] = []


class NewsConfig(BaseModel):
    """科技资讯 Agent 配置（Phase 5）"""
    enabled: bool = False
    rss_sources: list[str] = []          # RSS 源列表（URL）
    keywords: list[str] = []              # 命中关键词（标题/摘要包含即保留）
    exclude_keywords: list[str] = []      # 排除关键词（命中即丢弃）
    categories: list[CategoryConfig] = [] # 大类定义（名称 + 分类关键词，用于归类）
    min_items_per_category: int = 10      # 每个大类最少收录条目数
    max_items: int = 100                  # 单次采集最大条目数
    report_dir: str = "data/news"         # 日报存储目录（Markdown + 索引）
    retention_days: int = 70              # 日报保留天数
    time_window_hours: int = 24           # 日报信息时效窗口（小时）
    llm_role: str = "chat_simple"         # 生成日报用的 LLM 角色
    daily_cron: str = "0 9 * * *"         # 日报：每天 09:00
    weekly_cron: str = "0 9 * * 1"        # 周报：每周一 09:00
    monthly_cron: str = "0 9 1 * *"       # 月报：每月 1 日 09:00


class JobConfig(BaseModel):
    """招聘分析 Agent 配置（Phase 2）"""
    enabled: bool = False                 # 是否启用
    llm_role: str = "job_analysis"        # 分析用的 LLM 角色（JSON 输出）
    # 职位采集默认筛选条件
    default_keyword: str = "Agent"        # 默认搜索关键词（Agent 相关职位）
    default_city: str = "北京"            # 默认城市
    default_city_code: str = "010"        # 猎聘城市码（北京 010，全国 410）
    default_min_salary_k: int = 50        # 最低月薪（K），即 50K×14
    # 猎聘/BOSS 排除的大厂（这些公司已有独立渠道，避免重复，聚焦大厂之外的创业公司）
    exclude_companies: list[str] = [
        "字节", "阿里", "淘宝", "天猫", "蚂蚁", "腾讯", "百度", "小米", "京东",
        "大疆", "小红书", "深度求索", "DeepSeek", "ByteDance", "Alibaba",
        "Tencent", "Baidu", "Xiaomi",
    ]


class AppConfig(BaseModel):
    """应用全局配置（对应 config.yaml 的根结构）"""
    app: AppConfigSection
    llm: LLMConfig
    intent_routing: IntentRoutingConfig = IntentRoutingConfig()
    memory: MemoryConfig = MemoryConfig()
    reflection: ReflectionConfig = ReflectionConfig()
    evaluation: EvaluationConfig = EvaluationConfig()
    cost_control: CostControlConfig = CostControlConfig()
    tracing: TracingConfig = TracingConfig()
    tools: ToolsConfig = ToolsConfig()
    storage: StorageConfig = StorageConfig()
    security: SecurityConfig = SecurityConfig()
    api: ApiConfig = ApiConfig()
    logging: LoggingConfig = LoggingConfig()
    news: NewsConfig = NewsConfig()
    job: JobConfig = JobConfig()


# ============================================================
# 配置加载逻辑
# ============================================================

# 匹配 ${ENV_VAR} 格式的环境变量引用
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env_vars(obj: Any) -> Any:
    """
    递归展开配置中的 ${ENV_VAR} 引用。

    如果环境变量未设置，替换为空字符串（让 Pydantic 校验报错）。
    """
    if isinstance(obj, str):
        def replacer(match: re.Match) -> str:
            var_name = match.group(1)
            return os.getenv(var_name, "")
        return _ENV_VAR_PATTERN.sub(replacer, obj)
    elif isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """
    加载配置文件并返回强类型配置对象。

    流程：
        1. 读取 YAML 文件
        2. 递归展开 ${ENV_VAR} 引用
        3. Pydantic 强类型校验
        4. 返回 AppConfig 对象

    Args:
        config_path: 配置文件路径，默认为当前目录下的 config.yaml

    Returns:
        AppConfig: 全局配置对象

    Raises:
        ConfigError: 配置文件不存在或校验失败
    """
    # 加载 .env 文件到环境变量（不覆盖已存在的环境变量）
    load_dotenv()

    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {config_path}")

    try:
        raw_yaml = path.read_text(encoding="utf-8")
        raw_config = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML 解析失败: {e}") from e

    # 展开环境变量引用
    expanded = _expand_env_vars(raw_config)

    try:
        return AppConfig(**expanded)
    except Exception as e:
        raise ConfigError(f"配置校验失败: {e}") from e


@lru_cache(maxsize=1)
def get_config(config_path: str = "config.yaml") -> AppConfig:
    """
    获取全局配置单例。

    使用 lru_cache 确保全局只加载一次配置。
    测试时可通过 get_config.cache_clear() 清除缓存。

    Returns:
        AppConfig: 全局配置对象
    """
    return load_config(config_path)
