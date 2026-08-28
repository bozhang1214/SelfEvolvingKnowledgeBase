import React, { useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useUserStore } from '@/stores/user';
import Login from '@/pages/Login';
import Register from '@/pages/Register';
import Chat from '@/pages/Chat';
import AppLayout from '@/components/Layout/AppLayout';
import SharedKnowledge from '@/pages/SharedKnowledge';

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const isLoggedIn = useUserStore((s) => s.isLoggedIn);
  if (!isLoggedIn) return <Navigate to="/login" replace />;
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