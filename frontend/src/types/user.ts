export interface User {
  user_id: string;
  email: string;
  name: string;
  avatar_url: string;
  created_at: string;
  is_active: boolean;
  settings: UserSettings;
}

export interface UserSettings {
  model: string;
  temperature: number;
  max_tokens: number;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  name?: string;
}

export interface LoginResponse {
  user: User;
  token: string;
}