import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Layout, Typography, Spin, Empty, Button, Space, Avatar, Popconfirm, message, Card,
} from 'antd';
import {
  ArrowLeftOutlined, UserOutlined, MessageOutlined, DeleteOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getChatShare, revokeChatShare, type SharedChatInfo } from '@/services/share';
import { useUserStore } from '@/stores/user';

const { Text, Paragraph } = Typography;
const { Header, Content } = Layout;

/**
 * 聊天会话分享只读页。
 *
 * 通过分享链接 `/share/chat/{shareId}` 打开，展示会话标题与消息历史。
 * 纯只读：不提供输入框，不支持继续对话。所有者可撤销分享。
 */
const SharedChat: React.FC = () => {
  const { shareId } = useParams<{ shareId: string }>();
  const navigate = useNavigate();
  const { isLoggedIn } = useUserStore();

  const [info, setInfo] = useState<SharedChatInfo | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!shareId) return;
    // 未登录时不自动跳转：渲染下面的「登录引导页」，由用户主动点「去登录」
    if (!isLoggedIn) return;
    setLoading(true);
    getChatShare(shareId)
      .then((data) => setInfo(data))
      .catch(() => setInfo(null))
      .finally(() => setLoading(false));
  }, [shareId, isLoggedIn, navigate]);

  const handleRevoke = async () => {
    if (!shareId) return;
    try {
      await revokeChatShare(shareId);
      message.success('分享已撤销');
      navigate('/');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '撤销失败');
    }
  };

  if (loading) {
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

  // 未登录：给出明确的登录引导页（分享按策略必须登录后查看，不做公开只读）
  if (!isLoggedIn) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#f5f5f5' }}>
        <Card style={{ maxWidth: 420, textAlign: 'center' }}>
          <Text strong style={{ fontSize: 16 }}>需要登录后才能查看该分享</Text>
          <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 20 }}>
            分享内容仅对已登录账号开放。登录或注册后会自动回到这个分享页面。
          </Paragraph>
          <Space>
            <Button type="primary" onClick={() => navigate(`/login?redirect=/share/chat/${shareId}`)}>
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
      <Header
        style={{
          background: '#fff',
          borderBottom: '1px solid #f0f0f0',
          padding: '0 24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <Space>
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/')} />
          <Avatar size="small" icon={<UserOutlined />} />
          <div>
            <Text strong>{info.title}</Text>
            <br />
            <Text type="secondary" style={{ fontSize: 12 }}>分享自 {info.owner_name} · 只读对话</Text>
          </div>
        </Space>
        {info.is_owner && (
          <Popconfirm title="撤销后该链接将失效，确定？" onConfirm={handleRevoke}>
            <Button size="small" danger icon={<DeleteOutlined />}>撤销分享</Button>
          </Popconfirm>
        )}
      </Header>

      <Content style={{ display: 'flex', flexDirection: 'column', background: '#fff' }}>
        <div style={{ flex: 1, overflow: 'auto', padding: '24px 40px' }}>
          {info.messages.length === 0 ? (
            <div style={{ textAlign: 'center', marginTop: 120, color: '#999' }}>
              <MessageOutlined style={{ fontSize: 32, marginBottom: 8 }} />
              <br />
              <Text type="secondary">该会话暂无消息</Text>
            </div>
          ) : (
            info.messages.map((m, idx) => (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: m.role === 'user' ? 'flex-end' : 'flex-start',
                  marginBottom: 16,
                }}
              >
                <div
                  style={{
                    maxWidth: '70%',
                    padding: '12px 16px',
                    borderRadius: 12,
                    background: m.role === 'user' ? '#1677ff' : '#f5f5f5',
                    color: m.role === 'user' ? '#fff' : '#000',
                  }}
                >
                  {m.role === 'user' ? (
                    <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
                  ) : (
                    <div className="markdown-content">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                    </div>
                  )}
                </div>
                {m.created_at && (
                  <Text type="secondary" style={{ fontSize: 11, marginTop: 4, padding: '0 4px' }}>
                    {new Date(m.created_at).toLocaleString('zh-CN')}
                  </Text>
                )}
              </div>
            ))
          )}
        </div>
      </Content>
    </Layout>
  );
};

export default SharedChat;
