/**
 * 科技资讯页：周报/月报列表错位回归测试。
 *
 * 背景（owner 报的 bug）：日报→周报→月报顺序切换列表都正常；但**切回周报**时展示的是
 * 月报列表，周报里点「刷新」后再切月报又展示周报列表。
 *
 * 根因：周报/月报共用一个 pReports/pCurrent/pPeriod 状态，而 onTabChange 用 loadedTabs
 * 去重「切回已加载的 tab 不发请求」→ 切回周报时 pReports 里还留着上一次月报的数据。
 * 修复：按类型分存（Record<PeriodicType, ...>），切 tab 只改渲染取哪个 key。
 */
import { describe, it, expect, vi, beforeAll, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import News from '@/pages/News';

// ---- Mock 依赖 ----

const periodicLists: Record<'weekly' | 'monthly', Array<{ type: string; period: string; path: string }>> = {
  weekly: [{ type: 'weekly', period: '2026-09-07', path: '' }],
  monthly: [{ type: 'monthly', period: '2026-08', path: '' }],
};

const listPeriodicCalls = { weekly: 0, monthly: 0 };

vi.mock('@/services/news', () => ({
  listReports: vi.fn(async () => []),
  getReport: vi.fn(async () => ({ date: '', total_count: 0, headline: '', path: '', markdown: '' })),
  refreshNews: vi.fn(async () => ({ accepted: true, kind: 'daily' })),
  listPeriodic: vi.fn(async (type: 'weekly' | 'monthly') => {
    listPeriodicCalls[type] += 1;
    return periodicLists[type];
  }),
  getPeriodic: vi.fn(async (type: 'weekly' | 'monthly', period: string) => ({
    type, period, path: '', markdown: `# ${type} ${period}`,
  })),
  generatePeriodic: vi.fn(async () => ({ accepted: true, kind: 'monthly' })),
  getNewsStatus: vi.fn(async () => null),
}));

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ user: { access_level: 'full' } }),
}));

// ---- antd 响应式组件依赖 matchMedia ----
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
  listPeriodicCalls.weekly = 0;
  listPeriodicCalls.monthly = 0;
});

describe('科技资讯页 周报/月报 tab 切换', () => {
  it('切回周报时展示周报列表（而非月报）—— 回归原 bug', async () => {
    const user = userEvent.setup();
    render(<News />);

    // 1. 切到周报 → 显示周报列表（period 2026-09-07）
    await user.click(screen.getByRole('tab', { name: '周报' }));
    await waitFor(() => expect(screen.getByText('2026-09-07')).toBeInTheDocument());

    // 2. 切到月报 → 显示月报列表（period 2026-08）
    await user.click(screen.getByRole('tab', { name: '月报' }));
    await waitFor(() => expect(screen.getByText('2026-08')).toBeInTheDocument());
    expect(screen.queryByText('2026-09-07')).not.toBeInTheDocument();

    // 3. 切回周报 → 必须显示周报列表，而不是残留的月报列表（这是 bug 点）
    await user.click(screen.getByRole('tab', { name: '周报' }));
    await waitFor(() => expect(screen.getByText('2026-09-07')).toBeInTheDocument());
    expect(screen.queryByText('2026-08')).not.toBeInTheDocument();

    // 4. 再切回月报 → 仍显示月报列表
    await user.click(screen.getByRole('tab', { name: '月报' }));
    await waitFor(() => expect(screen.getByText('2026-08')).toBeInTheDocument());
    expect(screen.queryByText('2026-09-07')).not.toBeInTheDocument();
  });

  it('切回已加载的 tab 不重复发列表请求（去重仍生效）', async () => {
    const user = userEvent.setup();
    render(<News />);

    await user.click(screen.getByRole('tab', { name: '周报' }));
    await waitFor(() => expect(screen.getByText('2026-09-07')).toBeInTheDocument());
    await user.click(screen.getByRole('tab', { name: '月报' }));
    await waitFor(() => expect(screen.getByText('2026-08')).toBeInTheDocument());

    // 反复横跳，各自列表请求都应只发一次（去重不受分仓修复影响）
    await user.click(screen.getByRole('tab', { name: '周报' }));
    await user.click(screen.getByRole('tab', { name: '月报' }));
    await user.click(screen.getByRole('tab', { name: '周报' }));

    expect(listPeriodicCalls.weekly).toBe(1);
    expect(listPeriodicCalls.monthly).toBe(1);
  });
});
