import React from 'react';
import { Card, Typography, Form, Input, Select, InputNumber, Button, Divider, message, Space, Avatar } from 'antd';
import { UserOutlined, ApiOutlined } from '@ant-design/icons';
import { useUserStore } from '@/stores/user';
import apiClient from '@/services/api';

const { Title, Text } = Typography;

const Settings: React.FC = () => {
  const { user } = useUserStore();
  const [form] = Form.useForm();

  React.useEffect(() => {
    if (user) {
      form.setFieldsValue({
        name: user.name,
        model: user.settings?.model || 'deepseek-chat',
        temperature: user.settings?.temperature || 0.7,
        max_tokens: user.settings?.max_tokens || 4096,
      });
    }
  }, [user, form]);

  const handleSaveProfile = async (values: { name: string }) => {
    try {
      await apiClient.patch('/auth/me', { name: values.name });
      message.success('个人信息已更新');
    } catch {
      message.error('更新失败');
    }
  };

  const handleSaveModel = async (values: { model: string; temperature: number; max_tokens: number }) => {
    try {
      await apiClient.patch('/auth/me', {
        settings: { ...user?.settings, ...values },
      });
      message.success('模型偏好已更新');
    } catch {
      message.error('更新失败');
    }
  };

  return (
    <div style={{ padding: 24, maxWidth: 600 }}>
      <Title level={4}>设置</Title>

      {/* 个人信息 */}
      <Card title={<Space><UserOutlined />个人信息</Space>} style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space>
            <Avatar size={48} icon={<UserOutlined />} />
            <div>
              <Text strong>{user?.name || user?.email}</Text>
              <br />
              <Text type="secondary">{user?.email}</Text>
            </div>
          </Space>
          <Divider />
          <Form form={form} layout="vertical" onFinish={handleSaveProfile}>
            <Form.Item name="name" label="昵称" rules={[{ max: 50 }]}>
              <Input placeholder="输入昵称" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit">保存</Button>
            </Form.Item>
          </Form>
        </Space>
      </Card>

      {/* 模型偏好 */}
      <Card title={<Space><ApiOutlined />模型偏好</Space>} style={{ marginBottom: 16 }}>
        <Form
          layout="vertical"
          initialValues={{
            model: 'deepseek-chat',
            temperature: 0.7,
            max_tokens: 4096,
          }}
          onFinish={handleSaveModel}
        >
          <Form.Item name="model" label="默认模型">
            <Select>
              <Select.Option value="deepseek-chat">DeepSeek Chat</Select.Option>
              <Select.Option value="deepseek-reasoner">DeepSeek Reasoner</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="temperature" label="温度 (Temperature)">
            <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="max_tokens" label="最大 Token 数">
            <InputNumber min={256} max={8192} step={256} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit">保存设置</Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
};

export default Settings;