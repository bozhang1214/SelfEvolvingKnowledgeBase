import { create } from 'zustand';
import type { Conversation, Message, ChatMeta } from '@/types/chat';
import * as chatService from '@/services/chat';
import type { SetState, GetState } from 'zustand';

interface ChatState {
  conversations: Conversation[];
  currentConvId: string | null;
  messages: Record<string, Message[]>;
  isStreaming: boolean;
  streamingContent: string;
  thinkingContent: string;
  /** 排队待发送的消息（回复进行中时新输入进入队列，结束后自动发送） */
  pendingQueue: { content: string }[];
  /** 是否正在从队列里逐条发送（用于显示「已排队 N 条」） */
  queueSending: boolean;

  loadConversations: () => Promise<void>;
  selectConversation: (convId: string) => Promise<void>;
  createConversation: () => Promise<string>;
  deleteConversation: (convId: string) => Promise<void>;
  renameConversation: (convId: string, title: string) => Promise<void>;
  togglePin: (convId: string, pinned: boolean) => Promise<void>;
  sendMessage: (content: string) => Promise<void>;
  /** 重生成：删除指定用户消息之后的 assistant 消息，重新生成并替换 */
  regenerateAssistant: (convId: string, userMessageId: string, userContent: string) => Promise<void>;
  /** 编辑用户消息：替换内容，删除其后消息，重新生成 */
  editUserMessage: (convId: string, messageId: string, newContent: string) => Promise<void>;
  cancelStream: () => void;
  flushQueue: () => void;
  clearQueue: () => void;
  addMessage: (convId: string, message: Message) => void;
}

/** 找到 conv 下某条 user 消息的数组下标（按 message_id 匹配） */
function findUserIndex(messages: Message[], messageId?: string, content?: string): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.role !== 'user') continue;
    if (messageId && m.message_id === messageId) return i;
    if (!messageId && content && m.content === content) return i;
  }
  return -1;
}

/**
 * 核心流式请求：负责发起 SSE + 事件回调持久化。
 *
 * 不负责添加用户消息（由 sendMessage / regenerate / edit 各自决定），
 * 仅负责：
 *   - 置 isStreaming / 清空 streamingContent
 *   - onToken 累加内容、onThinking 更新思考提示
 *   - onDone 追加 assistant 消息到 displayConvId；isTemp 时做 temp→real 迁移
 *   - onError 追加错误消息
 *
 * @param cfg.sendConvId    传给后端的 conv_id（'' 表示后端新建会话）
 * @param cfg.displayConvId 前端展示/持久化的会话 key
 * @param cfg.isTemp        是否为新会话（需要 temp→real 迁移并以 real 重命名会话）
 */
