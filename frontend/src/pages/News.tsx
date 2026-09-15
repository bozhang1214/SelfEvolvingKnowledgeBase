import React, { useEffect, useRef, useState } from 'react';
import {
  Layout, List, Button, Typography, Tag, Spin, Empty, message, Card, Space, Tabs, Alert,
} from 'antd';
import {
  ReloadOutlined, FileTextOutlined, ThunderboltOutlined, CalendarOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  listReports, getReport, refreshNews,
  listPeriodic, getPeriodic, generatePeriodic, getNewsStatus,
  type NewsTaskStatus,
  type NewsReportMeta, type NewsReport,
  type PeriodicType, type PeriodicReportMeta, type PeriodicReport,
} from '@/services/news';
import { useUserStore } from '@/stores/user';

const TASK_LABEL: Record<TabKey, string> = { daily: '日报', weekly: '周报', monthly: '月报' };

const { Text } = Typography;
const { Sider, Content } = Layout;

type TabKey = 'daily' | 'weekly' | 'monthly';

const News: React.FC = () => {
  const { user } = useUserStore();
  const isPreview = user?.access_level === 'preview';
  const [tab, setTab] = useState<TabKey>('daily');

  // 日报
  const [reports, setReports] = useState<NewsReportMeta[]>([]);
  const [current, setCurrent] = useState<NewsReport | null>(null);
  const [currentDate, setCurrentDate] = useState<string>('');

  // 周报/月报
  const [pReports, setPReports] = useState<PeriodicReportMeta[]>([]);
  const [pCurrent, setPCurrent] = useState<PeriodicReport | null>(null);
  const [pPeriod, setPPeriod] = useState<string>('');

  const [loadingList, setLoadingList] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  // 哪个 tab 的任务在跑（null = 都没跑）。原来是一个布尔值共用，导致切到任何 tab
  // 都显示 loading、分不清是哪个任务在跑。
  const [generatingKey, setGeneratingKey] = useState<TabKey | null>(null);
  const [taskStatus, setTaskStatus] = useState<NewsTaskStatus | null>(null);
  // 已经加载过的 tab：切回来不再重复请求（每次切换都发「列表+详情」会打满网关配额，
  // 实测被打成 429 → 页面报「加载周期报告列表失败」）
  const loadedTabs = useRef<Set<TabKey>>(new Set());

  const loadReports = async (autoSelectLatest = false) => {
    setLoadingList(true);
    try {
      const data = await listReports();
      setReports(data);
      if (data.length > 0 && (!currentDate || autoSelectLatest)) {
        const latest = data[0].date;
        setCurrentDate(latest);
        loadReport(latest);
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载日报列表失败');
    } finally {
      setLoadingList(false);
    }
  };

  const loadReport = async (date: string) => {
    setLoadingDetail(true);
    setCurrentDate(date);
    try {
      const data = await getReport(date);
      setCurrent(data);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载日报失败');
      setCurrent(null);
    } finally {
      setLoadingDetail(false);
    }
  };

  const loadPeriodic = async (type: PeriodicType, autoSelectLatest = false) => {
    setLoadingList(true);
    try {
      const data = await withRetry(() => listPeriodic(type));
      loadedTabs.current.add(type as TabKey);
      setPReports(data);
      if (data.length > 0 && (!pPeriod || autoSelectLatest)) {
        const latest = data[0].period;
        setPPeriod(latest);
        loadPeriodicDetail(type, latest);
      } else if (data.length === 0) {
        setPCurrent(null);
      }
    } catch (e: any) {
      message.error(describeError(e, '加载周期报告列表失败'));
    } finally {
      setLoadingList(false);
    }
  };

  const loadPeriodicDetail = async (type: PeriodicType, period: string) => {
    setLoadingDetail(true);
    setPPeriod(period);
    try {
      const data = await getPeriodic(type, period);
      setPCurrent(data);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载周期报告失败');
      setPCurrent(null);
    } finally {
      setLoadingDetail(false);
    }
  };

  /** 把 axios 错误翻译成用户能懂的话。
   *
   *  为什么需要：429 有两种来源（**应用侧限流中间件**按 IP+路由组 60s 滑动窗口，
   *  以及 nginx 的 limit_req），原来的通用文案「加载周期报告列表失败」让人以为是功能坏了。
   *  这里不再猜是哪一层（对用户没意义），只告诉他「等一会儿再试」——窗口是 60 秒。
   */
  const describeError = (e: any, fallback: string): string => {
    const status = e?.response?.status;
    if (status === 429) return '请求过于频繁，已自动重试；仍失败请等 1 分钟再试';
    if (status === 409) return e?.response?.data?.detail || '任务正在生成中，请稍候';
    if (status === 502 || status === 503 || status === 504) return '服务正在重启或过载，请稍后重试';
    return e?.response?.data?.detail || fallback;
  };

  /** 对 429/5xx 做一次退避重试（网关限流与部署重启都是瞬时的）。 */
  const withRetry = async <T,>(fn: () => Promise<T>, times = 2, delayMs = 1500): Promise<T> => {
    let lastErr: any;
    for (let i = 0; i < times; i += 1) {
      try {
        return await fn();
      } catch (e: any) {
        lastErr = e;
        const st = e?.response?.status;
        if (st !== 429 && !(st >= 500 && st < 600)) throw e;
        if (i < times - 1) await new Promise((r) => setTimeout(r, delayMs * (i + 1)));
      }
    }
    throw lastErr;
  };

  /** 轮询任务状态：等到「最近一次任务完成时间」变化（或超时）。
   *
   *  为什么要轮询而不是等请求返回：周报/月报实测耗时约 10 分钟，浏览器与网关都容易
   *  先超时，界面就会一直转圈且拿不到结果。提交后立刻返回、由状态接口汇报结果，
   *  界面才不会假死。
   */
  const pollTask = async (kind: TabKey, prevFinishedAt?: string) => {
    for (let i = 0; i < 120; i += 1) {          // 15s × 120 ≈ 30 分钟上限
      await new Promise((r) => setTimeout(r, 15000));
      try {
        const st = await getNewsStatus();
        if (st && st.kind === kind && st.finished_at !== prevFinishedAt) {
          setTaskStatus(st);
          return st;
        }
      } catch {
        // 后端可能正在重启（部署），继续下一轮
      }
    }
    return null;
  };

  const finishTask = async (kind: TabKey, prev: string | undefined, okText: string) => {
    const st = await pollTask(kind, prev);
    if (st && st.ok) {
      message.success(okText);
    } else if (st) {
      message.error(`${okText.split('：')[0]}失败：${st.error || '原因未知'}`);
    } else {
      message.warning('任务仍在后台运行，稍后刷新查看结果');
    }
    if (kind === 'daily') {
      await loadReports(true);
    } else {
      await loadPeriodic(kind as PeriodicType, true);
    }
  };

  const handleRefresh = async () => {
    setGeneratingKey('daily');
    const prev = taskStatus?.finished_at;
    try {
      const result = await refreshNews(true);
      if (result.accepted === false) {
        message.info('今日日报已生成，无需重复生成');
        await loadReports(true);
      } else {
        // 接口已改为「提交任务，立即返回」：结果靠轮询 /status 获取
        message.info('已提交日报生成任务，正在后台生成…');
        await finishTask('daily', prev, '日报生成完成');
      }
    } catch (e: any) {
      if (e?.response?.status === 409) {
        message.info(describeError(e, '日报正在生成中'));
        await finishTask('daily', prev, '日报生成完成');
      } else {
        message.error(describeError(e, '生成日报失败'));
      }
    } finally {
      setGeneratingKey(null);
    }
  };

  const handleGeneratePeriodic = async (type: PeriodicType) => {
    const label = type === 'weekly' ? '周报' : '月报';
    setGeneratingKey(type as TabKey);
    const prev = taskStatus?.finished_at;
    try {
      // 后端会在生成完成后把结果写进状态文件；这里只负责提交 + 轮询
      await generatePeriodic(type);
      message.info(`已提交${label}生成任务，正在后台生成…`);
      await finishTask(type as TabKey, prev, `${label}生成完成，请查看列表`);
    } catch (e: any) {
      if (e?.response?.status === 409) {
        message.info(describeError(e, `${label}正在生成中`));
        await finishTask(type as TabKey, prev, `${label}生成完成，请查看列表`);
      } else {
        message.error(describeError(e, `生成${label}失败`));
      }
    } finally {
      setGeneratingKey(null);
    }
  };

  useEffect(() => {
    loadReports(true);
    getNewsStatus().then(setTaskStatus).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onTabChange = (key: string) => {
    const k = key as TabKey;
    setTab(k);
    if (loadedTabs.current.has(k)) return;      // 已加载过：不重复请求
    loadedTabs.current.add(k);
    if (k === 'daily') {
      loadReports(true);
    } else {
      loadPeriodic(k, true);
    }
  };

  const isDaily = tab === 'daily';
  const listData: {
    key: string;
    label: string;
    meta: NewsReportMeta | PeriodicReportMeta;
  }[] = isDaily
    ? reports.map((r) => ({ key: r.date, label: r.date, meta: r }))
    : pReports.map((r) => ({ key: r.period, label: r.period, meta: r }));

  const activeKey = isDaily ? currentDate : pPeriod;
  const activeDetail = isDaily ? current : pCurrent;
  const detailTitle = 'AI 科技资讯';

  return (
    <Layout style={{ minHeight: '100%' }}>
      <Sider
        width={300}
        theme="light"
        style={{ borderRight: '1px solid #f0f0f0', overflow: 'auto', background: '#fafafa' }}
      >
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0' }}>
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Text strong style={{ fontSize: 15 }}>科技资讯</Text>
            <Tabs
              size="small"
              activeKey={tab}
              onChange={onTabChange}
              items={[
                { key: 'daily', label: '日报' },
                { key: 'weekly', label: '周报' },
                { key: 'monthly', label: '月报' },
              ]}
            />
            {!isPreview && (
              <Button
                type="primary"
                block
                icon={<ThunderboltOutlined />}
                loading={generatingKey === tab}
                disabled={generatingKey !== null && generatingKey !== tab}
                onClick={() => (isDaily ? handleRefresh() : handleGeneratePeriodic(tab as PeriodicType))}
              >
                {isDaily ? '重新生成日报' : tab === 'weekly' ? '生成周报' : '生成月报'}
              </Button>
            )}
            <Button
              block
              icon={<ReloadOutlined />}
              onClick={() => (isDaily ? loadReports(false) : loadPeriodic(tab as PeriodicType, false))}
              loading={loadingList}
            >
              刷新列表
            </Button>
          </Space>
        </div>
        {taskStatus && !taskStatus.ok ? (
          <Alert
            type="error"
            showIcon
            closable
            style={{ marginBottom: 12 }}
            message={`最近一次${TASK_LABEL[taskStatus.kind] || ''}生成任务失败`}
            description={`${taskStatus.error || '原因未知'}（完成时间 ${taskStatus.finished_at || '未知'}）`}
          />
        ) : null}
        <Spin spinning={loadingList}>
          {listData.length === 0 ? (
            <div style={{ padding: 24 }}>
              <Empty description={isDaily ? '暂无日报' : '暂无周期报告'} />
            </div>
          ) : (
            <List
              size="small"
              dataSource={listData}
              renderItem={(item) => (
                <List.Item
                  onClick={() =>
                    isDaily
                      ? loadReport(item.key)
                      : loadPeriodicDetail(tab as PeriodicType, item.key)
                  }
                  style={{
                    cursor: 'pointer',
                    padding: '10px 16px',
                    background: item.key === activeKey ? '#e6f4ff' : 'transparent',
                  }}
                >
                  <List.Item.Meta
                    title={
                      <Space size={6}>
                        <CalendarOutlined style={{ fontSize: 12, color: '#999' }} />
                        <Text strong style={{ fontSize: 13 }}>{item.label}</Text>
                        {isDaily && <Tag color="blue" style={{ fontSize: 11 }}>{(item.meta as NewsReportMeta).total_count} 条</Tag>}
                      </Space>
                    }
                    description={
                      <Text type="secondary" ellipsis style={{ fontSize: 12 }}>
                        {isDaily ? (item.meta as NewsReportMeta).headline || '（无头条）' : `周期报告 ${item.label}`}
                      </Text>
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </Spin>
      </Sider>

      <Content style={{ padding: 24, overflow: 'auto', background: '#fff' }}>
        <Spin spinning={loadingDetail}>
          {!activeDetail ? (
            <div style={{ textAlign: 'center', marginTop: 120 }}>
              <FileTextOutlined style={{ fontSize: 40, color: '#ccc' }} />
              <br />
              <Text type="secondary" style={{ fontSize: 15, marginTop: 8 }}>
                选择左侧报告查看内容
              </Text>
            </div>
          ) : (
            <Card
              size="small"
              title={
                <Space>
                  <Text strong>{detailTitle}</Text>
                  {isDaily && <Tag color="blue">{(activeDetail as NewsReport).total_count} 条</Tag>}
                </Space>
              }
              style={{ maxWidth: 860, margin: '0 auto' }}
            >
              <div className="markdown-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeDetail.markdown || ''}</ReactMarkdown>
              </div>
            </Card>
          )}
        </Spin>
      </Content>
    </Layout>
  );
};

export default News;
