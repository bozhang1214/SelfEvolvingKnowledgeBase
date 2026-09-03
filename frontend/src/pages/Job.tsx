import React, { useMemo, useState } from 'react';
import {
  Card, Input, Button, Tabs, Typography, Space, Spin, Empty, message, Row, Col, Tag, List,
} from 'antd';
import {
  ThunderboltOutlined, ClearOutlined, FileSearchOutlined, SearchOutlined,
} from '@ant-design/icons';
import {
  analyzeJob,
  fetchJobs,
  type JobAnalyzeResult,
  type JobMeta,
  type FetchedJob,
} from '@/services/job';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

type SectionKey =
  | 'job_analysis'
  | 'knowledge_priority'
  | 'interview_qa'
  | 'gap_analysis'
  | 'resume_advice'
  | 'learning_plan'
  | 'project_iteration'
  | 'job_strategy';

const SECTION_LABELS: Record<SectionKey, string> = {
  job_analysis: '岗位分析',
  knowledge_priority: '知识点优先级',
  interview_qa: '面试 Q&A',
  gap_analysis: '差距分析',
  resume_advice: '简历建议',
  learning_plan: '学习计划',
  project_iteration: '项目迭代',
  job_strategy: '求职策略',
};

const SECTION_ORDER: SectionKey[] = [
  'job_analysis',
  'knowledge_priority',
  'interview_qa',
  'gap_analysis',
  'resume_advice',
  'learning_plan',
  'project_iteration',
  'job_strategy',
];

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function isEmptyValue(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  if (Array.isArray(v)) return v.length === 0;
  if (isPlainObject(v)) return Object.keys(v).length === 0;
  if (typeof v === 'string') return v.trim() === '';
  return false;
}

