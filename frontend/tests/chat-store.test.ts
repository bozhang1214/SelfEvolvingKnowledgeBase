/**
 * chat store 单元测试
 *
 * 覆盖 stores/chat.ts 的会话状态管理逻辑：
 * - loadConversations：成功/失败（静默）
 * - selectConversation：首次加载消息并缓存，再次不重复加载
 * - createConversation：加入列表头部 + 返回 conv_id；失败返回空串
 * - deleteConversation：移除会话+消息，清理 currentConvId
 * - renameConversation：更新会话标题
 * - sendMessage：isStreaming 时防重发
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@/services/chat', () => ({
  listConversations: vi.fn(),
  getMessages: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  updateConversation: vi.fn(),
  streamChat: vi.fn(() => new AbortController()),
}));

import { useChatStore } from '@/stores/chat';
import * as chatService from '@/services/chat';
import type { Conversation, Message } from '@/types/chat';

const makeConv = (id: string, title = `会话${id}`): Conversation => ({
  conv_id: id,
  title,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  message_count: 0,
  user_id: 'u1',
});

const makeMsg = (id: string, convId: string, role: 'user' | 'assistant', content: string): Message => ({
  message_id: id,
  conv_id: convId,
  role,
  content,
  created_at: '2026-01-01T00:00:00Z',
});

describe('chat store', () => {
  beforeEach(() => {
    useChatStore.setState({
      conversations: [],
      currentConvId: null,
      messages: {},
      isStreaming: false,
      streamingContent: '',
    });
    vi.mocked(chatService.listConversations).mockReset();
    vi.mocked(chatService.getMessages).mockReset();
    vi.mocked(chatService.createConversation).mockReset();
    vi.mocked(chatService.deleteConversation).mockReset();
    vi.mocked(chatService.updateConversation).mockReset();
    vi.mocked(chatService.streamChat).mockReset();
    vi.mocked(chatService.streamChat).mockReturnValue(new AbortController());
    delete (window as any).__stream_controller;
  });

  describe('loadConversations', () => {
    it('成功 → 设置 conversations', async () => {
      const convs = [makeConv('c1'), makeConv('c2')];
      vi.mocked(chatService.listConversations).mockResolvedValue(convs);

      await useChatStore.getState().loadConversations();

      expect(useChatStore.getState().conversations).toEqual(convs);
    });

    it('失败 → 静默处理，不抛错且不改变列表', async () => {
      vi.mocked(chatService.listConversations).mockRejectedValue(new Error('net'));
      useChatStore.setState({ conversations: [makeConv('c1')] });

      await expect(useChatStore.getState().loadConversations()).resolves.toBeUndefined();
      expect(useChatStore.getState().conversations).toHaveLength(1);
    });
  });

  describe('selectConversation', () => {
    it('首次选择 → 加载消息并缓存到 messages', async () => {
      const msgs = [makeMsg('m1', 'c1', 'user', 'hi')];
      vi.mocked(chatService.getMessages).mockResolvedValue(msgs);

      await useChatStore.getState().selectConversation('c1');

      expect(chatService.getMessages).toHaveBeenCalledWith('c1');
      expect(useChatStore.getState().messages['c1']).toEqual(msgs);
      expect(useChatStore.getState().currentConvId).toBe('c1');
    });

    it('再次选择同一会话 → 不重复加载消息', async () => {
      const msgs = [makeMsg('m1', 'c1', 'user', 'hi')];
      // 预置已缓存的消息
      useChatStore.setState({ messages: { c1: msgs } });

      await useChatStore.getState().selectConversation('c1');

      expect(chatService.getMessages).not.toHaveBeenCalled();
    });
  });

  describe('createConversation', () => {
    it('成功 → 加入列表头部并设置 currentConvId，返回 conv_id', async () => {
      const conv = makeConv('cNew', '新会话');
      vi.mocked(chatService.createConversation).mockResolvedValue(conv);

      const id = await useChatStore.getState().createConversation();

      expect(id).toBe('cNew');
      const state = useChatStore.getState();
      expect(state.currentConvId).toBe('cNew');
      expect(state.conversations[0].conv_id).toBe('cNew');
    });

    it('失败 → 返回空字符串，不修改列表', async () => {
      vi.mocked(chatService.createConversation).mockRejectedValue(new Error('net'));

      const id = await useChatStore.getState().createConversation();

      expect(id).toBe('');
      expect(useChatStore.getState().conversations).toHaveLength(0);
    });
  });

  describe('deleteConversation', () => {
    it('成功 → 移除会话+消息，且 currentConvId 被清理', async () => {
      useChatStore.setState({
        conversations: [makeConv('c1'), makeConv('c2')],
        messages: { c1: [makeMsg('m1', 'c1', 'user', 'hi')] },
        currentConvId: 'c1',
      });
      vi.mocked(chatService.deleteConversation).mockResolvedValue(undefined);

      await useChatStore.getState().deleteConversation('c1');

      const state = useChatStore.getState();
      expect(state.conversations.find((c) => c.conv_id === 'c1')).toBeUndefined();
      expect(state.messages['c1']).toBeUndefined();
      // 删除当前会话 → currentConvId 置空
      expect(state.currentConvId).toBeNull();
    });

    it('删除非当前会话 → currentConvId 不变', async () => {
      useChatStore.setState({
        conversations: [makeConv('c1'), makeConv('c2')],
        currentConvId: 'c2',
      });
      vi.mocked(chatService.deleteConversation).mockResolvedValue(undefined);

      await useChatStore.getState().deleteConversation('c1');

      expect(useChatStore.getState().currentConvId).toBe('c2');
    });
  });

  describe('renameConversation', () => {
    it('成功 → 更新对应会话标题', async () => {
      useChatStore.setState({ conversations: [makeConv('c1', '旧标题')] });
      const renamed = makeConv('c1', '新标题');
      vi.mocked(chatService.updateConversation).mockResolvedValue(renamed);

      await useChatStore.getState().renameConversation('c1', '新标题');

      expect(chatService.updateConversation).toHaveBeenCalledWith('c1', { title: '新标题' });
      expect(useChatStore.getState().conversations[0].title).toBe('新标题');
    });
  });

  describe('sendMessage: 防重发', () => {
    it('isStreaming=true → 不调用 streamChat', async () => {
      useChatStore.setState({ isStreaming: true, currentConvId: 'c1' });

      await useChatStore.getState().sendMessage('hello');

      expect(chatService.streamChat).not.toHaveBeenCalled();
    });
  });

  describe('sendMessage: onDone 会话 ID 处理', () => {
    it('onDone 返回有效 conversation_id → 更新 currentConvId 并迁移消息', async () => {
      // 捕获 onDone 回调
      let onDoneCb: ((meta: any) => void) | null = null;
      vi.mocked(chatService.streamChat).mockImplementation(
        (_convId, _msg, _onToken, onDone, _onError) => {
          onDoneCb = onDone;
          return new AbortController();
        }
      );

      await useChatStore.getState().sendMessage('hello');
      // 首次发消息时 convId 为空串
      expect(chatService.streamChat).toHaveBeenCalledWith('', 'hello', expect.any(Function), expect.any(Function), expect.any(Function));

      // 模拟后端返回真实 conversation_id
      onDoneCb!({ conversation_id: 'real-conv-1' } as any);

      const state = useChatStore.getState();
      expect(state.currentConvId).toBe('real-conv-1');
      expect(state.isStreaming).toBe(false);
      // 真实会话下应有 user + assistant 两条消息
      expect(state.messages['real-conv-1']).toHaveLength(2);
      expect(state.messages['real-conv-1'][1].role).toBe('assistant');
    });

    it('onDone 返回空 conversation_id → 不更新 currentConvId，仅结束 streaming', async () => {
      let onDoneCb: ((meta: any) => void) | null = null;
      vi.mocked(chatService.streamChat).mockImplementation(
        (_convId, _msg, _onToken, onDone, _onError) => {
          onDoneCb = onDone;
          return new AbortController();
        }
      );

      await useChatStore.getState().sendMessage('hello');
      const tempIdBefore = useChatStore.getState().currentConvId;
      expect(tempIdBefore).toMatch(/^temp_/);

      // 模拟异常 done（无 conversation_id）
      onDoneCb!({} as any);

      const state = useChatStore.getState();
      expect(state.isStreaming).toBe(false);
      // currentConvId 仍为 temp_xxx，未被设为空或无效值
      expect(state.currentConvId).toBe(tempIdBefore);
      // 消息仍留在 temp 会话下，不迁移
      expect(state.messages[tempIdBefore]).toHaveLength(1);
    });
  });
});
