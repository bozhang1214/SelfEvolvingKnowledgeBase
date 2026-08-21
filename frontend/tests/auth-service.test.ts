/**
 * auth service 单元测试
 *
 * 覆盖 services/auth.ts：
 * - login / register：POST 后端 + 持久化 token/user 到 localStorage
 * - logout：finally 中清除 localStorage（即使 API 失败也清除）
 * - getStoredUser / getToken：读取 localStorage
 * - getMe：GET 当前用户
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@/services/api', () => ({
  default: { post: vi.fn(), get: vi.fn() },
}));

import apiClient from '@/services/api';
import { login, register, logout, getMe, getStoredUser, getToken } from '@/services/auth';
import type { User, LoginResponse } from '@/types/user';

const mockUser: User = {
  user_id: 'u1',
  email: 'test@example.com',
  name: 'Test',
  avatar_url: '',
  created_at: '2026-01-01T00:00:00Z',
  is_active: true,
  settings: { model: 'deepseek', temperature: 0.7, max_tokens: 4096 },
};

const mockLoginResponse: LoginResponse = { user: mockUser, token: 'jwt-token-xxx' };

describe('auth service', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(apiClient.post).mockReset();
    vi.mocked(apiClient.get).mockReset();
  });

  describe('login', () => {
    it('成功 → POST /auth/login 并持久化 token/user', async () => {
      vi.mocked(apiClient.post).mockResolvedValue({ data: mockLoginResponse });

      const res = await login({ email: 'a@b.com', password: 'p' });

      expect(apiClient.post).toHaveBeenCalledWith('/auth/login', { email: 'a@b.com', password: 'p' });
      expect(res.token).toBe('jwt-token-xxx');
      expect(res.user.user_id).toBe('u1');
      expect(localStorage.getItem('sekb_token')).toBe('jwt-token-xxx');
      expect(JSON.parse(localStorage.getItem('sekb_user')!)).toMatchObject({ user_id: 'u1' });
    });
  });

  describe('register', () => {
    it('成功 → POST /auth/register 并持久化 token/user', async () => {
      vi.mocked(apiClient.post).mockResolvedValue({ data: mockLoginResponse });

      const res = await register({ email: 'new@b.com', password: 'p', name: 'New' });

      expect(apiClient.post).toHaveBeenCalledWith('/auth/register', { email: 'new@b.com', password: 'p', name: 'New' });
      expect(res.user.user_id).toBe('u1');
      expect(localStorage.getItem('sekb_token')).toBe('jwt-token-xxx');
      expect(localStorage.getItem('sekb_user')).toBeTruthy();
    });
  });

  describe('logout', () => {
    it('成功 → POST /auth/logout 并清除 localStorage', async () => {
      localStorage.setItem('sekb_token', 'tok');
      localStorage.setItem('sekb_user', '{}');
      vi.mocked(apiClient.post).mockResolvedValue({});

      await logout();

      expect(apiClient.post).toHaveBeenCalledWith('/auth/logout');
      expect(localStorage.getItem('sekb_token')).toBeNull();
      expect(localStorage.getItem('sekb_user')).toBeNull();
    });

    it('API 失败 → finally 仍清除 localStorage', async () => {
      localStorage.setItem('sekb_token', 'tok');
      localStorage.setItem('sekb_user', '{}');
      vi.mocked(apiClient.post).mockRejectedValue(new Error('network'));

      await expect(logout()).rejects.toThrow('network');

      // 关键：finally 保证本地 token 被清除
      expect(localStorage.getItem('sekb_token')).toBeNull();
      expect(localStorage.getItem('sekb_user')).toBeNull();
    });
  });

  describe('getMe', () => {
    it('成功 → GET /auth/me 返回 User', async () => {
      vi.mocked(apiClient.get).mockResolvedValue({ data: mockUser });

      const user = await getMe();

      expect(apiClient.get).toHaveBeenCalledWith('/auth/me');
      expect(user.user_id).toBe('u1');
    });
  });

  describe('getStoredUser / getToken', () => {
    it('getStoredUser 有数据 → 返回 User', () => {
      localStorage.setItem('sekb_user', JSON.stringify(mockUser));
      expect(getStoredUser()?.user_id).toBe('u1');
    });

    it('getStoredUser 无数据 → 返回 null', () => {
      expect(getStoredUser()).toBeNull();
    });

    it('getToken 有 → 返回 token', () => {
      localStorage.setItem('sekb_token', 'abc');
      expect(getToken()).toBe('abc');
    });

    it('getToken 无 → 返回 null', () => {
      expect(getToken()).toBeNull();
    });
  });
});
