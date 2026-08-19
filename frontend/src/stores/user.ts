import { create } from 'zustand';
import type { User, LoginRequest, RegisterRequest } from '@/types/user';
import * as authService from '@/services/auth';

interface UserState {
  user: User | null;
  token: string | null;
  isLoggedIn: boolean;

  init: () => void;
  login: (data: LoginRequest) => Promise<void>;
  register: (data: RegisterRequest) => Promise<void>;
  logout: () => Promise<void>;
}

export const useUserStore = create<UserState>((set) => ({
  user: null,
  token: null,
  isLoggedIn: false,

  init: () => {
    const token = localStorage.getItem('sekb_token');
    const raw = localStorage.getItem('sekb_user');
    if (token && raw) {
      try {
        const user = JSON.parse(raw) as User;
        set({ user, token, isLoggedIn: true });
      } catch {
        localStorage.removeItem('sekb_token');
        localStorage.removeItem('sekb_user');
      }
    }
  },

  login: async (data: LoginRequest) => {
    const resp = await authService.login(data);
    set({ user: resp.user, token: resp.token, isLoggedIn: true });
  },

  register: async (data: RegisterRequest) => {
    const resp = await authService.register(data);
    set({ user: resp.user, token: resp.token, isLoggedIn: true });
  },

  logout: async () => {
    await authService.logout();
    set({ user: null, token: null, isLoggedIn: false });
  },
}));