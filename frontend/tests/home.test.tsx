/**
 * Home 页面组件测试（@testing-library/react，Batch 2）
 *
 * 验证纯展示页的基本渲染与交互：
 * - 主标题/功能卡片/引导步骤正确渲染
 * - 卡片「进入」按钮触发路由跳转
 *
 * 技术要点：
 * - MemoryRouter 包裹，避免依赖真实路由
 * - antd 需要 window.matchMedia polyfill（jsdom 不提供）
 */
import { describe, it, expect, beforeAll, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import Home from '@/pages/Home';

// antd 响应式组件依赖 matchMedia
beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  }
});

afterEach(() => {
  cleanup();
});

/** 探针：把当前 location.pathname 暴露为 data-testid，用于断言跳转 */
function PathProbe() {
  const location = useLocation();
  return <span data-testid="path-probe">{location.pathname}</span>;
}

function renderHome() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="*" element={<PathProbe />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('Home 页面', () => {
  it('渲染主标题与 SEKB 标识', () => {
    renderHome();
    expect(screen.getByText('自迭代个人知识库')).toBeInTheDocument();
    expect(screen.getByText('SEKB')).toBeInTheDocument();
  });

  it('渲染全部功能卡片（AI 对话 / 科技资讯 / 职位分析等）', () => {
    renderHome();
    for (const title of ['AI 对话', '文件管理', '知识库', '科技资讯', '职位分析', '设置']) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
    // 卡片描述也出现
    expect(screen.getByText(/每日 AI 资讯日报/)).toBeInTheDocument();
  });

  it('点击卡片「进入」跳转到对应路由', async () => {
    const user = userEvent.setup();
    renderHome();

    // 定位「科技资讯」卡片（key=/news）内的「进入」按钮
    const newsCard = screen.getByText('科技资讯').closest('.ant-card') as HTMLElement;
    const button = newsCard.querySelector('button');
    expect(button).not.toBeNull();
    await user.click(button as HTMLElement);

    // 未命中其它路由时进入 PathProbe，暴露当前 path
    await waitFor(() => {
      expect(screen.getByTestId('path-probe')).toBeInTheDocument();
    });
    expect(screen.getByTestId('path-probe').textContent).toBe('/news');
  });
});
