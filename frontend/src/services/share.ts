import apiClient, { unwrap } from './api';
import type { ApiResponse } from '@/types/api';

/** 聊天会话分享创建结果 */
export interface ChatShareResult {
  share_id: string;
  title: string;
  share_url: string;
  message_count: number;
  created_at: string;
}

/** 聊天会话分享详情（只读浏览） */
export interface SharedChatInfo {
  share_id: string;
  title: string;
  owner_name: string;
  is_owner: boolean;
  permission: string;
  created_at: string;
  messages: Array<{
    role: 'user' | 'assistant';
    content: string;
    created_at: string;
  }>;
}

/** 创建聊天会话分享链接 */
export async function createChatShare(convId: string): Promise<ChatShareResult> {
  const res = await apiClient.post<ApiResponse<ChatShareResult>>('/chat-share', {
    conv_id: convId,
  });
  return unwrap(res);
}

/** 获取聊天会话分享内容（只读） */
export async function getChatShare(shareId: string): Promise<SharedChatInfo> {
  const res = await apiClient.get<ApiResponse<SharedChatInfo>>(`/chat-share/${shareId}`);
  return unwrap(res);
}

/** 撤销聊天会话分享 */
export async function revokeChatShare(shareId: string): Promise<void> {
  await apiClient.delete(`/chat-share/${shareId}`);
}