/** 递归渲染任意 JSON（对象/数组/标量），用于展示 LLM 结构化输出。 */
const JsonBlock: React.FC<{ data: unknown }> = ({ data }) => {
  if (data === null || data === undefined) return <Text type="secondary">—</Text>;

  if (Array.isArray(data)) {
    if (data.length === 0) return <Text type="secondary">（空）</Text>;
    return (
      <ul style={{ paddingLeft: 18, margin: 0 }}>
        {data.map((item, i) => (
          <li key={i} style={{ marginBottom: 4 }}>
            {isPlainObject(item) || Array.isArray(item) ? (
              <JsonBlock data={item} />
            ) : (
              <Text>{String(item)}</Text>
            )}
          </li>
        ))}
      </ul>
    );
  }

  if (isPlainObject(data)) {
    const entries = Object.entries(data);
    if (entries.length === 0) return <Text type="secondary">（空）</Text>;
    return (
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <tbody>
          {entries.map(([key, value]) => (
            <tr key={key} style={{ borderBottom: '1px solid #f0f0f0', verticalAlign: 'top' }}>
              <td
                style={{
                  padding: '6px 10px',
                  width: 180,
                  fontWeight: 600,
                  color: '#333',
                  background: '#fafafa',
                  whiteSpace: 'nowrap',
                }}
              >
                {key}
              </td>
              <td style={{ padding: '6px 10px' }}>
                {isPlainObject(value) || Array.isArray(value) ? (
                  <JsonBlock data={value} />
                ) : (
                  <Text>{String(value)}</Text>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  return <Text>{String(data)}</Text>;
};

const Job: React.FC = () => {
  const [jdText, setJdText] = useState('');
  const [meta, setMeta] = useState<JobMeta>({});
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<JobAnalyzeResult | null>(null);
  const [activeTab, setActiveTab] = useState<SectionKey>('job_analysis');

  // 职位采集（多源）
  const [fetchKeyword, setFetchKeyword] = useState('');
  const [fetching, setFetching] = useState(false);
  const [fetchedJobs, setFetchedJobs] = useState<FetchedJob[]>([]);
  const [companyFilter, setCompanyFilter] = useState('');

  const handleFetch = async () => {
    const kw = fetchKeyword.trim();
    if (!kw) {
      message.warning('请输入采集关键词');
      return;
    }
    setFetching(true);
    setFetchedJobs([]);
    try {
      const data = await fetchJobs({ keyword: kw });
      setFetchedJobs(data.jobs);
      if (data.jobs.length === 0) {
        message.info('未采集到职位（接口可能被限流或关键词无结果）');
      } else {
        message.success(`采集到 ${data.count} 个职位`);
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '职位采集失败');
    } finally {
      setFetching(false);
    }
  };

  /** 点击采集到的职位：有 JD 文本则填入分析框，否则跳转原链接 */
  const handlePickJob = (job: FetchedJob) => {
    if (job.jd_text && job.jd_text.trim()) {
      setJdText(job.jd_text);
      setMeta({ company: job.company, position: job.title, city: job.city, salary: job.salary });
      message.success('已填入职位描述，请点击「开始分析」');
    } else {
      if (job.job_url) {
        window.open(job.job_url, '_blank');
        message.info('该职位未返回 JD 文本，已打开职位详情页，请复制 JD 粘贴到上方分析框');
      } else {
        message.warning('该职位缺少 JD 文本，请手动粘贴职位描述');
      }
    }
  };

  const handleAnalyze = async () => {
    const text = jdText.trim();
    if (!text) {
      message.warning('请先粘贴职位描述（JD）');
      return;
    }
    const jobMeta = Object.fromEntries(
      Object.entries(meta).filter(([, v]) => (v as string)?.trim() !== ''),
    ) as JobMeta;

    setAnalyzing(true);
    setResult(null);
    try {
      const data = await analyzeJob({ jd_text: text, job_meta: jobMeta });
      setResult(data);
      setActiveTab('job_analysis');
      message.success('分析完成');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '分析失败，请稍后重试');
    } finally {
      setAnalyzing(false);
    }
  };

  const handleClear = () => {
    setJdText('');
    setMeta({});
    setResult(null);
  };

  const tabItems = useMemo(
    () =>
      SECTION_ORDER.map((key) => {
        const data = result ? result[key] : undefined;
        const empty = result === null || isEmptyValue(data);
        return {
          key,
          label: (
            <span>
              {SECTION_LABELS[key]}
              {result !== null && empty && <Tag color="default" style={{ marginLeft: 4 }}>无结果</Tag>}
            </span>
          ),
          children: empty ? (
            <Empty description="该步骤未产出结果（可能是 LLM 降级或输入不足）" />
          ) : (
            <JsonBlock data={data} />
          ),
        };
      }),
    [result],
  );

  return (
    <div style={{ padding: 24, overflow: 'auto', background: '#fff', minHeight: '100%' }}>
      {/* 职位采集（多源，免登录） */}
      <Card
        title={
          <Space>
            <SearchOutlined />
            <Text strong>职位采集</Text>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
              多源采集真实职位（猎聘/字节/腾讯/百度/小米/阿里/小红书，免登录），点击职位自动填入下方分析框
            </Text>
          </Space>
        }
        style={{ maxWidth: 1080, margin: '0 auto 16px' }}
      >
        <Space.Compact style={{ width: '100%' }}>
          <Input
            placeholder="采集关键词，如：AI Agent / 大模型应用工程师 / LangGraph"
            value={fetchKeyword}
            onChange={(e) => setFetchKeyword(e.target.value)}
            onPressEnter={handleFetch}
          />
          <Button type="primary" icon={<SearchOutlined />} loading={fetching} onClick={handleFetch}>
            采集职位
          </Button>
        </Space.Compact>
        {fetchedJobs.length > 0 && (
          <Input
            placeholder="按公司筛选（如：字节 / 智谱 / 月之暗面）"
            value={companyFilter}
            onChange={(e) => setCompanyFilter(e.target.value)}
            allowClear
            style={{ marginTop: 12 }}
            prefix={<Text type="secondary">公司</Text>}
          />
        )}
        {fetchedJobs.length > 0 && (
          <List
            size="small"
            style={{ marginTop: 12 }}
            dataSource={fetchedJobs.filter((j) =>
              !companyFilter.trim() || (j.company || '').toLowerCase().includes(companyFilter.trim().toLowerCase())
            )}
            renderItem={(job) => (
              <List.Item
                key={job.job_id || `${job.title}-${job.company}`}
                onClick={() => handlePickJob(job)}
                style={{ cursor: 'pointer' }}
                actions={[
                  <Button key="pick" size="small" type="link">
                    {job.jd_text ? '填入分析' : '打开详情'}
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space size={8}>
                      <Text strong>{job.title}</Text>
                      <Tag color="blue">{job.salary || '面议'}</Tag>
                      {job.source && <Tag style={{ fontSize: 11 }}>{job.source}</Tag>}
                    </Space>
                  }
                  description={
                    <Space size={8}>
                      <Text strong>{job.company}</Text>
                      {job.city && <Text type="secondary">{job.city}</Text>}
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>

      <Card
        title={
          <Space>
            <FileSearchOutlined />
            <Text strong>招聘分析</Text>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
              粘贴职位 JD，自动产出岗位定位 / 知识点 / 面试题 / 差距 / 简历建议 / 求职策略
            </Text>
          </Space>
        }
        style={{ maxWidth: 1080, margin: '0 auto' }}
      >
        <TextArea
          value={jdText}
          onChange={(e) => setJdText(e.target.value)}
          rows={9}
          placeholder={'在此粘贴职位描述（JD）原文，例如：\n【岗位】AI 应用工程师\n【职责】负责 Agent 应用落地，设计多 Agent 编排……\n【要求】本科及以上，熟悉 LangGraph、RAG、Python/FastAPI……'}
          style={{ marginBottom: 12 }}
        />
        <Row gutter={12} style={{ marginBottom: 16 }}>
          <Col xs={12} sm={6}>
            <Input
              placeholder="公司（可选）"
              value={meta.company || ''}
              onChange={(e) => setMeta({ ...meta, company: e.target.value })}
              allowClear
            />
          </Col>
          <Col xs={12} sm={6}>
            <Input
              placeholder="职位名（可选）"
              value={meta.position || ''}
              onChange={(e) => setMeta({ ...meta, position: e.target.value })}
              allowClear
            />
          </Col>
          <Col xs={12} sm={6}>
            <Input
              placeholder="城市（可选）"
              value={meta.city || ''}
              onChange={(e) => setMeta({ ...meta, city: e.target.value })}
              allowClear
            />
          </Col>
          <Col xs={12} sm={6}>
            <Input
              placeholder="薪资（可选）"
              value={meta.salary || ''}
              onChange={(e) => setMeta({ ...meta, salary: e.target.value })}
              allowClear
            />
          </Col>
        </Row>
        <Space>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={analyzing}
            onClick={handleAnalyze}
          >
            开始分析
          </Button>
          <Button icon={<ClearOutlined />} onClick={handleClear} disabled={analyzing}>
            清空
          </Button>
        </Space>
      </Card>

      <Card
        style={{ maxWidth: 1080, margin: '16px auto 0' }}
        styles={{ body: { paddingTop: 12 } }}
      >
        {result === null ? (
          <Spin spinning={analyzing} tip="分析中，需依次调用多个 LLM 步骤，请稍候…">
            <div style={{ minHeight: 160 }}>
              <Empty description={analyzing ? '正在分析职位…' : '粘贴 JD 后点击「开始分析」'} />
            </div>
          </Spin>
        ) : (
          <>
            <Paragraph type="secondary" style={{ fontSize: 13 }}>
              岗位定位：{result.job_analysis?.positioning || result.job_analysis?.position || '（未产出）'}
            </Paragraph>
            <Tabs
              activeKey={activeTab}
              onChange={(k) => setActiveTab(k as SectionKey)}
              items={tabItems}
            />
          </>
        )}
      </Card>
    </div>
  );
};

export default Job;
