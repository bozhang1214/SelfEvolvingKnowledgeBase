/**
 * user store 单元测试
 *
 * 覆盖 stores/user.ts 的核心状态逻辑：
 * - init：session 恢复（有效/无效/无 token）
 * - login / register：调用 authService 并设置登录态
 * - logout：后端失败时仍清除本地状态（finally 容错）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@/services/auth', () => ({
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
}));

import { useUserStore } from '@/stores/user';
import * as authService from '@/services/auth';
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

describe('user store', () => {
  beforeEach(() => {
    // 重置 store 状态（zustand 单例，测试间需手动复位）
    useUserStore.setState({ user: null, token: null, isLoggedIn: false });
    localStorage.clear();
    vi.mocked(authService.login).mockReset();
    vi.mocked(authService.register).mockReset();
    vi.mocked(authService.logout).mockReset();
  });

  describe('init: session 恢复', () => {
    it('有 token + 有效 user JSON → 恢复登录态', () => {
      localStorage.setItem('sekb_token', 'stored-token');
      localStorage.setItem('sekb_user', JSON.stringify(mockUser));

      useUserStore.getState().init();

      const state = useUserStore.getState();
      expect(state.isLoggedIn).toBe(true);
      expect(state.token).toBe('stored-token');
      expect(state.user?.user_id).toBe('u1');
    });

    it('有 token 但 user JSON 无效 → 清除本地存储，保持未登录', () => {
      localStorage.setItem('sekb_token', 'stored-token');
      localStorage.setItem('sekb_user', '{invalid json');

      useUserStore.getState().init();

      const state = useUserStore.getState();
      expect(state.isLoggedIn).toBe(false);
      expect(state.user).toBeNull();
      expect(state.token).toBeNull();
      // 无效 JSON 应触发清理
      expect(localStorage.getItem('sekb_token')).toBeNull();
      expect(localStorage.getItem('sekb_user')).toBeNull();
    });

    it('无 token → 不恢复，保持默认未登录', () => {
      useUserStore.getState().init();

      const state = useUserStore.getState();
      expect(state.isLoggedIn).toBe(false);
      expect(state.user).toBeNull();
      expect(state.token).toBeNull();
    });
  });

  describe('login', () => {
    it('成功 → 调用 authService.login 并设置登录态', async () => {
      vi.mocked(authService.login).mockResolvedValue(mockLoginResponse);

      await useUserStore.getState().login({ email: 'test@example.com', password: 'pass' });

      expect(authService.login).toHaveBeenCalledWith({ email: 'test@example.com', password: 'pass' });
      const state = useUserStore.getState();
      expect(state.isLoggedIn).toBe(true);
      expect(state.token).toBe('jwt-token-xxx');
      expect(state.user?.user_id).toBe('u1');
    });
  });

  describe('register', () => {
    it('成功 → 调用 authService.register 并设置登录态', async () => {
      vi.mocked(authService.register).mockResolvedValue(mockLoginResponse);

      await useUserStore.getState().register({ email: 'new@example.com', password: 'pass', name: 'New' });

      expect(authService.register).toHaveBeenCalledWith({ email: 'new@example.com', password: 'pass', name: 'New' });
      const state = useUserStore.getState();
      expect(state.isLoggedIn).toBe(true);
      expect(state.user?.user_id).toBe('u1');
    });
  });

  describe('logout', () => {
    it('后端登出成功 → 清除登录态', async () => {
      useUserStore.setState({ user: mockUser, token: 'tok', isLoggedIn: true });
      vi.mocked(authService.logout).mockResolvedValue(undefined);

      await useUserStore.getState().logout();

      const state = useUserStore.getState();
      expect(state.isLoggedIn).toBe(false);
      expect(state.token).toBeNull();
      expect(state.user).toBeNull();
    });

    it('后端登出失败 → 仍清除本地登录态（finally 容错）', async () => {
      useUserStore.setState({ user: mockUser, token: 'tok', isLoggedIn: true });
      vi.mocked(authService.logout).mockRejectedValue(new Error('network'));

      await useUserStore.getState().logout();

      const state = useUserStore.getState();
      // 关键容错：即使后端登出失败，本地状态也必须清除
      expect(state.isLoggedIn).toBe(false);
      expect(state.token).toBeNull();
      expect(state.user).toBeNull();
    });
  });
});
