import React, { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Layout, List, Input, Button, Typography, Space, Spin, Popconfirm, Checkbox, Popover, message as antMsg,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, EditOutlined, SendOutlined, StopOutlined,
  PushpinOutlined, PushpinFilled, CopyOutlined, CheckOutlined,
  ShareAltOutlined, DownloadOutlined, CheckSquareOutlined, HistoryOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@/stores/chat';
import { logger } from '@/utils/logger';
import { copyText, downloadTextFile } from '@/utils/clipboard';
import { createChatShare } from '@/services/share';
import type { Message } from '@/types/chat';
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

/** 将多条消息拼接为 Markdown 文本（用于导出下载）。 */
function buildConversationMarkdown(title: string, msgs: Message[]): string {
  const lines: string[] = [];
  lines.push(`# ${title || '对话'}`);
  lines.push('');
  lines.push(`> 导出自 SEKB 知识库对话 · ${new Date().toLocaleString('zh-CN')}`);
  lines.push('');
  msgs.forEach((m) => {
    const role = m.role === 'user' ? '🧑 用户' : '🤖 助手';
    const time = m.created_at ? new Date(m.created_at).toLocaleString('zh-CN') : '';
    lines.push(`## ${role}${time ? ` · ${time}` : ''}`);
    lines.push('');
    lines.push(m.content);
    lines.push('');
  });
  return lines.join('\n');
}

/** 拼接多条选中消息为纯文本（按 角色: 内容 格式）。 */
function joinSelectedMessages(msgs: Message[]): string {
  return msgs
    .map((m) => `${m.role === 'user' ? '用户' : '助手'}: ${m.content}`)
    .join('\n\n');
}

const Chat: React.FC = () => {
  const {
    conversations, currentConvId, messages, isStreaming, streamingContent, thinkingContent,
    pendingQueue,
    loadConversations, selectConversation, createConversation,
    deleteConversation, renameConversation, togglePin, sendMessage, cancelStream, clearQueue,
  } = useChatStore();

  const [inputValue, setInputValue] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  // 多选模式与已选消息索引集合（针对 currentMessages 下标）
  const [multiSelect, setMultiSelect] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  // 单条消息「已复制」状态（记录消息下标，短暂显示）
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const [sharing, setSharing] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [searchParams] = useSearchParams();

  // 加载会话列表
  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // 深链：从 URL 的 conversation_id 参数定位到指定会话
  // （飞书告警「查看对话记录」按钮跳转 /sekb/chat?conversation_id=xxx）
  useEffect(() => {
    const convId = searchParams.get('conversation_id');
    if (convId) {
      selectConversation(convId);
    }
  }, [searchParams, selectConversation]);

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

  // ============ 复制 / 多选 / 导出 / 分享 ============

  const handleCopyMessage = async (msg: { role: string; content: string }, idx: number) => {
    const ok = await copyText(msg.content);
    if (ok) {
      setCopiedIdx(idx);
      setTimeout(() => setCopiedIdx((cur) => (cur === idx ? null : cur)), 2000);
    } else {
      antMsg.error('复制失败');
    }
  };

  const enterMultiSelect = () => {
    setMultiSelect(true);
    setSelected(new Set());
  };

  const exitMultiSelect = () => {
    setMultiSelect(false);
    setSelected(new Set());
  };

  const toggleSelect = (idx: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const handleCopySelected = async () => {
    const msgs = currentMessages.filter((_, i) => selected.has(i));
    if (msgs.length === 0) return;
    const ok = await copyText(joinSelectedMessages(msgs));
    if (ok) {
      antMsg.success(`已复制 ${msgs.length} 条消息`);
      exitMultiSelect();
    } else {
      antMsg.error('复制失败');
    }
  };

  const handleExport = () => {
    if (currentMessages.length === 0) {
      antMsg.warning('当前会话没有可导出的消息');
      return;
    }
    const conv = conversations.find((c) => c.conv_id === currentConvId);
    const title = (conv?.title || '对话').trim();
    const md = buildConversationMarkdown(title, currentMessages);
    downloadTextFile(`${title}.md`, md);
    logger.info('chat_export_conversation', {
      conv_id: currentConvId || null,
      count: currentMessages.length,
    });
  };

  const handleShare = async () => {
    if (!currentConvId) {
      antMsg.warning('请先选择一个会话');
      return;
    }
    if (sharing) return;
    setSharing(true);
    logger.info('chat_share_conversation', { conv_id: currentConvId });
    try {
      const res = await createChatShare(currentConvId);
      const url = window.location.origin + res.share_url;
      const ok = await copyText(url);
      antMsg.success(ok ? '分享链接已复制到剪贴板' : '分享链接已生成，请手动复制');
    } catch (err: any) {
      logger.error('chat_share_failed', { conv_id: currentConvId, msg: err?.message });
      antMsg.error(err?.response?.data?.detail || err?.message || '分享失败');
    } finally {
      setSharing(false);
    }
  };

  const currentConvTitle = conversations.find((c) => c.conv_id === currentConvId)?.title || '';

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
        {/* 顶部工具栏：多选 / 导出 / 分享 */}
        {currentConvId && (
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '10px 24px',
              borderBottom: '1px solid #f0f0f0',
              background: '#fafafa',
            }}
          >
            <Text strong ellipsis={{ tooltip: currentConvTitle }} style={{ maxWidth: 320 }}>
              {currentConvTitle || '新对话'}
            </Text>
            <Space size={4}>
              {multiSelect ? (
                <>
                  <Text type="secondary" style={{ fontSize: 13, marginRight: 4 }}>
                    已选 {selected.size} 条
                  </Text>
                  <Button
                    size="small"
                    type="primary"
                    icon={<CopyOutlined />}
                    disabled={selected.size === 0}
                    onClick={handleCopySelected}
                  >
                    复制所选
                  </Button>
                  <Button size="small" onClick={exitMultiSelect}>取消</Button>
                </>
              ) : (
                <>
                  <Popover
                    trigger="hover"
                    placement="bottomRight"
                    overlayStyle={{ width: 300 }}
                    title="历史会话（点击切换）"
                    content={
                      <div style={{ maxHeight: 400, overflow: 'auto' }}>
                        {conversations.length === 0 ? (
                          <Text type="secondary" style={{ fontSize: 12 }}>暂无对话</Text>
                        ) : (
                          <List
                            size="small"
                            dataSource={conversations}
                            renderItem={(conv) => (
                              <List.Item
                                onClick={() => selectConversation(conv.conv_id)}
                                style={{
                                  cursor: 'pointer',
                                  background: currentConvId === conv.conv_id ? '#e6f4ff' : undefined,
                                  paddingLeft: 8,
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
                                  <Popconfirm title="确定删除？" onConfirm={() => handleDelete(conv.conv_id)} key="del">
                                    <DeleteOutlined style={{ color: '#999' }} />
                                  </Popconfirm>,
                                ]}
                              >
                                <Text
                                  ellipsis={{ tooltip: conv.title }}
                                  style={{ maxWidth: 170, cursor: 'pointer', fontSize: 13 }}
                                >
                                  {conv.pinned ? '📌 ' : ''}{conv.title || '新对话'}
                                </Text>
                              </List.Item>
                            )}
                          />
                        )}
                      </div>
                    }
                  >
                    <Button size="small" icon={<HistoryOutlined />}>历史会话</Button>
                  </Popover>
                  <Button size="small" icon={<CheckSquareOutlined />} onClick={enterMultiSelect}>多选</Button>
                  <Button size="small" icon={<DownloadOutlined />} onClick={handleExport}>导出</Button>
                  <Button size="small" icon={<ShareAltOutlined />} loading={sharing} onClick={handleShare}>分享</Button>
                </>
              )}
            </Space>
          </div>
        )}
        {/* 消息列表 */}
        <div style={{ flex: 1, overflow: 'auto', padding: '24px 40px' }}>
          {allContent.length === 0 && !isStreaming && (
            <div style={{ textAlign: 'center', marginTop: 120, color: '#999' }}>
              <Text style={{ fontSize: 16 }}>开始一个新对话</Text>
              <br />
              <Text type="secondary">在下方输入消息，开始与 SEKB 知识库对话</Text>
            </div>
          )}
          {allContent.map((msg, idx) => {
            // 流式占位气泡不属于已持久化消息，不提供复制/多选
            const isPersisted = idx < currentMessages.length;
            const isCopied = copiedIdx === idx;
            return (
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
                  className="msg-row"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
                  }}
                >
                  {multiSelect && isPersisted && (
                    <Checkbox
                      checked={selected.has(idx)}
                      onChange={() => toggleSelect(idx)}
                      onClick={(e) => e.stopPropagation()}
                    />
                  )}
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
                  {isPersisted && (
                    <Button
                      size="small"
                      type="text"
                      className="msg-copy-btn"
                      icon={isCopied ? <CheckOutlined style={{ color: '#52c41a' }} /> : <CopyOutlined />}
                      onClick={() => handleCopyMessage(msg, idx)}
                      title="复制"
                    />
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
            );
          })}
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
              placeholder={isStreaming ? '回复进行中，输入将进入队列（结束后自动发送）' : '输入消息，Enter 发送，Shift+Enter 换行'}
              autoSize={{ minRows: 2, maxRows: 6 }}
            />
            {isStreaming && (
              <Button
                danger
                icon={<StopOutlined />}
                onClick={() => {
                  clearQueue();
                  handleStopStream();
                }}
                style={{ height: 'auto' }}
              >
                停止
              </Button>
            )}
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSend}
              disabled={!inputValue.trim()}
              style={{ height: 'auto' }}
            >
              {isStreaming ? '排队' : '发送'}
            </Button>
          </Space.Compact>
          {isStreaming && (
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 6 }}>
              {pendingQueue.length > 0
                ? `回复进行中 · 已排队 ${pendingQueue.length} 条，回复结束后自动发送`
                : '回复进行中，输入的消息会自动排队发送（点「停止」取消）'}
            </Text>
          )}
        </div>
      </Content>

      <style>{`
        @keyframes blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
        .msg-copy-btn {
          opacity: 0;
          transition: opacity 0.15s;
        }
        .msg-row:hover .msg-copy-btn {
          opacity: 1;
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