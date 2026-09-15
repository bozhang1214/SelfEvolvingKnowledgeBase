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
    """L2 中期记忆配置（Phase 2，Redis 落地）"""
    enabled: bool = False
    max_items: int = 50
    eviction: str = "lru"
    redis_url: str = "redis://localhost:6379/0"  # Redis 连接串
    ttl_days: int = 30                            # 偏好/话题的过期天数


class EvictionConfig(BaseModel):
    """L3 知识库驱逐配置"""
    max_items: int = 10000
    stale_days: int = 7
    similarity_merge_threshold: float = 0.95


class RetrievalConfig(BaseModel):
    """RAG 检索增强配置（混合检索 / 重排 / 查询改写）"""
    hybrid_enabled: bool = False           # 是否启用「向量 + BM25」混合检索
    bm25_enabled: bool = True              # 混合检索内是否启用 BM25 关键词通道
    bm25_cache_ttl: int = 300              # BM25 索引缓存秒数
    bm25_page_size: int = 1000             # 分页拉取语料每页条数
    rerank_enabled: bool = False           # 是否启用 LLM 列表式重排
    rerank_top_n: int = 0                  # 重排后保留条数（0 = 用 top_k）
    rerank_role: str = "rerank"            # 重排 LLM 角色
    query_rewrite_enabled: bool = False    # 是否启用查询改写
    query_rewrite_role: str = "rerank"     # 查询改写 LLM 角色


class L3MemoryConfig(BaseModel):
    """L3 长期知识库配置（Phase 2）"""
    enabled: bool = False
    retrieval_top_k: int = 5
    importance_threshold: float = 0.3
    eviction: EvictionConfig = EvictionConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
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


class ReflectionConfig(BaseModel):
    """反思策略配置（Phase 1 仅实现 always；adaptive/sampling 占位已按 D3 删除，见 11-EVOLUTION）"""
    policy: str = "always"
    max_replan: int = Field(2, ge=0)
    max_rewrite: int = Field(1, ge=0, description="needs_rewrite 时最多重写答案次数")
    model_switch_threshold: float = Field(0.7, ge=0.0, le=1.0, description="复杂度>=阈值时 Critic 用 reasoner")

    @field_validator("policy")
    @classmethod
    def policy_must_be_valid(cls, v: str) -> str:
        valid = {"always"}
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


class RagasConfig(BaseModel):
    """RAGAS 式检索质量评测配置（LLM-as-Judge）"""
    role: str = "ragas"          # 裁判 LLM 角色
    top_k: int = 5               # 每次评测检索返回条数


class EvaluationConfig(BaseModel):
    """评估体系配置"""
    enabled: bool = True
    log_to_file: bool = True
    log_path: str = "data/eval_logs"
    metrics: EvaluationMetricsConfig = EvaluationMetricsConfig()
    regression: RegressionConfig = RegressionConfig()
    ragas: RagasConfig = RagasConfig()


class PricingConfig(BaseModel):
    """单模型定价"""
    input: float
    output: float


class UsageConfig(BaseModel):
    """费用/用量统计存储配置（Redis，用于对话 token 与费用展示）"""
    enabled: bool = True
    redis_url: str = "redis://localhost:6379/0"


class CostControlConfig(BaseModel):
    """成本控制配置（仅保留定价表 + 费用统计；预算占位已按 D3 删除）"""
    pricing: dict[str, PricingConfig] = {}
    usd_to_cny: float = 7.2              # USD → CNY 固定汇率（费用展示用）
    usage: UsageConfig = UsageConfig()


class LangSmithConfig(BaseModel):
    """LangSmith 配置"""
    api_key: str = ""
    project: str = "self-evolving-kb"
    endpoint: str = "https://api.smith.langchain.com"


class TracingConfig(BaseModel):
    """链路追踪配置（Phase 3：仅 LangSmith；本地 JSON 降级已按 D6 删除）"""
    provider: str = "langsmith"
    langsmith: LangSmithConfig = LangSmithConfig()


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
    prompt_injection_use_llm: bool = False   # 是否启用 LLM 层注入检测（额外延迟）
    prompt_injection_llm_role: str = "ragas"  # LLM 层检测使用的角色
    max_input_length: int = 8000
    blocked_patterns: list[str] = []


class RateLimitConfig(BaseModel):
    """限流配置（按路由分组，前缀匹配；纯 ASGI 中间件，不缓冲 SSE）"""
    enabled: bool = False
    requests_per_minute: int = 60      # 默认（chat/knowledge/conversations 等）
    share_per_minute: int = 20         # 分享问答（公开链接，防滥用）
    upload_per_minute: int = 30        # 文件上传
    job_per_minute: int = 30           # 职位采集/批量分析
    news_per_minute: int = 10          # 资讯刷新（成本高）


class AuthConfig(BaseModel):
    """鉴权配置"""
    token_expire_hours: int = 2160  # 90 天（配合前端滑动续租，实际接近免登录）
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
    daily_cron: str = "0 8 * * *"         # 日报：每天 08:00（北京时间）
    weekly_cron: str = "0 8 * * 1"        # 周报：每周一 08:00（北京时间）
    monthly_cron: str = "0 8 1 * *"       # 月报：每月 1 日 08:00（北京时间）
    timezone: str = "Asia/Shanghai"       # 定时任务时区（容器默认 UTC，需显式指定北京时间）


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
    # ---- 分析链路传输方式（P4：SEKB 切到 MCP）----
    # "mcp"    走 jobcopilot-mcp 子进程（stdio）——解耦内核，SEKB 不再直接 import
    # "direct" 进程内直接调用 jobcopilot 包（旧路径，保留作紧急回滚开关）
    transport: str = "mcp"
    mcp_command: str = "jobcopilot-mcp"   # 子进程命令
    mcp_timeout_s: float = 180.0          # 单次工具调用超时（秒）
    mcp_connect_timeout_s: float = 30.0   # 建连超时（秒，含子进程冷启动）


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


def _resolve_env_ref(expr: str) -> str:
    """解析单个 ``${...}`` 内的表达式，支持默认值语法。

    支持：
    - ``${VAR}``                → 未设置返回空串
    - ``${VAR:-default}``       → 未设置或为空返回 default
    - ``${VAR:default}``        → 同上（兼容 config.yaml 里的简写）
    """
    expr = expr.strip()
    if ":-" in expr:
        var_name, default = expr.split(":-", 1)
    elif ":" in expr:
        var_name, default = expr.split(":", 1)
    else:
        var_name, default = expr, ""
    value = os.getenv(var_name)
    if value is None or value == "":
        return default
    return value


def _expand_env_vars(obj: Any) -> Any:
    """
    递归展开配置中的 ${ENV_VAR} 引用（含默认值语法）。

    如果环境变量未设置，替换为空字符串（让 Pydantic 校验报错）。
    """
    if isinstance(obj, str):
        return _ENV_VAR_PATTERN.sub(lambda m: _resolve_env_ref(m.group(1)), obj)
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
