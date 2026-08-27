import { create } from 'zustand';
import type { Conversation, Message, ChatMeta } from '@/types/chat';
import * as chatService from '@/services/chat';

interface ChatState {
  conversations: Conversation[];
  currentConvId: string | null;
  messages: Record<string, Message[]>;
  isStreaming: boolean;
  streamingContent: string;
  thinkingContent: string;

  loadConversations: () => Promise<void>;
  selectConversation: (convId: string) => Promise<void>;
  createConversation: () => Promise<string>;
  deleteConversation: (convId: string) => Promise<void>;
  renameConversation: (convId: string, title: string) => Promise<void>;
  togglePin: (convId: string, pinned: boolean) => Promise<void>;
  sendMessage: (content: string) => Promise<void>;
  cancelStream: () => void;
  addMessage: (convId: string, message: Message) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  currentConvId: null,
  messages: {},
  isStreaming: false,
  streamingContent: '',
  thinkingContent: '',

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
      const updated = await chatService.updateConversation(convId, { pinned });
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
    const { currentConvId, conversations, isStreaming } = get();

    // 防止重复发送
    if (isStreaming) return;

    let convId = currentConvId || '';

    // 如果没有当前会话，使用临时 ID 让 UI 立即显示用户消息
    const tempConvId = convId || `temp_${Date.now()}`;
    if (!convId) {
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
      return {
        messages: { ...state.messages, [tempConvId]: convMsgs },
        isStreaming: true,
        streamingContent: '',
      };
    });

    // SSE 流式聊天：首次发消息时 convId 为空串，后端收到 null 会自动创建新会话
    // （不能传 tempConvId，否则后端会校验 temp_xxx 不存在而 404）
    let fullContent = '';
    const controller = chatService.streamChat(
      convId,
      content,
      // onToken
      (token) => {
        fullContent += token;
        // 收到第一个 token 时清除思考提示，切换为正常输出模式
        set({ streamingContent: fullContent, thinkingContent: '' });
      },
      // onDone
      (meta: ChatMeta) => {
        // 后端 done 事件必须返回 conversation_id；为空说明异常，保持 tempConvId 不持久化
        const realConvId = meta.conversation_id || '';
        if (!realConvId) {
          // 无效 conversation_id，不迁移消息，仅结束 streaming 状态
          set({ isStreaming: false, streamingContent: '', thinkingContent: '' });
          return;
        }
        const assistantMsg: Message = {
          message_id: `msg_${Date.now()}`,
          conv_id: realConvId,
          role: 'assistant',
          content: fullContent,
          created_at: new Date().toISOString(),
          metadata: {
            intent: meta.intent || undefined,
            latency_ms: meta.latency_ms,
          },
        };
        set((state) => {
          // 将临时会话的消息迁移到真实会话 ID
          const oldMsgs = state.messages[tempConvId] || [];
          const newMsgs = [...oldMsgs, assistantMsg];
          const messages = { ...state.messages };
          if (tempConvId !== realConvId) {
            delete messages[tempConvId];
          }
          messages[realConvId] = newMsgs;
          return {
            messages,
            isStreaming: false,
            streamingContent: '',
            thinkingContent: '',
            currentConvId: realConvId,
          };
        });

        // 刷新会话列表，让新对话出现在侧边栏
        // （首次发消息时后端自动创建会话，列表里还没有这条记录）
        void get().loadConversations();
      },
      // onError
      (error) => {
        const errMsg: Message = {
          message_id: `err_${Date.now()}`,
          conv_id: convId,
          role: 'assistant',
          content: `**错误**: ${error}`,
          created_at: new Date().toISOString(),
        };
        set((state) => {
          const convMsgs = [...(state.messages[convId] || []), errMsg];
          return {
            messages: { ...state.messages, [convId]: convMsgs },
            isStreaming: false,
            streamingContent: '',
            thinkingContent: '',
          };
        });
      },
      // onThinking
      (content) => {
        set({ thinkingContent: content });
      },
    );

    // 保存 controller 供取消
    (window as any).__stream_controller = controller;
  },

  cancelStream: () => {
    const controller = (window as any).__stream_controller;
    if (controller) {
      controller.abort();
      set({ isStreaming: false, streamingContent: '', thinkingContent: '' });
    }
  },

  addMessage: (convId: string, message: Message) => {
    set((state) => {
      const convMsgs = [...(state.messages[convId] || []), message];
      return { messages: { ...state.messages, [convId]: convMsgs } };
    });
  },
}));