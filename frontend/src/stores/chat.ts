import { create } from 'zustand';
import type { Conversation, Message, ChatMeta } from '@/types/chat';
import * as chatService from '@/services/chat';
import type { SetState, GetState } from 'zustand';

/** 单个会话的流式状态（思考文案 + 已流出的答案） */
export interface ConvStreamState {
  content: string;
  thinking: string;
}

interface ChatState {
  conversations: Conversation[];
  currentConvId: string | null;
  messages: Record<string, Message[]>;
  /** 每个正在流式生成的会话的独立流式状态（key = convId，新会话用 tempId） */
  streamingByConv: Record<string, ConvStreamState>;
  /** 每个会话的独立排队队列（该会话流式进行中时，后续输入入队） */
  queueByConv: Record<string, string[]>;

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
  /** 取消某会话的流式（清空该会话的流式状态与队列） */
  cancelStream: (convId: string) => void;
  clearQueue: (convId: string) => void;
  addMessage: (convId: string, message: Message) => void;
  /** 内部：某会话流结束后发送其队列下一条 */
  flushQueueFor: (convId: string) => void;
  /** 内部：直接向指定会话发送（队列续发，不切换 currentConvId） */
  sendMessageToConv: (convId: string, content: string) => Promise<void>;
}

/** 每个会话的 AbortController（不放入 state，避免序列化与多余渲染） */
const streamControllers = new Map<string, AbortController>();

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
 * 核心流式请求：发起 SSE + 事件回调持久化（按会话隔离）。
 *
 * @param cfg.sendConvId    传给后端的 conv_id（'' 表示后端新建会话）
 * @param cfg.displayConvId 前端展示/持久化的会话 key（新会话为 tempId）
 * @param cfg.isTemp        是否为新会话（需要 temp→real 迁移）
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
      set((state) => ({
        streamingByConv: {
          ...state.streamingByConv,
          [displayConvId]: { content: fullContent, thinking: '' },
        },
      }));
    },
    // onDone
    (meta: ChatMeta) => {
      const realConvId = meta.conversation_id || '';
      if (!realConvId) {
        // 无效 conversation_id，不迁移消息，仅结束该会话的流式状态
        set((state) => {
          const streamingByConv = { ...state.streamingByConv };
          delete streamingByConv[displayConvId];
          return { streamingByConv };
        });
        get().flushQueueFor(displayConvId);
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
        // 迁移流式状态与队列到最终会话 key（temp→real）
        const streamingByConv = { ...state.streamingByConv };
        delete streamingByConv[displayConvId];
        const queueByConv = { ...state.queueByConv };
        if (isTemp && displayConvId !== realConvId) {
          queueByConv[realConvId] = queueByConv[displayConvId] || [];
          delete queueByConv[displayConvId];
        }
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
        // 仅当用户仍停留在本会话时才跟随迁移后的真实会话 ID（避免切走后又跳回）
        const currentConvId =
          isTemp && state.currentConvId === displayConvId ? realConvId : state.currentConvId;
        return { messages, conversations, streamingByConv, queueByConv, currentConvId };
      });

      // 刷新会话列表（首次发消息时后端自动创建会话，列表里还没有这条记录）
      void get().loadConversations();
      // 该会话回复结束，自动发送其排队的下一条
      get().flushQueueFor(finalConvId);
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
        const streamingByConv = { ...state.streamingByConv };
        delete streamingByConv[displayConvId];
        return {
          messages: { ...state.messages, [displayConvId]: convMsgs },
          streamingByConv,
        };
      });
      get().flushQueueFor(displayConvId);
    },
    // onThinking
    (thinking) => {
      set((state) => ({
        streamingByConv: {
          ...state.streamingByConv,
          [displayConvId]: {
            content: state.streamingByConv[displayConvId]?.content || '',
            thinking,
          },
        },
      }));
    },
  );

  streamControllers.set(displayConvId, controller);
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  currentConvId: null,
  messages: {},
  streamingByConv: {},
  queueByConv: {},

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
    set({ currentConvId: convId });
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
        const streamingByConv = { ...state.streamingByConv };
        delete streamingByConv[convId];
        const queueByConv = { ...state.queueByConv };
        delete queueByConv[convId];
        return {
          conversations: convs,
          messages: msgs,
          streamingByConv,
          queueByConv,
          currentConvId: state.currentConvId === convId ? null : state.currentConvId,
        };
      });
      streamControllers.get(convId)?.abort();
      streamControllers.delete(convId);
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
      set((state) => {
        const convs = state.conversations.map((c) =>
          c.conv_id === convId ? { ...c, pinned } : c
        );
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
    const convId = get().currentConvId || '';

    // 当前会话正在流式：入该会话的队列（不打断，也不污染其它会话）
    if (convId && get().streamingByConv[convId]) {
      set((state) => ({
        queueByConv: {
          ...state.queueByConv,
          [convId]: [...(state.queueByConv[convId] || []), content],
        },
      }));
      return;
    }

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
        streamingByConv: {
          ...state.streamingByConv,
          [tempConvId]: { content: '', thinking: '' },
        },
      };
    });

    await runStream(get, set, {
      sendConvId: convId,
      displayConvId: tempConvId,
      content,
      isTemp: isNewConv,
    });
  },

  regenerateAssistant: async (convId: string, userMessageId: string, userContent: string) => {
    if (!convId || !get().messages[convId]) return;
    // 该会话已在流式：忽略（避免并发重生成）
    if (get().streamingByConv[convId]) return;

    set((state) => {
      const convMsgs = state.messages[convId] || [];
      const userIdx = findUserIndex(convMsgs, userMessageId, userContent);
      const truncated = userIdx >= 0 ? convMsgs.slice(0, userIdx + 1) : convMsgs;
      return {
        messages: { ...state.messages, [convId]: truncated },
        streamingByConv: {
          ...state.streamingByConv,
          [convId]: { content: '', thinking: '' },
        },
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
    if (!convId || !get().messages[convId]) return;
    if (get().streamingByConv[convId]) return;

    set((state) => {
      const convMsgs = state.messages[convId] || [];
      const userIdx = convMsgs.findIndex((m) => m.message_id === messageId && m.role === 'user');
      if (userIdx < 0) return {};
      const updated = [...convMsgs];
      updated[userIdx] = { ...updated[userIdx], content: newContent };
      const truncated = updated.slice(0, userIdx + 1);
      return {
        messages: { ...state.messages, [convId]: truncated },
        streamingByConv: {
          ...state.streamingByConv,
          [convId]: { content: '', thinking: '' },
        },
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

  /** 某会话流结束后，发送其队列中的下一条（不跨会话） */
  flushQueueFor: (convId: string) => {
    const queue = get().queueByConv[convId] || [];
    if (get().streamingByConv[convId] || queue.length === 0) return;
    const next = queue[0];
    set((state) => ({
      queueByConv: { ...state.queueByConv, [convId]: state.queueByConv[convId].slice(1) },
    }));
    void get().sendMessageToConv(convId, next);
  },

  /** 直接向指定会话发送（用于队列续发；不切换 currentConvId） */
  sendMessageToConv: async (convId: string, content: string) => {
    const userMsg: Message = {
      message_id: `temp_${Date.now()}`,
      conv_id: convId,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    set((state) => {
      const convMsgs = [...(state.messages[convId] || []), userMsg];
      return {
        messages: { ...state.messages, [convId]: convMsgs },
        streamingByConv: {
          ...state.streamingByConv,
          [convId]: { content: '', thinking: '' },
        },
      };
    });
    await runStream(get, set, {
      sendConvId: convId,
      displayConvId: convId,
      content,
      isTemp: false,
    });
  },

  clearQueue: (convId: string) => {
    set((state) => {
      const queueByConv = { ...state.queueByConv };
      delete queueByConv[convId];
      return { queueByConv };
    });
  },

  cancelStream: (convId: string) => {
    const controller = streamControllers.get(convId);
    if (controller) {
      controller.abort();
      streamControllers.delete(convId);
    }
    set((state) => {
      const streamingByConv = { ...state.streamingByConv };
      delete streamingByConv[convId];
      const queueByConv = { ...state.queueByConv };
      delete queueByConv[convId];
      return { streamingByConv, queueByConv };
    });
  },

  addMessage: (convId: string, message: Message) => {
    set((state) => {
      const convMsgs = [...(state.messages[convId] || []), message];
      return { messages: { ...state.messages, [convId]: convMsgs } };
    });
  },
}));
