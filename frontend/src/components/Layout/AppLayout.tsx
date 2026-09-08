import React from 'react';
import { Layout, Menu, Avatar, Dropdown, Typography, Space } from 'antd';
import {
  MessageOutlined,
  FileOutlined,
  DatabaseOutlined,
  SettingOutlined,
  LogoutOutlined,
  UserOutlined,
  ReadOutlined,
  SolutionOutlined,
  HomeOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation, Routes, Route, Navigate } from 'react-router-dom';
import { useUserStore } from '@/stores/user';
import Home from '@/pages/Home';
import Chat from '@/pages/Chat';
import Files from '@/pages/Files';
import Knowledge from '@/pages/Knowledge';
import Settings from '@/pages/Settings';
import News from '@/pages/News';
import Job from '@/pages/Job';

const { Sider, Content } = Layout;
const { Text } = Typography;

const menuItems = [
  { key: '/', icon: <HomeOutlined />, label: '功能说明' },
  { key: '/chat', icon: <MessageOutlined />, label: 'AI 对话' },
  { key: '/files', icon: <FileOutlined />, label: '文件管理' },
  { key: '/knowledge', icon: <DatabaseOutlined />, label: '知识库' },
  { key: '/news', icon: <ReadOutlined />, label: '科技资讯' },
  { key: '/job', icon: <SolutionOutlined />, label: '职位分析' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置' },
];

// 预览账号可访问的菜单项（仅功能说明 + 科技资讯）
const PREVIEW_MENU_KEYS = new Set(['/', '/news']);

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useUserStore();
  const isPreview = user?.access_level === 'preview';
  const visibleMenu = isPreview ? menuItems.filter((m) => PREVIEW_MENU_KEYS.has(m.key)) : menuItems;

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
          items={visibleMenu}
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
            <Route index element={<Home />} />
            <Route path="chat" element={isPreview ? <Navigate to="/" replace /> : <Chat />} />
            <Route path="files" element={isPreview ? <Navigate to="/" replace /> : <Files />} />
            <Route path="knowledge" element={isPreview ? <Navigate to="/" replace /> : <Knowledge />} />
            <Route path="news" element={<News />} />
            <Route path="job" element={isPreview ? <Navigate to="/" replace /> : <Job />} />
            <Route path="settings" element={isPreview ? <Navigate to="/" replace /> : <Settings />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
};

export default AppLayout;