import React, { useEffect, useState } from 'react';
import {
  Layout, List, Button, Typography, Tag, Spin, Empty, message, Card, Space, Tabs,
} from 'antd';
import {
  ReloadOutlined, FileTextOutlined, ThunderboltOutlined, CalendarOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  listReports, getReport, refreshNews,
  listPeriodic, getPeriodic, generatePeriodic,
  type NewsReportMeta, type NewsReport,
  type PeriodicType, type PeriodicReportMeta, type PeriodicReport,
} from '@/services/news';

const { Text } = Typography;
const { Sider, Content } = Layout;

type TabKey = 'daily' | 'weekly' | 'monthly';

const News: React.FC = () => {
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
  const [generating, setGenerating] = useState(false);

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
      const data = await listPeriodic(type);
      setPReports(data);
      if (data.length > 0 && (!pPeriod || autoSelectLatest)) {
        const latest = data[0].period;
        setPPeriod(latest);
        loadPeriodicDetail(type, latest);
      } else if (data.length === 0) {
        setPCurrent(null);
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载周期报告列表失败');
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

  const handleRefresh = async () => {
    setGenerating(true);
    try {
      const result = await refreshNews();
      message.success(`日报生成完成：采集 ${result.fetched} 条，筛选 ${result.filtered} 条`);
      await loadReports(true);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '生成日报失败');
    } finally {
      setGenerating(false);
    }
  };

  const handleGeneratePeriodic = async (type: PeriodicType) => {
    setGenerating(true);
    try {
      const result = await generatePeriodic(type);
      const label = type === 'weekly' ? '周报' : '月报';
      message.success(`${label}生成完成：${result.period}`);
      await loadPeriodic(type, true);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '生成周期报告失败');
    } finally {
      setGenerating(false);
    }
  };

  useEffect(() => {
    loadReports(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onTabChange = (key: string) => {
    const k = key as TabKey;
    setTab(k);
    if (k === 'daily') {
      if (reports.length === 0) loadReports(true);
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
            <Button
              type="primary"
              block
              icon={<ThunderboltOutlined />}
              loading={generating}
              onClick={() => (isDaily ? handleRefresh() : handleGeneratePeriodic(tab as PeriodicType))}
            >
              {isDaily ? '生成今日日报' : tab === 'weekly' ? '生成周报' : '生成月报'}
            </Button>
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
