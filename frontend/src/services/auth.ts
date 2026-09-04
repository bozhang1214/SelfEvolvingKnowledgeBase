import apiClient from './api';
import type { LoginRequest, LoginResponse, RegisterRequest, User } from '@/types/user';

export async function login(data: LoginRequest): Promise<LoginResponse> {
  const res = await apiClient.post<LoginResponse>('/auth/login', data);
  const { user, token } = res.data;
  localStorage.setItem('sekb_token', token);
  localStorage.setItem('sekb_user', JSON.stringify(user));
  return res.data;
}

export async function register(data: RegisterRequest): Promise<LoginResponse> {
  const res = await apiClient.post<LoginResponse>('/auth/register', data);
  const { user, token } = res.data;
  localStorage.setItem('sekb_token', token);
  localStorage.setItem('sekb_user', JSON.stringify(user));
  return res.data;
}

export async function logout(): Promise<void> {
  try {
    await apiClient.post('/auth/logout');
  } finally {
    localStorage.removeItem('sekb_token');
    localStorage.removeItem('sekb_user');
  }
}

export async function getMe(): Promise<User> {
  const res = await apiClient.get<User>('/auth/me');
  return res.data;
}

export function getStoredUser(): User | null {
  const raw = localStorage.getItem('sekb_user');
  return raw ? JSON.parse(raw) : null;
}

export function getToken(): string | null {
  return localStorage.getItem('sekb_token');
}

/** 解码 JWT 的 exp 时间戳（毫秒），失败返回 null。 */
function decodeJwtExp(token: string): number | null {
  try {
    const part = token.split('.')[1];
    const payload = JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/')));
    return typeof payload.exp === 'number' ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

/** 滑动续租：用当前仍有效的 token 换取新的 90 天 token。 */
export async function refreshToken(): Promise<string | null> {
  try {
    const res = await apiClient.post<LoginResponse>('/auth/refresh');
    localStorage.setItem('sekb_token', res.data.token);
    return res.data.token;
  } catch {
    return null;
  }
}

/**
 * 自动续租：token 临近过期（<7 天）时刷新；返回 token 是否仍可用。
 * 用于进入页面时静默续租，实现「90 天有效期 + 到期自动续租」。
 */
export async function ensureFreshToken(): Promise<boolean> {
  const token = getToken();
  if (!token) return false;
  const exp = decodeJwtExp(token);
  if (exp === null) return true; // 无法解码，交给后端校验
  const SEVEN_DAYS = 7 * 24 * 3600 * 1000;
  if (exp - Date.now() < SEVEN_DAYS) {
    return (await refreshToken()) !== null;
  }
  return true;
}
