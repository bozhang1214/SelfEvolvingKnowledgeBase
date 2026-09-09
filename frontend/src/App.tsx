import React, { useEffect } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { useUserStore } from '@/stores/user';
import Login from '@/pages/Login';
import Register from '@/pages/Register';
import AppLayout from '@/components/Layout/AppLayout';
import SharedKnowledge from '@/pages/SharedKnowledge';
import SharedChat from '@/pages/SharedChat';

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const isLoggedIn = useUserStore((s) => s.isLoggedIn);
  const location = useLocation();
  if (!isLoggedIn) {
    // 未登录时跳登录页，并携带原始路径（含 query 如 conversation_id）供登录后回跳
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }
  return <>{children}</>;
};

const App: React.FC = () => {
  const init = useUserStore((s) => s.init);

  useEffect(() => {
    init();
  }, [init]);

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      {/* 分享知识库页面：独立全屏布局，内部处理登录校验与跳转 */}
      <Route path="/share/:shareId" element={<SharedKnowledge />} />
      {/* 分享聊天会话页面：只读对话历史（更具体的路径优先匹配） */}
      <Route path="/share/chat/:shareId" element={<SharedChat />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      />
    </Routes>
  );
};

export default App;