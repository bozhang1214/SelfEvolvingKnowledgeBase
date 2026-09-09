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
  pinned: false,
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
      streamingByConv: {},
      queueByConv: {},
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

  describe('togglePin', () => {
    it('置顶 → 调用 API 并将置顶会话排到列表首位', async () => {
      const c1 = makeConv('c1', '会话1');
      c1.updated_at = '2026-01-02T00:00:00Z';
      const c2 = makeConv('c2', '会话2');
      c2.updated_at = '2026-01-01T00:00:00Z';
      useChatStore.setState({ conversations: [c1, c2] });

      vi.mocked(chatService.updateConversation).mockResolvedValue({ ...c2, pinned: true });

      await useChatStore.getState().togglePin('c2', true);

      expect(chatService.updateConversation).toHaveBeenCalledWith('c2', { pinned: true });
      const state = useChatStore.getState();
      // c2 置顶后排到首位
      expect(state.conversations[0].conv_id).toBe('c2');
      expect(state.conversations[0].pinned).toBe(true);
      expect(state.conversations[1].conv_id).toBe('c1');
    });

    it('取消置顶 → 调用 API 并恢复正常排序', async () => {
      const c1 = makeConv('c1', '会话1');
      c1.pinned = true;
      c1.updated_at = '2026-01-01T00:00:00Z';
      const c2 = makeConv('c2', '会话2');
      c2.updated_at = '2026-01-02T00:00:00Z';
      useChatStore.setState({ conversations: [c1, c2] });

      vi.mocked(chatService.updateConversation).mockResolvedValue({ ...c1, pinned: false });

      await useChatStore.getState().togglePin('c1', false);

      expect(chatService.updateConversation).toHaveBeenCalledWith('c1', { pinned: false });
      const state = useChatStore.getState();
      // 取消置顶后 c1 不再排首位，c2 更新时间更晚所以排第一
      expect(state.conversations[0].conv_id).toBe('c2');
      expect(state.conversations[1].conv_id).toBe('c1');
      expect(state.conversations[1].pinned).toBe(false);
    });

    it('API 失败 → 静默处理不抛异常', async () => {
      useChatStore.setState({ conversations: [makeConv('c1')] });
      vi.mocked(chatService.updateConversation).mockRejectedValue(new Error('network'));

      await expect(useChatStore.getState().togglePin('c1', true)).resolves.not.toThrow();
    });
  });

  describe('sendMessage: 防重发', () => {
    it('当前会话已在流式 → 入队而不调用 streamChat', async () => {
      useChatStore.setState({
        currentConvId: 'c1',
        streamingByConv: { c1: { content: '', thinking: '' } },
        queueByConv: {},
      });

      await useChatStore.getState().sendMessage('hello');

      expect(chatService.streamChat).not.toHaveBeenCalled();
      // 已入该会话的队列
      expect(useChatStore.getState().queueByConv['c1']).toEqual(['hello']);
    });
  });

  describe('sendMessage: onDone 会话 ID 处理', () => {
    it('onDone 返回有效 conversation_id → 更新 currentConvId 并迁移消息', async () => {
      // 捕获 onDone 回调
      let onDoneCb: ((meta: any) => void) | null = null;
      vi.mocked(chatService.streamChat).mockImplementation(
        (_convId, _msg, _onToken, onDone, _onError, _onThinking) => {
          onDoneCb = onDone;
          return new AbortController();
        }
      );

      await useChatStore.getState().sendMessage('hello');
      // 首次发消息时 convId 为空串，无 skill 参数
      expect(chatService.streamChat).toHaveBeenCalledWith('', 'hello', expect.any(Function), expect.any(Function), expect.any(Function), expect.any(Function));

      // 模拟后端返回真实 conversation_id
      onDoneCb!({ conversation_id: 'real-conv-1' } as any);

      const state = useChatStore.getState();
      expect(state.currentConvId).toBe('real-conv-1');
      expect(state.streamingByConv['real-conv-1']).toBeUndefined();
      // 真实会话下应有 user + assistant 两条消息
      expect(state.messages['real-conv-1']).toHaveLength(2);
      expect(state.messages['real-conv-1'][1].role).toBe('assistant');
    });

    it('onDone 返回空 conversation_id → 不更新 currentConvId，仅结束 streaming', async () => {
      let onDoneCb: ((meta: any) => void) | null = null;
      vi.mocked(chatService.streamChat).mockImplementation(
        (_convId, _msg, _onToken, onDone, _onError, _onThinking) => {
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
      expect(state.streamingByConv[tempIdBefore]).toBeUndefined();
      // currentConvId 仍为 temp_xxx，未被设为空或无效值
      expect(state.currentConvId).toBe(tempIdBefore);
      // 消息仍留在 temp 会话下，不迁移
      expect(state.messages[tempIdBefore]).toHaveLength(1);
    });
  });

  describe('streaming 会话隔离（切走不串台）', () => {
    it('发送后 streamingByConv 记录当前会话', async () => {
      useChatStore.setState({ currentConvId: 'c1', messages: { c1: [] } });
      await useChatStore.getState().sendMessage('hello');
      expect(useChatStore.getState().streamingByConv['c1']).toBeDefined();
    });

    it('切到其它会话后 done 不把 currentConvId 跳回', async () => {
      let onDoneCb: ((meta: any) => void) | null = null;
      vi.mocked(chatService.streamChat).mockImplementation(
        (_convId, _msg, _onToken, onDone, _onError, _onThinking) => {
          onDoneCb = onDone;
          return new AbortController();
        }
      );

      // 在 c1 发送（c1 已存在，isTemp=false）
      useChatStore.setState({
        currentConvId: 'c1',
        messages: { c1: [], c2: [] },
      });
      await useChatStore.getState().sendMessage('hello');

      // 用户切到 c2
      await useChatStore.getState().selectConversation('c2');
      expect(useChatStore.getState().currentConvId).toBe('c2');

      // c1 的流完成，不应把 currentConvId 拉回 c1
      onDoneCb!({ conversation_id: 'c1', intent: '', metrics: {} } as any);

      expect(useChatStore.getState().currentConvId).toBe('c2');
      expect(useChatStore.getState().streamingByConv['c1']).toBeUndefined();
      // 回答仍写入 c1
      expect(useChatStore.getState().messages['c1'].some((m) => m.role === 'assistant')).toBe(true);
    });

    it('新会话(temp)切走后 done 不迁移 currentConvId', async () => {
      let onDoneCb: ((meta: any) => void) | null = null;
      vi.mocked(chatService.streamChat).mockImplementation(
        (_convId, _msg, _onToken, onDone, _onError, _onThinking) => {
          onDoneCb = onDone;
          return new AbortController();
        }
      );
      useChatStore.setState({ currentConvId: null, messages: {}, conversations: [] });

      await useChatStore.getState().sendMessage('hello');
      const tempId = useChatStore.getState().currentConvId;
      expect(tempId).toMatch(/^temp_/);

      // 切到已有会话 c2
      await useChatStore.getState().selectConversation('c2');

      onDoneCb!({ conversation_id: 'real-1', intent: '', metrics: {} } as any);

      expect(useChatStore.getState().currentConvId).toBe('c2');
      expect(useChatStore.getState().messages['real-1']).toBeDefined();
    });

    it('两会话可独立流式：c1 流式期间切 c2 发送，c2 独立发起不排队', async () => {
      const cbs: Record<string, ((meta: any) => void) | null> = {};
      vi.mocked(chatService.streamChat).mockImplementation(
        (convId, _msg, _onToken, onDone, _onError, _onThinking) => {
          cbs[convId] = onDone;
          return new AbortController();
        }
      );

      useChatStore.setState({ currentConvId: 'c1', messages: { c1: [], c2: [] } });
      await useChatStore.getState().sendMessage('c1 的问题');
      expect(useChatStore.getState().streamingByConv['c1']).toBeDefined();

      // 切到 c2 发送
      await useChatStore.getState().selectConversation('c2');
      await useChatStore.getState().sendMessage('c2 的问题');

      // c2 独立发起（不是排队），且 c1 仍在流式
      expect(cbs['c2']).toBeDefined();
      expect(useChatStore.getState().queueByConv['c2']).toBeUndefined();
      expect(useChatStore.getState().streamingByConv['c1']).toBeDefined();
      expect(useChatStore.getState().streamingByConv['c2']).toBeDefined();
    });
  });

  describe('regenerateAssistant', () => {
    it('截断到用户消息并重发同内容', async () => {
      const convId = 'c1';
      useChatStore.setState({
        currentConvId: convId,
        messages: {
          [convId]: [
            makeMsg('u1', convId, 'user', '问题1'),
            makeMsg('a1', convId, 'assistant', '回答1'),
          ],
        },
      });

      await useChatStore.getState().regenerateAssistant(convId, 'u1', '问题1');

      // 发送到后端的 convId 为真实会话（非 temp），content 为用户内容
      expect(chatService.streamChat).toHaveBeenCalledWith(
        convId, '问题1', expect.any(Function), expect.any(Function), expect.any(Function), expect.any(Function)
      );
      // 旧 assistant 消息已从本地截断，只保留 user
      const msgs = useChatStore.getState().messages[convId];
      expect(msgs).toHaveLength(1);
      expect(msgs[0].role).toBe('user');
      expect(useChatStore.getState().streamingByConv[convId]).toBeDefined();
    });
  });

  describe('editUserMessage', () => {
    it('替换用户消息内容并删除其后 assistant 消息', async () => {
      const convId = 'c1';
      useChatStore.setState({
        currentConvId: convId,
        messages: {
          [convId]: [
            makeMsg('u1', convId, 'user', '原问题'),
            makeMsg('a1', convId, 'assistant', '旧回答'),
          ],
        },
      });

      await useChatStore.getState().editUserMessage(convId, 'u1', '新问题');

      expect(chatService.streamChat).toHaveBeenCalledWith(
        convId, '新问题', expect.any(Function), expect.any(Function), expect.any(Function), expect.any(Function)
      );
      const msgs = useChatStore.getState().messages[convId];
      expect(msgs).toHaveLength(1);
      expect(msgs[0].content).toBe('新问题'); // 内容已替换，其后 assistant 已删除
      expect(useChatStore.getState().streamingByConv[convId]).toBeDefined();
    });
  });
});
