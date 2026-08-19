import React, { useEffect, useRef, useState } from 'react';
import {
  Layout, List, Input, Button, Typography, Space, Spin, Popconfirm, message as antMsg,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, EditOutlined, SendOutlined, StopOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@/stores/chat';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';

const { Text } = Typography;
const { Sider, Content } = Layout;

const Chat: React.FC = () => {
  const {
    conversations, currentConvId, messages, isStreaming, streamingContent,
    loadConversations, selectConversation, createConversation,
    deleteConversation, renameConversation, sendMessage, cancelStream,
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
  }, [messages, streamingContent]);

  const handleSend = async () => {
    const content = inputValue.trim();
    if (!content) return;
    setInputValue('');
    await sendMessage(content);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleNewChat = async () => {
    await createConversation();
  };

  const handleDelete = async (convId: string) => {
    await deleteConversation(convId);
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

  const currentMessages = currentConvId ? (messages[currentConvId] || []) : [];
  const allContent = isStreaming
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
                background: currentConvId === conv.conv_id ? '#e6f4ff' : undefined,
                borderLeft: currentConvId === conv.conv_id ? '3px solid #1677ff' : '3px solid transparent',
              }}
              actions={[
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
                        style={{ maxWidth: 160, cursor: 'pointer' }}
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
                justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
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
                              <SyntaxHighlighter style={oneLight} language={match[1]} PreTag="div">
                                {codeStr}
                              </SyntaxHighlighter>
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
            </div>
          ))}
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
                onClick={cancelStream}
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
      `}</style>
    </Layout>
  );
};

export default Chat;