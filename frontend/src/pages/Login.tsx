import React, { useState } from 'react';
import { useNavigate, Link, useSearchParams } from 'react-router-dom';
import { Form, Input, Button, Card, Typography, message, Space, Alert } from 'antd';
import { MailOutlined, LockOutlined } from '@ant-design/icons';
import { useUserStore } from '@/stores/user';
import { logger, maskEmail } from '@/utils/logger';

const { Title, Text } = Typography;

const Login: React.FC = () => {
  const navigate = useNavigate();
  const login = useUserStore((s) => s.login);
  const [searchParams] = useSearchParams();
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string>('');

  const onFinish = async (values: { email: string; password: string }) => {
    setLoading(true);
    setErrorMsg('');
    logger.info('login_submit', { email: maskEmail(values.email) });
    try {
      await login(values);
      message.success('登录成功');
      // 登录后回跳原始目标（如飞书告警深链的 /chat?conversation_id=xxx）；非法值回首页
      const redirect = searchParams.get('redirect') || '';
      navigate(redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : '/');
    } catch (err: any) {
      const status = err?.response?.status;
      const msg = err?.response?.data?.message || '登录失败，请检查邮箱和密码';
      setErrorMsg(msg);
      message.error(msg);
      // 失败埋点：记录 HTTP 状态、后端返回的消息、是否网络错误
      logger.warn('login_submit_failed', {
        email: maskEmail(values.email),
        http_status: status ?? 0,
        server_msg: msg,
        network_error: !err.response,
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', justifyContent: 'center', alignItems: 'center', background: '#f0f2f5' }}>
      <Card style={{ width: 400, boxShadow: '0 2px 8px rgba(0,0,0,0.1)' }}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <div style={{ textAlign: 'center' }}>
            <Title level={3}>SEKB 知识库</Title>
            <Text type="secondary">登录您的账户</Text>
          </div>
          {errorMsg && (
            <Alert
              type="error"
              showIcon
              message={errorMsg}
              description={
                <span>
                  还没有账户？<Link to="/register">立即注册</Link>（注册后可直接登录）
                </span>
              }
              closable
              onClose={() => setErrorMsg('')}
            />
          )}
          <Form onFinish={onFinish} layout="vertical" size="large">
            <Form.Item name="email" rules={[{ required: true, type: 'email', message: '请输入有效邮箱' }]}>
              <Input prefix={<MailOutlined />} placeholder="邮箱" />
            </Form.Item>
            <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password prefix={<LockOutlined />} placeholder="密码" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={loading} block>
                登录
              </Button>
            </Form.Item>
          </Form>
          <div style={{ textAlign: 'center' }}>
            <Text>还没有账户？</Text>
            <Link to="/register">立即注册</Link>
          </div>
        </Space>
      </Card>
    </div>
  );
};

export default Login;