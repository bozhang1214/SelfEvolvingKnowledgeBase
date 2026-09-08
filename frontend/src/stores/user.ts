import { create } from 'zustand';
import type { User, LoginRequest, RegisterRequest } from '@/types/user';
import * as authService from '@/services/auth';
import { logger, maskEmail, maskToken } from '@/utils/logger';

interface UserState {
  user: User | null;
  token: string | null;
  isLoggedIn: boolean;

  init: () => void;
  login: (data: LoginRequest) => Promise<void>;
  register: (data: RegisterRequest) => Promise<void>;
  logout: () => Promise<void>;
}

/** 同步从 localStorage 读取会话（模块加载时执行，避免刷新后闪现登录页）。 */
function readStoredSession(): { user: User | null; token: string | null; isLoggedIn: boolean } {
  const token = localStorage.getItem('sekb_token');
  const raw = localStorage.getItem('sekb_user');
  if (!token || !raw) {
    return { user: null, token: null, isLoggedIn: false };
  }
  try {
    const user = JSON.parse(raw) as User;
    return { user, token, isLoggedIn: true };
  } catch {
    localStorage.removeItem('sekb_token');
    localStorage.removeItem('sekb_user');
    return { user: null, token: null, isLoggedIn: false };
  }
}

const stored = readStoredSession();

export const useUserStore = create<UserState>((set) => ({
  user: stored.user,
  token: stored.token,
  isLoggedIn: stored.isLoggedIn,

  // 重新校验并恢复会话（覆盖模块加载后 localStorage 的变化，并清理无效 token），再后台静默续租。
  init: () => {
    const session = readStoredSession();
    set({ user: session.user, token: session.token, isLoggedIn: session.isLoggedIn });
    if (!session.isLoggedIn) return;
    authService
      .ensureFreshToken()
      .then((fresh) => {
        if (!fresh) {
          // token 已过期且续租失败：登出
          logger.warn('session_expired', { reason: 'token 过期且续租失败' });
          localStorage.removeItem('sekb_token');
          localStorage.removeItem('sekb_user');
          set({ user: null, token: null, isLoggedIn: false });
        } else {
          // 续租成功：token 已写入 localStorage，刷新内存中的 token 引用
          set({ token: localStorage.getItem('sekb_token') });
        }
      })
      .catch((err: any) => {
        logger.warn('token_refresh_failed', { msg: err?.message });
      });
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
