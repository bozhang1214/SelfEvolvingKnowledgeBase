import React, { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Layout, Typography, Spin, Empty, Tag, Input, Button, Space, message, Card, Avatar,
} from 'antd';
import {
  ArrowLeftOutlined, SendOutlined, DatabaseOutlined, UserOutlined, MessageOutlined,
} from '@ant-design/icons';
import apiClient, { unwrap, API_BASE } from '@/services/api';
import { useUserStore } from '@/stores/user';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const { Text, Paragraph } = Typography;
const { Sider, Content, Header } = Layout;

interface ShareInfo {
  share_id: string;
  title: string;
  owner_name: string;
  entries_count: number;
  is_owner: boolean;
  has_expired: boolean;
  category_label?: string;
}

interface SharedEntry {
  entry_id: string;
  content: string;
  source: string;
  source_id: string;
  category_l1: string;
  category_l2: string;
  category_l3: string;
  created_at: string;
}

interface SharedMessage {
  role: string;
  content: string;
}

const SharedKnowledge: React.FC = () => {
  const { shareId } = useParams<{ shareId: string }>();
  const navigate = useNavigate();
  const { isLoggedIn } = useUserStore();

  const [info, setInfo] = useState<ShareInfo | null>(null);
  const [entries, setEntries] = useState<SharedEntry[]>([]);
  const [loadingInfo, setLoadingInfo] = useState(true);
  const [loadingEntries, setLoadingEntries] = useState(false);

  const [messages, setMessages] = useState<SharedMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamContent, setStreamContent] = useState('');
  const [thinking, setThinking] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 加载分享信息
  useEffect(() => {
    if (!shareId) return;
    // 未登录时不自动跳转：渲染下面的「登录引导页」，由用户主动点「去登录」
    if (!isLoggedIn) return;
    setLoadingInfo(true);
    apiClient
      .get(`/share/${shareId}`)
      .then((res) => {
        const data = unwrap<ShareInfo>(res);
        setInfo(data);
      })
      .catch((e) => {
        message.error(e?.response?.data?.detail || '分享不存在或已失效');
        setInfo(null);
      })
      .finally(() => setLoadingInfo(false));
  }, [shareId, isLoggedIn, navigate]);

  // 加载条目
  const loadEntries = async () => {
    if (!shareId) return;
    setLoadingEntries(true);
    try {
      const res = await apiClient.get(`/share/${shareId}/entries`, {
        params: { page: 1, page_size: 50 },
      });
      const data = unwrap<{ entries: SharedEntry[]; total: number }>(res);
      setEntries(data.entries || []);
    } catch {
      setEntries([]);
    } finally {
      setLoadingEntries(false);
    }
  };

  // 加载会话历史
  const loadMessages = async () => {
    if (!shareId) return;
    try {
      const res = await apiClient.get(`/share/${shareId}/messages`);
      const data = unwrap<{ messages: SharedMessage[] }>(res);
      setMessages(data.messages || []);
    } catch {
      setMessages([]);
    }
  };

  useEffect(() => {
    if (info) {
      loadEntries();
      loadMessages();
    }
  }, [info]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamContent]);

  const handleSend = () => {
    const content = input.trim();
    if (!content || !shareId || streaming) return;
    setInput('');
    // 立即展示用户消息，避免流式期间用户提问不可见
    setMessages((prev) => [...prev, { role: 'user', content }]);
    setStreaming(true);
    setStreamContent('');
    setThinking('正在检索知识库...');

    const controller = new AbortController();
    abortRef.current = controller;
    const token = localStorage.getItem('sekb_token');

    fetch(`${API_BASE}/share/${shareId}/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ message: content }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const err = await response.json().catch(() => ({ message: '请求失败' }));
          message.error(err.detail || err.message || `HTTP ${response.status}`);
          setStreaming(false);
          setThinking('');
          return;
        }
        const reader = response.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let acc = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const events = buffer.split('\n\n');
          buffer = events.pop() || '';
          for (const block of events) {
            const lines = block.split('\n').filter((l) => l.startsWith('data: '));
            if (!lines.length) continue;
            const dataStr = lines.map((l) => l.slice(6)).join('');
            try {
              const data = JSON.parse(dataStr);
              if (data.type === 'thinking') {
                setThinking(data.content);
              } else if (data.type === 'token') {
                setThinking('');
                acc += data.content;
                setStreamContent(acc);
              } else if (data.type === 'done') {
                setThinking('');
                setMessages((prev) => [...prev, { role: 'assistant', content: acc }]);
                setStreamContent('');
              } else if (data.type === 'error') {
                message.error(data.detail || '对话失败');
              }
            } catch {
              // 忽略解析错误
            }
          }
        }
        setStreaming(false);
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          message.error(err.message || '网络错误');
        }
        setStreaming(false);
        setThinking('');
      });
  };

  const handleStop = () => {
    abortRef.current?.abort();
    setStreaming(false);
    setThinking('');
    if (streamContent) {
      // 用户消息已在发送时加入，这里只需追加已生成的部分回复
      setMessages((prev) => [...prev, { role: 'assistant', content: streamContent }]);
      setStreamContent('');
    }
  };

  if (loadingInfo) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!info) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', height: '100vh', gap: 16 }}>
        <Empty description="分享不存在或已失效" />
        <Button type="primary" onClick={() => navigate('/')}>返回首页</Button>
      </div>
    );
  }

  const allMessages = streaming && streamContent
    ? [...messages, { role: 'assistant', content: streamContent }]
    : messages;

  // 未登录：不静默跳转，而是给出**明确的登录引导页**（告知「为什么需要登录」+ 一键去登录，
  // 登录后自动回到本分享）。分享按策略**必须登录**才能查看，不做公开只读。
  if (!isLoggedIn) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#f5f5f5' }}>
        <Card style={{ maxWidth: 420, textAlign: 'center' }}>
          <Text strong style={{ fontSize: 16 }}>需要登录后才能查看该分享</Text>
          <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 20 }}>
            分享内容仅对已登录账号开放。登录或注册后会自动回到这个分享页面。
          </Paragraph>
          <Space>
            <Button type="primary" onClick={() => navigate(`/login?redirect=/share/${shareId}`)}>
              去登录
            </Button>
            <Button onClick={() => navigate('/register')}>注册账号</Button>
          </Space>
        </Card>
      </div>
    );
  }

  return (
    <Layout style={{ height: '100vh' }}>
      {/* 左侧：知识条目浏览（只读） */}
      <Sider width={360} theme="light" style={{ borderRight: '1px solid #f0f0f0', overflow: 'auto', background: '#fafafa' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0', position: 'sticky', top: 0, background: '#fafafa', zIndex: 1 }}>
          <Space>
            <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/knowledge')} />
            <Text strong>知识条目</Text>
            {info.category_label && info.category_label !== '全部' && (
              <Tag color="geekblue">{info.category_label}</Tag>
            )}
            <Tag color="blue">{info.entries_count}</Tag>
          </Space>
        </div>
        <Spin spinning={loadingEntries}>
          {entries.length === 0 ? (
            <div style={{ padding: 24 }}>
              <Empty description="无知识条目" />
            </div>
          ) : (
            entries.map((e) => (
              <Card
                key={e.entry_id}
                size="small"
                style={{ margin: '8px 12px' }}
                bodyStyle={{ padding: 12 }}
              >
                <Paragraph
                  ellipsis={{ rows: 3 }}
                  style={{ marginBottom: 8, fontSize: 13 }}
                >
                  {e.content}
                </Paragraph>
                <Space size={4} wrap>
                  <Tag color="blue" style={{ fontSize: 11 }}>{e.category_l1}</Tag>
                  <Tag color="geekblue" style={{ fontSize: 11 }}>{e.category_l2}</Tag>
                  <Tag color="purple" style={{ fontSize: 11 }}>{e.category_l3}</Tag>
                </Space>
              </Card>
            ))
          )}
        </Spin>
      </Sider>

      {/* 右侧：对话区 */}
      <Layout>
        <Header style={{ background: '#fff', borderBottom: '1px solid #f0f0f0', padding: '0 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Space>
            <Avatar size="small" icon={<UserOutlined />} />
            <div>
              <Text strong>{info.title}</Text>
              <br />
              <Text type="secondary" style={{ fontSize: 12 }}>分享自 {info.owner_name} · 只读 + 可对话</Text>
            </div>
          </Space>
          <Space>
            <Tag icon={<DatabaseOutlined />}>{info.entries_count} 条知识</Tag>
            {info.is_owner && <Tag color="green">我的知识库</Tag>}
          </Space>
        </Header>

        <Content style={{ display: 'flex', flexDirection: 'column', background: '#fff' }}>
          {/* 消息列表 */}
          <div style={{ flex: 1, overflow: 'auto', padding: '24px 40px' }}>
            {allMessages.length === 0 && !streaming && (
              <div style={{ textAlign: 'center', marginTop: 120, color: '#999' }}>
                <MessageOutlined style={{ fontSize: 32, marginBottom: 8 }} />
                <br />
                <Text style={{ fontSize: 16 }}>基于该知识库提问</Text>
                <br />
                <Text type="secondary">问答仅读取分享者知识库，不会修改其内容</Text>
              </div>
            )}
            {allMessages.map((msg, idx) => (
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
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                    </div>
                  )}
                </div>
              </div>
            ))}
            {streaming && thinking && !streamContent && (
              <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 16 }}>
                <div style={{ padding: '10px 16px', borderRadius: 12, background: '#f0f5ff', border: '1px solid #d6e4ff', color: '#1677ff', fontSize: 14 }}>
                  <span className="thinking-dots"><span /><span /><span /></span>
                  <span style={{ marginLeft: 8 }}>{thinking}</span>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* 输入区 */}
          <div style={{ padding: '16px 40px 24px', borderTop: '1px solid #f0f0f0' }}>
            <Space.Compact style={{ width: '100%' }}>
              <Input.TextArea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder="基于该知识库提问，Enter 发送"
                autoSize={{ minRows: 2, maxRows: 6 }}
                disabled={streaming}
              />
              {streaming ? (
                <Button danger icon={<SendOutlined />} onClick={handleStop} style={{ height: 'auto' }}>停止</Button>
              ) : (
                <Button type="primary" icon={<SendOutlined />} onClick={handleSend} style={{ height: 'auto' }}>发送</Button>
              )}
            </Space.Compact>
          </div>
        </Content>
      </Layout>

      <style>{`
        .thinking-dots { display: inline-flex; gap: 4px; }
        .thinking-dots span { width: 6px; height: 6px; border-radius: 50%; background: #1677ff; animation: tb 1.4s infinite ease-in-out both; }
        .thinking-dots span:nth-child(1) { animation-delay: -0.32s; }
        .thinking-dots span:nth-child(2) { animation-delay: -0.16s; }
        @keyframes tb { 0%,80%,100% { transform: scale(0.6); opacity: 0.4; } 40% { transform: scale(1); opacity: 1; } }
      `}</style>
    </Layout>
  );
};

export default SharedKnowledge;
