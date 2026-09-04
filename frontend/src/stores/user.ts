import { create } from 'zustand';
import type { User, LoginRequest, RegisterRequest } from '@/types/user';
import * as authService from '@/services/auth';
import { logger, maskEmail, maskToken } from '@/utils/logger';

interface UserState {
  user: User | null;
  token: string | null;
  isLoggedIn: boolean;

  init: () => Promise<void>;
  login: (data: LoginRequest) => Promise<void>;
  register: (data: RegisterRequest) => Promise<void>;
  logout: () => Promise<void>;
}

export const useUserStore = create<UserState>((set) => ({
  user: null,
  token: null,
  isLoggedIn: false,

  init: async () => {
    const token = localStorage.getItem('sekb_token');
    const raw = localStorage.getItem('sekb_user');
    if (token && raw) {
      // 自动续租：临近过期时静默换新 token；已过期则登出
      const fresh = await authService.ensureFreshToken();
      if (!fresh) {
        logger.warn('session_expired', { reason: 'token 过期且续租失败' });
        localStorage.removeItem('sekb_token');
        localStorage.removeItem('sekb_user');
        set({ user: null, token: null, isLoggedIn: false });
        return;
      }
      try {
        const user = JSON.parse(raw) as User;
        const freshToken = localStorage.getItem('sekb_token');
        set({ user, token: freshToken, isLoggedIn: true });
        logger.info('session_restore', {
          user_id: user.user_id,
          token: maskToken(freshToken || ''),
        });
      } catch {
        logger.warn('session_restore_failed', { reason: 'invalid_json' });
        localStorage.removeItem('sekb_token');
        localStorage.removeItem('sekb_user');
      }
    }
  },

  login: async (data: LoginRequest) => {
    logger.info('login_attempt', { email: maskEmail(data.email) });
    const resp = await authService.login(data);
    set({ user: resp.user, token: resp.token, isLoggedIn: true });
    logger.info('login_success', {
      user_id: resp.user.user_id,
      token: maskToken(resp.token),
    });
  },

  register: async (data: RegisterRequest) => {
    logger.info('register_attempt', { email: maskEmail(data.email) });
    const resp = await authService.register(data);
    set({ user: resp.user, token: resp.token, isLoggedIn: true });
    logger.info('register_success', {
      user_id: resp.user.user_id,
      token: maskToken(resp.token),
    });
  },

  logout: async () => {
    logger.info('logout_attempt');
    try {
      await authService.logout();
      logger.info('logout_success');
    } catch (err: any) {
      // 即使后端登出失败，本地 token 也应清除
      logger.warn('logout_failed', { msg: err?.message });
    } finally {
      set({ user: null, token: null, isLoggedIn: false });
    }
  },
}));