async function runStream(
  get: GetState<ChatState>,
  set: SetState<ChatState>,
  cfg: {
    sendConvId: string;
    displayConvId: string;
    content: string;
    isTemp: boolean;
  },
): Promise<void> {
  const { sendConvId, displayConvId, content, isTemp } = cfg;
  let fullContent = '';
  const controller = chatService.streamChat(
    sendConvId,
    content,
    // onToken
    (token) => {
      fullContent += token;
      // 收到第一个 token 时清除思考提示，切换为正常输出模式
      set({ streamingContent: fullContent, thinkingContent: '' });
    },
    // onDone
    (meta: ChatMeta) => {
      const realConvId = meta.conversation_id || '';
      if (!realConvId) {
        // 无效 conversation_id，不迁移消息，仅结束 streaming 状态
        set({ isStreaming: false, streamingContent: '', thinkingContent: '' });
        get().flushQueue();
        return;
      }
      const finalConvId = isTemp ? realConvId : displayConvId;
      const assistantMsg: Message = {
        message_id: `msg_${Date.now()}`,
        conv_id: finalConvId,
        role: 'assistant',
        content: fullContent,
        created_at: new Date().toISOString(),
        metadata: {
          intent: meta.intent || undefined,
          latency_ms: meta.latency_ms,
        },
      };
      set((state) => {
        const oldMsgs = state.messages[displayConvId] || [];
        const newMsgs = [...oldMsgs, assistantMsg];
        const messages = { ...state.messages };
        if (isTemp && displayConvId !== realConvId) {
          delete messages[displayConvId];
        }
        messages[finalConvId] = newMsgs;
        const conversations = isTemp
          ? state.conversations.map((c) =>
              c.conv_id === displayConvId
                ? { ...c, conv_id: realConvId, message_count: newMsgs.length }
                : c
            )
          : state.conversations.map((c) =>
              c.conv_id === displayConvId
                ? { ...c, message_count: newMsgs.length }
                : c
            );
        return {
          messages,
          conversations,
          isStreaming: false,
          streamingContent: '',
          thinkingContent: '',
          currentConvId: finalConvId,
        };
      });

      // 刷新会话列表（首次发消息时后端自动创建会话，列表里还没有这条记录）
      void get().loadConversations();
      // 当前回复结束，自动发送排队的下一条
      get().flushQueue();
    },
    // onError
    (error) => {
      const errMsg: Message = {
        message_id: `err_${Date.now()}`,
        conv_id: displayConvId,
        role: 'assistant',
        content: `**错误**: ${error}`,
        created_at: new Date().toISOString(),
      };
      set((state) => {
        const convMsgs = [...(state.messages[displayConvId] || []), errMsg];
        return {
          messages: { ...state.messages, [displayConvId]: convMsgs },
          isStreaming: false,
          streamingContent: '',
          thinkingContent: '',
        };
      });
      get().flushQueue();
    },
    // onThinking
    (content2) => {
      set({ thinkingContent: content2 });
    },
  );

  // 保存 controller 供取消
  (window as any).__stream_controller = controller;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  currentConvId: null,
  messages: {},
  isStreaming: false,
  streamingContent: '',
  thinkingContent: '',
  pendingQueue: [],
  queueSending: false,

  loadConversations: async () => {
    try {
      const convs = await chatService.listConversations();
      // 排序：pinned 优先，其次按 updated_at 倒序（后端已排序，前端兜底保证）
      convs.sort((a, b) => {
        if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
        return (b.updated_at || '').localeCompare(a.updated_at || '');
      });
      set({ conversations: convs });
    } catch {
      // 静默处理
    }
  },

  selectConversation: async (convId: string) => {
    set({ currentConvId: convId, streamingContent: '' });
    if (!get().messages[convId]) {
      try {
        const msgs = await chatService.getMessages(convId);
        set((state) => ({
          messages: { ...state.messages, [convId]: msgs },
        }));
      } catch {
        // 静默处理
      }
    }
  },

  createConversation: async () => {
    try {
      const conv = await chatService.createConversation();
      set((state) => ({
        conversations: [conv, ...state.conversations],
        currentConvId: conv.conv_id,
      }));
      return conv.conv_id;
    } catch {
      return '';
    }
  },

  deleteConversation: async (convId: string) => {
    try {
      await chatService.deleteConversation(convId);
      set((state) => {
        const convs = state.conversations.filter((c) => c.conv_id !== convId);
        const msgs = { ...state.messages };
        delete msgs[convId];
        return {
          conversations: convs,
          messages: msgs,
          currentConvId: state.currentConvId === convId ? null : state.currentConvId,
        };
      });
    } catch {
      // 静默处理
    }
  },

  renameConversation: async (convId: string, title: string) => {
    try {
      const conv = await chatService.updateConversation(convId, { title });
      set((state) => ({
        conversations: state.conversations.map((c) =>
          c.conv_id === convId ? conv : c
        ),
      }));
    } catch {
      // 静默处理
    }
  },

  togglePin: async (convId: string, pinned: boolean) => {
    try {
      await chatService.updateConversation(convId, { pinned });
      // 更新后重新排序：pinned 优先
      set((state) => {
        const convs = state.conversations.map((c) =>
          c.conv_id === convId ? { ...c, pinned } : c
        );
        // 排序：pinned 优先，其次按 updated_at 倒序
        convs.sort((a, b) => {
          if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
          return (b.updated_at || '').localeCompare(a.updated_at || '');
        });
        return { conversations: convs };
      });
    } catch {
      // 静默处理
    }
  },

  sendMessage: async (content: string) => {
    const { currentConvId, isStreaming } = get();

    // 回复进行中：新输入进入队列，结束后自动发送，避免误打断
    if (isStreaming) {
      set((state) => ({
        pendingQueue: [...state.pendingQueue, { content }],
        queueSending: true,
      }));
      return;
    }

    const convId = currentConvId || '';
    const tempConvId = convId || `temp_${Date.now()}`;
    const isNewConv = !convId;
    if (isNewConv) {
      set({ currentConvId: tempConvId });
    }

    // 添加用户消息
    const userMsg: Message = {
      message_id: `temp_${Date.now()}`,
      conv_id: tempConvId,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    set((state) => {
      const convMsgs = [...(state.messages[tempConvId] || []), userMsg];
      // 新会话立即加入对话列表，让用户看到（用首条消息前 30 字作为临时标题）
      const conversations = isNewConv
        ? [{
            conv_id: tempConvId,
            title: content.slice(0, 30) || '新对话',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            message_count: 1,
            user_id: '',
            pinned: false,
          }, ...state.conversations]
        : state.conversations;
      return {
        messages: { ...state.messages, [tempConvId]: convMsgs },
        conversations,
        isStreaming: true,
        streamingContent: '',
      };
    });

    // SSE 流式聊天：首次发消息时 sendConvId 为空串，后端收到 null 会自动创建新会话
    await runStream(get, set, {
      sendConvId: convId,
      displayConvId: tempConvId,
      content,
      isTemp: isNewConv,
    });
  },

  regenerateAssistant: async (convId: string, userMessageId: string, userContent: string) => {
    const { isStreaming } = get();
    if (isStreaming) return;
    if (!convId || !get().messages[convId]) return;

    set((state) => {
      const convMsgs = state.messages[convId] || [];
      const userIdx = findUserIndex(convMsgs, userMessageId, userContent);
      // 截断到该用户消息（含），删除其后的 assistant 消息
      const truncated = userIdx >= 0 ? convMsgs.slice(0, userIdx + 1) : convMsgs;
      return {
        messages: { ...state.messages, [convId]: truncated },
        isStreaming: true,
        streamingContent: '',
        thinkingContent: '',
        currentConvId: convId,
      };
    });

    await runStream(get, set, {
      sendConvId: convId,
      displayConvId: convId,
      content: userContent,
      isTemp: false,
    });
  },

  editUserMessage: async (convId: string, messageId: string, newContent: string) => {
    const { isStreaming } = get();
    if (isStreaming) return;
    if (!convId || !get().messages[convId]) return;

    set((state) => {
      const convMsgs = state.messages[convId] || [];
      const userIdx = convMsgs.findIndex((m) => m.message_id === messageId && m.role === 'user');
      if (userIdx < 0) return {};
      // 替换内容，并删除该用户消息之后的所有消息
      const updated = [...convMsgs];
      updated[userIdx] = { ...updated[userIdx], content: newContent };
      const truncated = updated.slice(0, userIdx + 1);
      return {
        messages: { ...state.messages, [convId]: truncated },
        isStreaming: true,
        streamingContent: '',
        thinkingContent: '',
        currentConvId: convId,
      };
    });

    await runStream(get, set, {
      sendConvId: convId,
      displayConvId: convId,
      content: newContent,
      isTemp: false,
    });
  },

  flushQueue: () => {
    const { isStreaming, pendingQueue } = get();
    if (isStreaming || pendingQueue.length === 0) {
      if (!isStreaming && pendingQueue.length === 0) {
        set({ queueSending: false });
      }
      return;
    }
    const next = pendingQueue[0];
    set((state) => ({ pendingQueue: state.pendingQueue.slice(1) }));
    void get().sendMessage(next.content);
  },

  clearQueue: () => {
    set({ pendingQueue: [], queueSending: false });
  },

  cancelStream: () => {
    const controller = (window as any).__stream_controller;
    if (controller) {
      controller.abort();
      // 停止：取消当前流，同时清空排队队列（一次停止，全部停止）
      set({ isStreaming: false, streamingContent: '', thinkingContent: '', pendingQueue: [], queueSending: false });
    }
  },

  addMessage: (convId: string, message: Message) => {
    set((state) => {
      const convMsgs = [...(state.messages[convId] || []), message];
      return { messages: { ...state.messages, [convId]: convMsgs } };
    });
  },
}));
