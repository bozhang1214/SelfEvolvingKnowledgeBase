"""
自定义异常体系

定义项目所有自定义异常，按功能域分类。
所有异常继承自 SEKBError，便于统一捕获和处理。
"""

from typing import Any


class SEKBError(Exception):
    """所有项目异常的基类"""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigError(SEKBError):
    """配置加载或校验错误"""


class LLMError(SEKBError):
    """LLM 调用相关错误"""

    def __init__(
        self,
        message: str,
        model: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.model = model
        self.status_code = status_code


class LLMRateLimitError(LLMError):
    """LLM 速率限制错误（429）"""


class LLMTimeoutError(LLMError):
    """LLM 调用超时"""


class LLMRetryableError(LLMError):
    """
    LLM 可重试错误。

    覆盖 5xx 服务器错误、连接重置、余额不足(402)、
    请求体过大(413)等暂时性/可恢复错误。
    与 LLMRateLimitError、LLMTimeoutError 一样被 tenacity 重试。
    """


class ToolError(SEKBError):
    """工具调用相关错误"""

    def __init__(
        self,
        message: str,
        tool_name: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.tool_name = tool_name


class MCPError(ToolError):
    """MCP 协议相关错误"""


class StorageError(SEKBError):
    """存储后端相关错误"""


class SEKBMemoError(SEKBError):
    """
    记忆系统相关错误。

    注意：命名为 SEKBMemoError 而非 MemoryError，
    避免遮蔽 Python 内置的 MemoryError。
    """


class AgentError(SEKBError):
    """Agent 节点执行错误"""

    def __init__(
        self,
        message: str,
        agent_name: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details)
        self.agent_name = agent_name


class ReflectionError(AgentError):
    """反思评估相关错误"""


class EvaluationError(SEKBError):
    """评估体系相关错误"""


class BudgetExceededError(SEKBError):
    """预算超限错误"""

    def __init__(self, message: str, current_cost: float, budget_limit: float):
        super().__init__(message)
        self.current_cost = current_cost
        self.budget_limit = budget_limit


class SecurityError(SEKBError):
    """安全相关错误（如 Prompt 注入检测）"""


class AuthError(Exception):
    """鉴权相关错误"""
    def __init__(self, message: str = "鉴权失败", status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)
