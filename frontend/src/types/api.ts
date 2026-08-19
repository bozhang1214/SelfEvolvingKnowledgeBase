// 统一 API 响应格式
export interface ApiResponse<T = unknown> {
  code: number;
  data: T;
  message: string;
  meta?: {
    trace_id?: string;
    timestamp?: string;
  };
}

// 错误码
export enum ErrorCode {
  SUCCESS = 0,
  PARAM_INVALID = 1001,
  NOT_FOUND = 1002,
  UNAUTHORIZED = 2001,
  FORBIDDEN = 2002,
  RATE_LIMITED = 3001,
  LLM_ERROR = 4001,
  TOOL_ERROR = 4002,
  BUDGET_EXCEEDED = 5001,
}