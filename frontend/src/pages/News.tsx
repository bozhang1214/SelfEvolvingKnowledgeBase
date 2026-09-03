import React, { useEffect, useState } from 'react';
import {
  Layout, List, Button, Typography, Tag, Spin, Empty, message, Card, Space,
} from 'antd';
import {
  ReloadOutlined, FileTextOutlined, ThunderboltOutlined, CalendarOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  listReports, getReport, refreshNews,
  type NewsReportMeta, type NewsReport,
} from '@/services/news';

const { Title, Text } = Typography;
const { Sider, Content } = Layout;

const News: React.FC = () => {
  const [reports, setReports] = useState<NewsReportMeta[]>([]);
  const [current, setCurrent] = useState<NewsReport | null>(null);
  const [currentDate, setCurrentDate] = useState<string>('');
  const [loadingList, setLoadingList] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const loadReports = async (autoSelectLatest = false) => {
    setLoadingList(true);
    try {
      const data = await listReports();
      setReports(data);
      // 默认选中最新一篇
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

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const result = await refreshNews();
      message.success(`日报生成完成：采集 ${result.fetched} 条，筛选 ${result.filtered} 条`);
      await loadReports(true);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '生成日报失败');
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadReports(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Layout style={{ minHeight: '100%' }}>
      {/* 左侧：日报列表 */}
      <Sider
        width={300}
        theme="light"
        style={{ borderRight: '1px solid #f0f0f0', overflow: 'auto', background: '#fafafa' }}
      >
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0' }}>
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Text strong style={{ fontSize: 15 }}>资讯日报</Text>
            <Button
              type="primary"
              block
              icon={<ThunderboltOutlined />}
              loading={refreshing}
              onClick={handleRefresh}
            >
              生成今日日报
            </Button>
            <Button block icon={<ReloadOutlined />} onClick={() => loadReports(false)} loading={loadingList}>
              刷新列表
            </Button>
          </Space>
        </div>
        <Spin spinning={loadingList}>
          {reports.length === 0 ? (
            <div style={{ padding: 24 }}>
              <Empty description="暂无日报，点击「生成今日日报」" />
            </div>
          ) : (
            <List
              size="small"
              dataSource={reports}
              renderItem={(item) => (
                <List.Item
                  onClick={() => loadReport(item.date)}
                  style={{
                    cursor: 'pointer',
                    padding: '10px 16px',
                    background: item.date === currentDate ? '#e6f4ff' : 'transparent',
                  }}
                >
                  <List.Item.Meta
                    title={
                      <Space size={6}>
                        <CalendarOutlined style={{ fontSize: 12, color: '#999' }} />
                        <Text strong style={{ fontSize: 13 }}>{item.date}</Text>
                        <Tag color="blue" style={{ fontSize: 11 }}>{item.total_count} 条</Tag>
                      </Space>
                    }
                    description={
                      <Text type="secondary" ellipsis style={{ fontSize: 12 }}>
                        {item.headline || '（无头条）'}
                      </Text>
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </Spin>
      </Sider>

      {/* 右侧：日报正文 */}
      <Content style={{ padding: 24, overflow: 'auto', background: '#fff' }}>
        <Spin spinning={loadingDetail}>
          {!current ? (
            <div style={{ textAlign: 'center', marginTop: 120 }}>
              <FileTextOutlined style={{ fontSize: 40, color: '#ccc' }} />
              <br />
              <Text type="secondary" style={{ fontSize: 15, marginTop: 8 }}>
                选择左侧日报查看内容
              </Text>
            </div>
          ) : (
            <Card
              size="small"
              title={
                <Space>
                  <Text strong>AI 资讯日报 · {current.date}</Text>
                  <Tag color="blue">{current.total_count} 条</Tag>
                </Space>
              }
              style={{ maxWidth: 860, margin: '0 auto' }}
            >
              <div className="markdown-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{current.markdown || ''}</ReactMarkdown>
              </div>
            </Card>
          )}
        </Spin>
      </Content>
    </Layout>
  );
};

export default News;
