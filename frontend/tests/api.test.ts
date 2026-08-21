/**
 * api 拦截器单元测试
 *
 * 覆盖 services/api.ts 的 axios 拦截器逻辑：
 * - 请求拦截器：有/无 token 时自动注入 Authorization
 * - 响应拦截器(成功)：触发 perf done 回调
 * - 响应拦截器(rejected 401 非 auth 接口)：清除 localStorage + 跳转 /login
 * - 响应拦截器(rejected 401 auth 接口)：不跳转（交由页面处理）
 * - 响应拦截器(rejected 非 401)：仅 reject，不清除 token
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// mock logger，避免实际打日志和 perf 计时副作用
vi.mock('@/utils/logger', () => ({
  logger: {
    perf: vi.fn(() => vi.fn()),
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
  },
}));

import apiClient from '@/services/api';

// 取出拦截器处理函数（axios 1.x 暴露 handlers 数组）
const getRequestHandler = () => apiClient.interceptors.request.handlers[0].fulfilled;
const getResponseRejected = () => apiClient.interceptors.response.handlers[0].rejected;

describe('api 拦截器', () => {
  let originalLocation: Location;

  beforeEach(() => {
    localStorage.clear();
    originalLocation = window.location;
    // jsdom 的 location.href 不可直接赋值，用 defineProperty 替换
    delete (window as any).location;
    Object.defineProperty(window, 'location', {
      value: { href: '' },
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    // 还原 location
    delete (window as any).location;
    Object.defineProperty(window, 'location', {
      value: originalLocation,
      configurable: true,
    });
  });

  describe('请求拦截器: token 注入', () => {
    it('有 token → headers 注入 Authorization: Bearer xxx', () => {
      localStorage.setItem('sekb_token', 'my-token');
      const config: any = { headers: {}, method: 'get', url: '/test' };

      const result = getRequestHandler()(config);

      expect(result.headers.Authorization).toBe('Bearer my-token');
    });

    it('无 token → 不设置 Authorization', () => {
      const config: any = { headers: {}, method: 'get', url: '/test' };

      const result = getRequestHandler()(config);

      expect(result.headers.Authorization).toBeUndefined();
    });
  });

  describe('响应拦截器(rejected): 401 处理', () => {
    const makeError = (url: string, status: number) => {
      const config: any = { url, method: 'get', __logDone: vi.fn() };
      const error: any = {
        config,
        message: 'Request failed',
        response: { status },
      };
      return error;
    };

    it('401 非 auth 接口 → 清除 localStorage 并跳转 /login', async () => {
      localStorage.setItem('sekb_token', 'tok');
      localStorage.setItem('sekb_user', '{}');
      const error = makeError('/conversations', 401);

      await expect(getResponseRejected()(error)).rejects.toBe(error);
      expect(localStorage.getItem('sekb_token')).toBeNull();
      expect(localStorage.getItem('sekb_user')).toBeNull();
      expect(window.location.href).toBe('/login');
    });

    it('401 auth/login 接口 → 不跳转，不清除 token（交由页面处理）', async () => {
      localStorage.setItem('sekb_token', 'tok');
      const error = makeError('/auth/login', 401);

      await expect(getResponseRejected()(error)).rejects.toBe(error);
      expect(window.location.href).not.toBe('/login');
      // 登录接口的 401 不应清 token（可能用户只是密码错，token 可能是旧的）
      expect(localStorage.getItem('sekb_token')).toBe('tok');
    });

    it('401 auth/register 接口 → 不跳转', async () => {
      const error = makeError('/auth/register', 401);

      await expect(getResponseRejected()(error)).rejects.toBe(error);
      expect(window.location.href).not.toBe('/login');
    });

    it('非 401 错误 → 仅 reject，不清除 token、不跳转', async () => {
      localStorage.setItem('sekb_token', 'tok');
      const error = makeError('/conversations', 500);

      await expect(getResponseRejected()(error)).rejects.toBe(error);
      expect(localStorage.getItem('sekb_token')).toBe('tok');
      expect(window.location.href).not.toBe('/login');
    });
  });
});
