import apiClient from './api';
import type { ApiResponse } from '@/types/api';
import type { Conversation, Message, ChatMeta } from '@/types/chat';

export async function listConversations(): Promise<Conversation[]> {
  const res = await apiClient.get<ApiResponse<Conversation[]>>('/conversations');
  return res.data.data;
}

export async function getConversation(convId: string): Promise<Conversation> {
  const res = await apiClient.get<ApiResponse<Conversation>>(`/conversations/${convId}`);
  return res.data.data;
}

export async function getMessages(convId: string): Promise<Message[]> {
  const res = await apiClient.get<ApiResponse<Message[]>>(`/conversations/${convId}/messages`);
  return res.data.data;
}

export async function createConversation(): Promise<Conversation> {
  const res = await apiClient.post<ApiResponse<Conversation>>('/conversations');
  return res.data.data;
}

export async function updateConversation(convId: string, data: { title?: string }): Promise<Conversation> {
  const res = await apiClient.patch<ApiResponse<Conversation>>(`/conversations/${convId}`, data);
  return res.data.data;
}

export async function deleteConversation(convId: string): Promise<void> {
  await apiClient.delete(`/conversations/${convId}`);
}

// SSE 流式聊天
export function streamChat(
  convId: string,
  message: string,
  onToken: (token: string) => void,
  onDone: (meta: ChatMeta) => void,
  onError: (error: string) => void
): AbortController {
  const controller = new AbortController();
  const token = getToken();

  // SSE 通过 Vite 代理转发（开发模式）
  const streamUrl = '/api/v1/chat/stream';

  fetch(streamUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ conversation_id: convId || null, message }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const err = await response.json().catch(() => ({ message: '请求失败' }));
        onError(err.detail || err.message || `HTTP ${response.status}`);
        return;
      }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let pendingBuffer = '';
      let parsed = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        pendingBuffer += decoder.decode(value, { stream: true });

        // 按 \n\n 分割 SSE 事件
        const events = pendingBuffer.split('\n\n');
        pendingBuffer = events.pop() || '';

        for (const eventBlock of events) {
          const dataLines = eventBlock
            .split('\n')
            .filter((l) => l.startsWith('data: '));
          if (dataLines.length === 0) continue;

          const dataStr = dataLines.map((l) => l.slice(6)).join('');
          try {
            const data = JSON.parse(dataStr);
            if (data.type === 'token') {
              onToken(data.content);
            } else if (data.type === 'done') {
              parsed = true;
              onDone(data.meta);
            } else if (data.type === 'error') {
              onError(data.error || data.detail || '未知错误');
            }
          } catch {
            // 忽略解析错误
          }
        }
      }

      if (!parsed) {
        // 流未收到 done 事件就结束（被中断/连接断开），视为错误
        // 不能调用 onDone({})：空 meta 会让 store 回退到 tempConvId，导致下次发消息 404
        onError('流式响应异常结束');
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError(err.message || '网络错误');
      }
    });

  return controller;
}

function getToken(): string | null {
  return localStorage.getItem('sekb_token');
}