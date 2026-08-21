import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';
import type { ApiResponse } from '@/types/api';
import { logger } from '@/utils/logger';

// 开发模式：走 Vite 代理（相对路径），避免 CORS 和直连问题
// 生产模式：通过 VITE_API_BASE 直连后端
const API_BASE = import.meta.env.PROD && import.meta.env.VITE_API_BASE
  ? `${import.meta.env.VITE_API_BASE}/api/v1`
  : '/api/v1';

const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截器：自动添加 Token + 请求埋点
apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem('sekb_token');
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  // 请求埋点：记录 method、url、是否带 token，开始计时
  const done = logger.perf('api_request', {
    method: (config.method || 'get').toUpperCase(),
    url: config.url || '',
  });
  // 用自定义字段把 done 回调挂到 config 上，响应拦截器取出
  (config as any).__logDone = done;
  return config;
});

// 响应拦截器：统一错误处理 + 响应埋点
apiClient.interceptors.response.use(
  (response) => {
    const done = (response.config as any).__logDone;
    if (done) done({ status: response.status, result: 'success' });
    return response;
  },
  (error: AxiosError<ApiResponse>) => {
    const config = error.config as any;
    const done = config?.__logDone;
    const url = config?.url || '';
    const status = error.response?.status ?? 0;

    // 失败埋点：HTTP 状态 + 错误消息 + 是否超时
    if (done) {
      done({
        status,
        result: 'error',
        msg: error.message,
        timeout: error.code === 'ECONNABORTED',
      });
    }

    // 详细错误日志：便于前端排查
    logger.error('api_error', {
      url,
      method: (config?.method || 'get').toUpperCase(),
      status,
      msg: error.message,
      resp_msg: error.response?.data?.message,
      timeout: error.code === 'ECONNABORTED',
    });

    // 仅当 401 且不是登录/注册接口时，才视为 token 失效并跳转登录页
    // 登录/注册接口的 401（"邮箱或密码错误"等）由调用方页面处理，否则会冲掉错误提示
    const isAuthEndpoint = url.includes('/auth/login') || url.includes('/auth/register');
    if (error.response?.status === 401 && !isAuthEndpoint) {
      localStorage.removeItem('sekb_token');
      localStorage.removeItem('sekb_user');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

export { apiClient, API_BASE };
export default apiClient;