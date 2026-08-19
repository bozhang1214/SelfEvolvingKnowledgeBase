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
