import React, { useEffect, useRef, useState } from 'react';
import {
  Layout, List, Input, Button, Typography, Space, Spin, Popconfirm, message as antMsg,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, EditOutlined, SendOutlined, StopOutlined,
  PushpinOutlined, PushpinFilled, CopyOutlined, CheckOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@/stores/chat';
import { logger } from '@/utils/logger';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';

const { Text } = Typography;
const { Sider, Content } = Layout;

/** 代码块组件：带语言标签 + 复制按钮 */
const CodeBlock: React.FC<{ language: string; code: string }> = ({ language, code }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div style={{ position: 'relative' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '4px 12px',
          background: '#f0f0f0',
          borderRadius: '4px 4px 0 0',
          fontSize: 12,
          color: '#666',
        }}
      >
        <span>{language}</span>
        <Button
          type="text"
          size="small"
          icon={copied ? <CheckOutlined style={{ color: '#52c41a' }} /> : <CopyOutlined />}
          onClick={handleCopy}
        >
          {copied ? '已复制' : '复制'}
        </Button>
      </div>
      <SyntaxHighlighter
        style={oneLight}
        language={language}
        PreTag="div"
        customStyle={{ margin: 0, borderRadius: '0 0 4px 4px' }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
};

const Chat: React.FC = () => {
  const {
    conversations, currentConvId, messages, isStreaming, streamingContent, thinkingContent,
    loadConversations, selectConversation, createConversation,
    deleteConversation, renameConversation, togglePin, sendMessage, cancelStream,
  } = useChatStore();

  const [inputValue, setInputValue] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // 加载会话列表
  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent, thinkingContent]);

  const handleSend = async () => {
    const content = inputValue.trim();
    if (!content) return;
    setInputValue('');
    const msgLen = content.length;
    logger.info('chat_send_message', {
      conv_id: currentConvId || null,
      msg_len: msgLen,
    });
    const done = logger.perf('chat_send_message', { conv_id: currentConvId || null });
    try {
      await sendMessage(content);
      done({ result: 'success' });
    } catch (err: any) {
      done({ result: 'error', msg: err?.message });
      logger.error('chat_send_message_failed', {
        conv_id: currentConvId || null,
        msg: err?.message,
      });
      antMsg.error(err?.message || '发送失败');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleNewChat = async () => {
    logger.info('chat_new_conversation');
    try {
      await createConversation();
    } catch (err: any) {
      logger.error('chat_new_conversation_failed', { msg: err?.message });
      antMsg.error('创建会话失败');
    }
  };

  const handleStopStream = () => {
    logger.info('chat_cancel_stream', { conv_id: currentConvId || null });
    cancelStream();
  };

  const handleDelete = async (convId: string) => {
    logger.info('chat_delete_conversation', { conv_id: convId });
    try {
      await deleteConversation(convId);
    } catch (err: any) {
      logger.error('chat_delete_conversation_failed', { conv_id: convId, msg: err?.message });
      antMsg.error('删除会话失败');
    }
  };

  const handleRenameStart = (convId: string, currentTitle: string) => {
    setEditingId(convId);
    setEditTitle(currentTitle);
  };

  const handleRenameConfirm = async (convId: string) => {
    if (editTitle.trim()) {
      await renameConversation(convId, editTitle.trim());
    }
    setEditingId(null);
  };

  const handleTogglePin = async (convId: string, pinned: boolean) => {
    logger.info('chat_toggle_pin', { conv_id: convId, pinned: !pinned });
    await togglePin(convId, !pinned);
  };

  const currentMessages = currentConvId ? (messages[currentConvId] || []) : [];
  // 思考阶段（有 thinkingContent 但无 streamingContent）不显示 assistant 气泡
  // 正常流式输出阶段（有 streamingContent）才显示 assistant 气泡
  const showStreamingBubble = isStreaming && streamingContent.length > 0;
  const allContent = showStreamingBubble
    ? [...currentMessages, { role: 'assistant' as const, content: streamingContent, isStreaming: true }]
    : currentMessages;

  return (
    <Layout style={{ height: '100vh' }}>
      {/* 会话列表侧边栏 */}
      <Sider width={280} theme="light" style={{ borderRight: '1px solid #f0f0f0', overflow: 'auto' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0' }}>
          <Button type="primary" icon={<PlusOutlined />} block onClick={handleNewChat}>
            新对话
          </Button>
        </div>
        <List
          dataSource={conversations}
          renderItem={(conv) => (
            <List.Item
              onClick={() => selectConversation(conv.conv_id)}
              style={{
                cursor: 'pointer',
                padding: '10px 16px',
                background: currentConvId === conv.conv_id
                  ? '#e6f4ff'
                  : (conv.pinned ? '#fffbe6' : undefined),
                borderLeft: currentConvId === conv.conv_id ? '3px solid #1677ff' : '3px solid transparent',
              }}
              actions={[
                <span
                  key="pin"
                  onClick={(e) => { e.stopPropagation(); handleTogglePin(conv.conv_id, !!conv.pinned); }}
                  style={{ cursor: 'pointer' }}
                  title={conv.pinned ? '取消置顶' : '置顶'}
                >
                  {conv.pinned
                    ? <PushpinFilled style={{ color: '#faad14' }} />
                    : <PushpinOutlined style={{ color: '#999' }} />}
                </span>,
                <Popconfirm title="确定删除？" onConfirm={() => handleDelete(conv.conv_id)} key="delete">
                  <DeleteOutlined style={{ color: '#999' }} />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  editingId === conv.conv_id ? (
                    <Input
                      size="small"
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      onBlur={() => handleRenameConfirm(conv.conv_id)}
                      onPressEnter={() => handleRenameConfirm(conv.conv_id)}
                      autoFocus
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : (
                    <Space>
                      <Text
                        ellipsis={{ tooltip: conv.title }}
                        style={{ maxWidth: 140, cursor: 'pointer' }}
                        onDoubleClick={() => handleRenameStart(conv.conv_id, conv.title)}
                      >
                        {conv.title || '新对话'}
                      </Text>
                      <EditOutlined
                        style={{ color: '#999', fontSize: 12, cursor: 'pointer' }}
                        onClick={(e) => { e.stopPropagation(); handleRenameStart(conv.conv_id, conv.title); }}
                      />
                    </Space>
                  )
                }
                description={
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {conv.updated_at ? new Date(conv.updated_at).toLocaleString('zh-CN') : ''}
                  </Text>
                }
              />
            </List.Item>
          )}
          locale={{ emptyText: '暂无对话' }}
          style={{ flex: 1 }}
        />
      </Sider>

      {/* 聊天主区域 */}
      <Content style={{ display: 'flex', flexDirection: 'column', background: '#fff' }}>
        {/* 消息列表 */}
        <div style={{ flex: 1, overflow: 'auto', padding: '24px 40px' }}>
          {allContent.length === 0 && !isStreaming && (
            <div style={{ textAlign: 'center', marginTop: 120, color: '#999' }}>
              <Text style={{ fontSize: 16 }}>开始一个新对话</Text>
              <br />
              <Text type="secondary">在下方输入消息，开始与 SEKB 知识库对话</Text>
            </div>
          )}
          {allContent.map((msg, idx) => (
            <div
              key={idx}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
                marginBottom: 16,
              }}
            >
              <div
                style={{
                  maxWidth: '70%',
                  padding: '12px 16px',
                  borderRadius: 12,
                  background: msg.role === 'user' ? '#1677ff' : '#f5f5f5',
                  color: msg.role === 'user' ? '#fff' : '#000',
                }}
              >
                {msg.role === 'user' ? (
                  <div style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
                ) : (
                  <div className="markdown-content">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        code({ className, children, ...props }) {
                          const match = /language-(\w+)/.exec(className || '');
                          const codeStr = String(children).replace(/\n$/, '');
                          if (match) {
                            return (
                              <CodeBlock language={match[1]} code={codeStr} />
                            );
                          }
                          return <code className={className} {...props}>{children}</code>;
                        },
                      }}
                    >
                      {msg.content}
                    </ReactMarkdown>
                    {(msg as any).isStreaming && (
                      <span style={{ display: 'inline-block', animation: 'blink 1s steps(1) infinite' }}>|</span>
                    )}
                  </div>
                )}
              </div>
              {/* 消息时间戳 */}
              {(msg as any).created_at && (
                <Text
                  type="secondary"
                  style={{ fontSize: 11, marginTop: 4, padding: '0 4px' }}
                >
                  {new Date((msg as any).created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                </Text>
              )}
            </div>
          ))}
          {/* 思考中提示（无气泡、无光标，独立提示卡片） */}
          {isStreaming && thinkingContent && !streamingContent && (
            <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 16 }}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '10px 16px',
                  borderRadius: 12,
                  background: '#f0f5ff',
                  border: '1px solid #d6e4ff',
                  color: '#1677ff',
                  fontSize: 14,
                }}
              >
                <span className="thinking-dots">
                  <span />
                  <span />
                  <span />
                </span>
                <span>{thinkingContent}</span>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* 输入区域 */}
        <div style={{ padding: '16px 40px 24px', borderTop: '1px solid #f0f0f0' }}>
          <Space.Compact style={{ width: '100%' }}>
            <Input.TextArea
              ref={inputRef as any}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入消息，Enter 发送，Shift+Enter 换行"
              autoSize={{ minRows: 2, maxRows: 6 }}
              disabled={isStreaming}
            />
            {isStreaming ? (
              <Button
                danger
                icon={<StopOutlined />}
                onClick={handleStopStream}
                style={{ height: 'auto' }}
              >
                停止
              </Button>
            ) : (
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={handleSend}
                style={{ height: 'auto' }}
              >
                发送
              </Button>
            )}
          </Space.Compact>
        </div>
      </Content>

      <style>{`
        @keyframes blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
        .thinking-dots {
          display: inline-flex;
          gap: 4px;
        }
        .thinking-dots span {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: #1677ff;
          animation: thinking-bounce 1.4s infinite ease-in-out both;
        }
        .thinking-dots span:nth-child(1) { animation-delay: -0.32s; }
        .thinking-dots span:nth-child(2) { animation-delay: -0.16s; }
        @keyframes thinking-bounce {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
          40% { transform: scale(1); opacity: 1; }
        }
      `}</style>
    </Layout>
  );
};

export default Chat;