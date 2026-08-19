import React from 'react';
import { Layout, Menu, Avatar, Dropdown, Typography, Space } from 'antd';
import {
  MessageOutlined,
  FileOutlined,
  DatabaseOutlined,
  SettingOutlined,
  LogoutOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation, Routes, Route } from 'react-router-dom';
import { useUserStore } from '@/stores/user';
import Chat from '@/pages/Chat';
import Files from '@/pages/Files';
import Knowledge from '@/pages/Knowledge';
import Settings from '@/pages/Settings';

const { Sider, Content } = Layout;
const { Text } = Typography;

const menuItems = [
  { key: '/', icon: <MessageOutlined />, label: '聊天' },
  { key: '/files', icon: <FileOutlined />, label: '文件管理' },
  { key: '/knowledge', icon: <DatabaseOutlined />, label: '知识库' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置' },
];

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useUserStore();

  const handleMenuClick = (info: { key: string }) => {
    navigate(info.key);
  };

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const userDropdownItems = {
    items: [
      { key: 'profile', icon: <UserOutlined />, label: '个人信息' },
      { type: 'divider' as const },
      { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
    ],
    onClick: (info: { key: string }) => {
      if (info.key === 'logout') handleLogout();
    },
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={220} theme="light" style={{ borderRight: '1px solid #f0f0f0' }}>
        <div style={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'center', borderBottom: '1px solid #f0f0f0' }}>
          <Text strong style={{ fontSize: 18 }}>SEKB 知识库</Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname === '/' ? '/' : location.pathname]}
          items={menuItems}
          onClick={handleMenuClick}
          style={{ borderRight: 0, marginTop: 8 }}
        />
        <div style={{ position: 'absolute', bottom: 16, left: 0, right: 0, padding: '0 16px' }}>
          <Dropdown menu={userDropdownItems} placement="topRight">
            <Space style={{ cursor: 'pointer', padding: '8px 12px', borderRadius: 6, width: '100%', justifyContent: 'center' }}>
              <Avatar size="small" icon={<UserOutlined />} />
              <Text ellipsis style={{ maxWidth: 120 }}>{user?.name || user?.email}</Text>
            </Space>
          </Dropdown>
        </div>
      </Sider>
      <Layout>
        <Content style={{ padding: 0, overflow: 'auto' }}>
          <Routes>
            <Route index element={<Chat />} />
            <Route path="files" element={<Files />} />
            <Route path="knowledge" element={<Knowledge />} />
            <Route path="settings" element={<Settings />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
};

export default AppLayout;