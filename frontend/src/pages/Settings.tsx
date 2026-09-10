import React from 'react';
import { Card, Typography, Form, Input, Select, Button, Divider, message, Space, Avatar, Tag, Modal, Spin } from 'antd';
import { UserOutlined, ApiOutlined, SafetyOutlined, QrcodeOutlined } from '@ant-design/icons';
import { useUserStore } from '@/stores/user';
import apiClient from '@/services/api';
import { getTokenExpiry } from '@/services/auth';
import { bossQrStart, bossQrStatus } from '@/services/job';

const { Title, Text, Paragraph } = Typography;

const Settings: React.FC = () => {
  const { user } = useUserStore();
  const [form] = Form.useForm();
  const [expiry, setExpiry] = React.useState<Date | null>(null);

  React.useEffect(() => {
    setExpiry(getTokenExpiry());
  }, []);

  React.useEffect(() => {
    if (user) {
      form.setFieldsValue({
        name: user.name,
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

  const handleSaveModel = async (values: { send_key?: string }) => {
    try {
      await apiClient.patch('/auth/me', {
        settings: { ...user?.settings, ...values },
      });
      message.success('偏好已更新');
      // 刷新本地用户态（settings 已变，让聊天输入框即时生效）
      useUserStore.setState((s) => ({
        user: s.user ? { ...s.user, settings: { ...s.user.settings, ...values } } : s.user,
      }));
    } catch {
      message.error('更新失败');
    }
  };

  // BOSS 扫码登录（获取登录 Cookie 备用）
  const [qrOpen, setQrOpen] = React.useState(false);
  const [qrImageUrl, setQrImageUrl] = React.useState('');
  const [qrWaiting, setQrWaiting] = React.useState(false);
  const [qrTip, setQrTip] = React.useState('');

  const handleBossQrLogin = async () => {
    setQrOpen(true);
    setQrWaiting(true);
    setQrImageUrl('');
    setQrTip('');
    let timer: ReturnType<typeof setInterval> | undefined;
    let finished = false;
    const finish = () => {
      finished = true;
      if (timer) clearInterval(timer);
      setQrWaiting(false);
    };
    try {
      const { qr_id, qr_image_url } = await bossQrStart();
      setQrImageUrl(qr_image_url);
      setQrTip('用手机 BOSS App「扫一扫」此二维码');
      timer = setInterval(async () => {
        try {
          const st = await bossQrStatus(qr_id);
          if (st.qr_image_url) setQrImageUrl(st.qr_image_url);
          if (st.phase === 'waiting_scan') {
            setQrTip('用手机 BOSS App「扫一扫」此二维码');
          } else if (st.phase === 'waiting_second_scan') {
            setQrTip('已扫描，请再次扫一扫这张新二维码');
          } else if (st.phase === 'waiting_confirm') {
            setQrTip('请在 BOSS App 上点击「确认登录」');
          } else if (st.phase === 'success') {
            finish();
            setQrOpen(false);
            message.success('BOSS 登录成功，Cookie 已保存');
          } else if (st.phase === 'expired') {
            finish();
            message.warning('二维码已过期，请重新点击「扫码登录」');
          } else if (st.phase === 'login_failed') {
            finish();
            message.error(st.message || 'BOSS 登录失败，请重试');
          }
        } catch {
          // 单个轮询失败不中断，继续等
        }
      }, 1500);
      setTimeout(() => {
        if (!finished) {
          finish();
          message.warning('等待扫码超时，请重试');
        }
      }, 180000);
    } catch (e: any) {
      setQrWaiting(false);
      message.error(e?.response?.data?.detail || 'BOSS 扫码登录失败');
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
              <br />
              <Text type="secondary" style={{ fontSize: 12 }}>
                用户 ID: {user?.user_id || '-'}
              </Text>
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

      {/* 登录态（90 天自动续租） */}
      <Card title={<Space><SafetyOutlined />登录态</Space>} style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={4}>
          <Text>
            登录有效期 <Tag color="green">90 天</Tag>，到期自动续租（接近免登录）。
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            只要 90 天内使用过系统，token 会自动续成新的 90 天，无需重新登录。
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            当前 token 到期时间：
            {expiry ? expiry.toLocaleString('zh-CN') : '（无法解析）'}
          </Text>
        </Space>
      </Card>

      {/* 偏好（模型设置已隐藏，统一由服务端配置指定模型） */}
      <Card title={<Space><ApiOutlined />偏好</Space>} style={{ marginBottom: 16 }}>
        <Form
          layout="vertical"
          initialValues={{
            send_key: user?.settings?.send_key || 'enter',
          }}
          onFinish={handleSaveModel}
        >
          <Form.Item name="send_key" label="发送快捷键">
            <Select>
              <Select.Option value="enter">Enter 发送（Shift+Enter 换行）</Select.Option>
              <Select.Option value="cmd_enter">Cmd/Ctrl + Enter 发送（Enter 换行）</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit">保存设置</Button>
          </Form.Item>
        </Form>
      </Card>

      {/* BOSS 直聘登录（第三方登录，备用） */}
      <Card title={<Space><QrcodeOutlined />BOSS 直聘登录</Space>} style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={4}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            登录 BOSS 直聘以获取 Cookie，供后续采集 BOSS 职位使用（当前 BOSS 采集受反爬限制，登录作为备用）。
          </Text>
          <Button icon={<QrcodeOutlined />} onClick={handleBossQrLogin}>
            扫码登录
          </Button>
        </Space>
      </Card>

      {/* BOSS 扫码登录弹窗 */}
      <Modal
        title="BOSS 直聘扫码登录"
        open={qrOpen}
        onCancel={() => setQrOpen(false)}
        footer={null}
        width={360}
      >
        <div style={{ textAlign: 'center', padding: '12px 0' }}>
          {qrImageUrl ? (
            <>
              <img src={qrImageUrl} alt="BOSS 登录二维码" style={{ width: 220, height: 220 }} />
              <Paragraph type="secondary" style={{ marginTop: 12 }}>
                {qrTip || '用手机 BOSS App「扫一扫」此二维码'}
              </Paragraph>
            </>
          ) : (
            <Spin tip="正在生成二维码…" />
          )}
          {qrWaiting && qrImageUrl && (
            <Paragraph type="secondary" style={{ marginTop: 8 }}>
              <Spin size="small" /> 等待扫码确认中…（约 3 分钟超时）
            </Paragraph>
          )}
        </div>
      </Modal>
    </div>
  );
};

export default Settings;