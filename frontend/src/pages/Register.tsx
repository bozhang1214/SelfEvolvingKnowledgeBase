import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Form, Input, Button, Card, Typography, message, Space } from 'antd';
import { MailOutlined, LockOutlined, UserOutlined } from '@ant-design/icons';
import { useUserStore } from '@/stores/user';

const { Title, Text } = Typography;

const Register: React.FC = () => {
  const navigate = useNavigate();
  const register = useUserStore((s) => s.register);
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { email: string; password: string; name: string }) => {
    setLoading(true);
    try {
      await register(values);
      message.success('注册成功');
      navigate('/');
    } catch (err: any) {
      message.error(err?.response?.data?.message || '注册失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', justifyContent: 'center', alignItems: 'center', background: '#f0f2f5' }}>
      <Card style={{ width: 400, boxShadow: '0 2px 8px rgba(0,0,0,0.1)' }}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <div style={{ textAlign: 'center' }}>
            <Title level={3}>创建账户</Title>
            <Text type="secondary">注册 SEKB 知识库账户</Text>
          </div>
          <Form onFinish={onFinish} layout="vertical" size="large">
            <Form.Item name="name" rules={[{ required: false }]}>
              <Input prefix={<UserOutlined />} placeholder="昵称（可选）" />
            </Form.Item>
            <Form.Item name="email" rules={[{ required: true, type: 'email', message: '请输入有效邮箱' }]}>
              <Input prefix={<MailOutlined />} placeholder="邮箱" />
            </Form.Item>
            <Form.Item name="password" rules={[
              { required: true, min: 8, message: '密码至少 8 位' },
            ]}>
              <Input.Password prefix={<LockOutlined />} placeholder="密码" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={loading} block>
                注册
              </Button>
            </Form.Item>
          </Form>
          <div style={{ textAlign: 'center' }}>
            <Text>已有账户？</Text>
            <Link to="/login">返回登录</Link>
          </div>
        </Space>
      </Card>
    </div>
  );
};

export default Register;