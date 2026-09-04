import React, { useEffect, useMemo, useState } from 'react';
import {
  Card, Input, Button, Tabs, Typography, Space, Spin, Empty, message, Row, Col, Tag, List, Modal, Divider, Alert, Select, Pagination,
} from 'antd';
import {
  ThunderboltOutlined, ClearOutlined, FileSearchOutlined, SearchOutlined, QrcodeOutlined,
  BarChartOutlined, DeleteOutlined, ReloadOutlined,
} from '@ant-design/icons';
import {
  analyzeJob,
  fetchJobs,
  bossQrStart,
  bossQrStatus,
  batchAnalyze,
  deleteBatchAnalysis,
  type JobAnalyzeResult,
  type JobMeta,
  type FetchedJob,
  type MarketReport,
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

// 职位采集筛选选项
const CITY_OPTIONS = ['不限', '北京', '上海', '深圳', '杭州', '广州', '成都', '全国'];
const SALARY_OPTIONS = ['不限', '20K+', '30K+', '40K+', '50K+', '60K+', '80K+', '100K+'];

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
  const [fetchCity, setFetchCity] = useState('不限');
  const [fetchSalary, setFetchSalary] = useState('不限');
  const [fetching, setFetching] = useState(false);
  const [fetchedJobs, setFetchedJobs] = useState<FetchedJob[]>([]);
  const [companyFilter, setCompanyFilter] = useState('');
  // 批量报告职位列表分页（受控，修复「N 条/页」不生效）
  const [jobPage, setJobPage] = useState(1);
  const [jobPageSize, setJobPageSize] = useState(20);

  // BOSS 扫码登录
  const [qrOpen, setQrOpen] = useState(false);
  const [qrImageUrl, setQrImageUrl] = useState('');
  const [qrWaiting, setQrWaiting] = useState(false);
  const [qrTip, setQrTip] = useState('');

  // 批量市场分析
  const [batchAnalyzing, setBatchAnalyzing] = useState(false);
  const [marketReport, setMarketReport] = useState<MarketReport | null>(null);
  // 分析模式：批量分析 / 单职位分析 / BOSS 登录
  const [analysisMode, setAnalysisMode] = useState<'batch' | 'single' | 'boss'>('batch');

  const handleBatchAnalyze = async (force = false) => {
    setBatchAnalyzing(true);
    try {
      const { cached, report } = await batchAnalyze({ keyword: fetchKeyword.trim() || undefined, force });
      setMarketReport(report);
      message.success(cached ? '已加载缓存的分析报告（7 天内）' : '批量分析完成');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '批量分析失败');
    } finally {
      setBatchAnalyzing(false);
    }
  };

  const handleDeleteReport = async () => {
    try {
      await deleteBatchAnalysis();
      setMarketReport(null);
      message.success('分析报告已删除，可重新分析');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败');
    }
  };

  // 进入页面自动加载/触发批量分析（7 天内命中缓存则直接展示，否则自动分析）
  useEffect(() => {
    let mounted = true;
    (async () => {
      setBatchAnalyzing(true);
      try {
        const { report } = await batchAnalyze({});
        if (mounted) setMarketReport(report);
      } catch {
        // 静默失败，用户可手动点「一键分析」
      } finally {
        if (mounted) setBatchAnalyzing(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  const handleBossQrLogin = async () => {
    setQrOpen(true);
    setQrWaiting(true);
    setQrImageUrl('');
    setQrTip('');
    let timer: ReturnType<typeof setInterval> | undefined;
    let finished = false;
    const finish = () => {
      finished = true;
      if (timer) clearInterval(timer);
      setQrWaiting(false);
    };
    try {
      const { qr_id, qr_image_url } = await bossQrStart();
      setQrImageUrl(qr_image_url);
      setQrTip('用手机 BOSS App「扫一扫」此二维码');
      // 轮询状态机（第一张码扫完会换第二张码）
      timer = setInterval(async () => {
        try {
          const st = await bossQrStatus(qr_id);
          if (st.qr_image_url) setQrImageUrl(st.qr_image_url);
          if (st.phase === 'waiting_scan') {
            setQrTip('用手机 BOSS App「扫一扫」此二维码');
          } else if (st.phase === 'waiting_second_scan') {
            setQrTip('已扫描，请再次扫一扫这张新二维码');
          } else if (st.phase === 'waiting_confirm') {
            setQrTip('请在 BOSS App 上点击「确认登录」');
          } else if (st.phase === 'success') {
            finish();
            setQrOpen(false);
            message.success('BOSS 登录成功，Cookie 已保存，现在可以采集 BOSS 职位了');
          } else if (st.phase === 'expired') {
            finish();
            message.warning('二维码已过期，请重新点击「BOSS 扫码登录」');
          } else if (st.phase === 'login_failed') {
            finish();
            message.error(st.message || 'BOSS 登录失败，请重试');
          }
        } catch (e: any) {
          // 单个轮询失败不中断，继续等
        }
      }, 1500);
      // 最长轮询 3 分钟
      setTimeout(() => {
        if (!finished) {
          finish();
          message.warning('等待扫码超时，请重试');
        }
      }, 180000);
    } catch (e: any) {
      setQrWaiting(false);
      message.error(e?.response?.data?.detail || 'BOSS 扫码登录失败');
    }
  };

  const handleFetch = async () => {
    const kw = fetchKeyword.trim();
    if (!kw) {
      message.warning('请输入采集关键词');
      return;
    }
    // 薪资「不限」→ 0；否则解析「30K+」→ 30
    const salaryK = fetchSalary === '不限' ? 0 : parseInt(fetchSalary, 10) || 0;
    setFetching(true);
    setFetchedJobs([]);
    try {
      const data = await fetchJobs({
        keyword: kw,
        city: fetchCity === '不限' ? '' : fetchCity,
        min_salary_k: salaryK,
      });
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

  /** 点击采集到的职位：有 JD 文本则填入分析框并切到「单职位分析」，否则跳转原链接 */
  const handlePickJob = (job: FetchedJob) => {
    if (job.jd_text && job.jd_text.trim()) {
      setJdText(job.jd_text);
      setMeta({ company: job.company, position: job.title, city: job.city, salary: job.salary });
      setAnalysisMode('single');
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
      message.success(data.cached ? '已加载缓存的分析结果（14 天内）' : '分析完成');
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
        <Row gutter={12} align="middle">
          <Col xs={24} sm={4}>
            <Select
              value={fetchCity}
              onChange={setFetchCity}
              style={{ width: '100%' }}
              options={CITY_OPTIONS.map((c) => ({ value: c, label: c === '不限' ? '工作地：不限' : `工作地：${c}` }))}
            />
          </Col>
          <Col xs={24} sm={9}>
            <Input
              placeholder="关键字，多个用空格分隔（如：Agent 大模型）"
              value={fetchKeyword}
              onChange={(e) => setFetchKeyword(e.target.value)}
              onPressEnter={handleFetch}
            />
          </Col>
          <Col xs={24} sm={5}>
            <Select
              value={fetchSalary}
              onChange={setFetchSalary}
              style={{ width: '100%' }}
              options={SALARY_OPTIONS.map((s) => ({ value: s, label: s === '不限' ? '薪资：不限' : `薪资：${s}` }))}
            />
          </Col>
          <Col xs={24} sm={6}>
            <Button type="primary" icon={<SearchOutlined />} loading={fetching} onClick={handleFetch} block>
              采集职位
            </Button>
          </Col>
        </Row>
        <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
          说明：工作地/薪资默认「不限」；关键字支持按空格拼接多个关键词（如「Agent 大模型」），空则用默认「Agent」。
        </Paragraph>
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

      <Tabs
        activeKey={analysisMode}
        onChange={(k) => setAnalysisMode(k as 'batch' | 'single' | 'boss')}
        style={{ maxWidth: 1080, margin: '0 auto' }}
        items={[
          {
            key: 'batch',
            label: <span><BarChartOutlined /> 批量分析</span>,
            children: (
              <>
                {/* 批量市场分析 */}
                <Card
                  title={
          <Space>
            <BarChartOutlined />
            <Text strong>批量市场分析</Text>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
              一键分析采集结果（7 天内命中缓存直接展示）
            </Text>
          </Space>
        }
        extra={
          <Space>
            {marketReport && (
              <>
                <Button size="small" icon={<ReloadOutlined />} loading={batchAnalyzing} onClick={() => handleBatchAnalyze(true)}>
                  重新分析
                </Button>
                <Button size="small" danger icon={<DeleteOutlined />} onClick={handleDeleteReport}>
                  删除报告
                </Button>
              </>
            )}
            <Button type="primary" size="small" icon={<BarChartOutlined />} loading={batchAnalyzing} onClick={() => handleBatchAnalyze(false)}>
              一键分析
            </Button>
          </Space>
        }
        style={{ maxWidth: 1080, margin: '0 auto 16px' }}
      >
        {batchAnalyzing && !marketReport ? (
          <Spin tip="正在采集职位并生成市场分析报告，约需 30~60 秒…">
            <div style={{ minHeight: 120 }} />
          </Spin>
        ) : marketReport ? (
          <div>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={`分析于 ${marketReport.analyzed_at?.replace('T', ' ').slice(0, 19) || ''} · 共 ${marketReport.job_count} 个职位 · 关键词「${marketReport.keyword}」· ${marketReport.city}`}
            />
            {marketReport.overview && (
              <>
                <Paragraph strong style={{ marginBottom: 4 }}>市场概况</Paragraph>
                <Paragraph>{marketReport.overview}</Paragraph>
              </>
            )}

            <Row gutter={16}>
              <Col xs={24} sm={8}>
                <Paragraph strong style={{ marginBottom: 4 }}>公司分布</Paragraph>
                <Space wrap size={[4, 4]}>
                  {marketReport.stats?.company_distribution?.map((c) => (
                    <Tag key={c.name} color="blue">{c.name} × {c.count}</Tag>
                  ))}
                </Space>
              </Col>
              <Col xs={24} sm={8}>
                <Paragraph strong style={{ marginBottom: 4 }}>职位方向</Paragraph>
                <Space wrap size={[4, 4]}>
                  {marketReport.stats?.role_distribution?.map((c) => (
                    <Tag key={c.name} color="geekblue">{c.name} × {c.count}</Tag>
                  ))}
                </Space>
              </Col>
              <Col xs={24} sm={8}>
                <Paragraph strong style={{ marginBottom: 4 }}>热点关键词</Paragraph>
                <Space wrap size={[4, 4]}>
                  {marketReport.stats?.hot_keywords?.map((c) => (
                    <Tag key={c.keyword} color="purple">{c.keyword} × {c.count}</Tag>
                  ))}
                </Space>
              </Col>
            </Row>

            {marketReport.trends && marketReport.trends.length > 0 && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <Paragraph strong style={{ marginBottom: 4 }}>市场趋势</Paragraph>
                <List
                  size="small"
                  dataSource={marketReport.trends}
                  renderItem={(t) => <List.Item>· {t}</List.Item>}
                />
              </>
            )}

            {marketReport.opportunities && marketReport.opportunities.length > 0 && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <Paragraph strong style={{ marginBottom: 4 }}>重点机会</Paragraph>
                <List
                  size="small"
                  dataSource={marketReport.opportunities}
                  renderItem={(o) => (
                    <List.Item>
                      <List.Item.Meta
                        title={<Text strong>{o.title}</Text>}
                        description={<Text type="secondary">{o.company}</Text>}
                      />
                      <Text type="secondary" style={{ fontSize: 12, maxWidth: 420 }}>{o.reason}</Text>
                    </List.Item>
                  )}
                />
              </>
            )}

            {marketReport.recommendations && marketReport.recommendations.length > 0 && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <Paragraph strong style={{ marginBottom: 4 }}>行动建议</Paragraph>
                <List
                  size="small"
                  dataSource={marketReport.recommendations}
                  renderItem={(r) => <List.Item>· {r}</List.Item>}
                />
              </>
            )}

            <Divider style={{ margin: '12px 0' }} />
            <Paragraph strong style={{ marginBottom: 8 }}>全部职位（{marketReport.job_count}）</Paragraph>
            <List
              size="small"
              dataSource={(marketReport.jobs || []).slice((jobPage - 1) * jobPageSize, jobPage * jobPageSize)}
              renderItem={(job) => (
                <List.Item
                  key={job.job_id || `${job.title}-${job.company}`}
                  actions={[
                    <Button key="analyze" size="small" type="link" onClick={() => handlePickJob(job)}>
                      填入分析
                    </Button>,
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <Space size={8}>
                        <Text strong>{job.title}</Text>
                        {job.source && <Tag style={{ fontSize: 11 }}>{job.source}</Tag>}
                      </Space>
                    }
                    description={<Text strong>{job.company}</Text>}
                  />
                </List.Item>
              )}
            />
            <div style={{ textAlign: 'right', marginTop: 12 }}>
              <Pagination
                current={jobPage}
                pageSize={jobPageSize}
                total={marketReport.jobs?.length || 0}
                size="small"
                showSizeChanger
                pageSizeOptions={[10, 20, 50, 100]}
                showTotal={(total) => `共 ${total} 条`}
                onChange={(page, size) => {
                  setJobPage(page);
                  setJobPageSize(size);
                }}
              />
            </div>
          </div>
        ) : (
          <Empty description="点击「一键分析」生成市场分析报告" />
        )}
                </Card>
              </>
            ),
          },
          {
            key: 'single',
            label: <span><FileSearchOutlined /> 单职位分析</span>,
            children: (
              <>
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
            <Space style={{ marginBottom: 8 }}>
              <Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 0 }}>
                岗位定位：{result.job_analysis?.positioning || result.job_analysis?.position || '（未产出）'}
              </Paragraph>
              {(result as { cached?: boolean }).cached && (
                <Tag color="green" style={{ fontSize: 11 }}>14 天缓存</Tag>
              )}
            </Space>
            <Tabs
              activeKey={activeTab}
              onChange={(k) => setActiveTab(k as SectionKey)}
              items={tabItems}
            />
          </>
        )}
                </Card>
              </>
            ),
          },
          {
            key: 'boss',
            label: <span><QrcodeOutlined /> BOSS 登录</span>,
            children: (
              <>
                <Card
                  title={
                    <Space>
                      <QrcodeOutlined />
                      <Text strong>BOSS 直聘扫码登录</Text>
                    </Space>
                  }
                  style={{ maxWidth: 1080, margin: '0 auto' }}
                >
                  <Paragraph type="secondary">
                    登录 BOSS 直聘以获取登录 Cookie，用于后续采集 BOSS 上的职位（当前 BOSS 职位采集仍受反爬签名限制，登录作为备用）。
                  </Paragraph>
                  <Space>
                    <Button type="primary" icon={<QrcodeOutlined />} onClick={handleBossQrLogin}>
                      扫码登录
                    </Button>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      用手机 BOSS App「扫一扫」，走两步扫码（扫第一张 → 换第二张 → App 确认）。
                    </Text>
                  </Space>
                </Card>
              </>
            ),
          },
        ]}
      />

      {/* BOSS 扫码登录弹窗 */}
      <Modal
        title="BOSS 直聘扫码登录"
        open={qrOpen}
        onCancel={() => setQrOpen(false)}
        footer={null}
        width={360}
      >
        <div style={{ textAlign: 'center', padding: '12px 0' }}>
          {qrImageUrl ? (
            <>
              <img src={qrImageUrl} alt="BOSS 登录二维码" style={{ width: 220, height: 220 }} />
              <Paragraph type="secondary" style={{ marginTop: 12 }}>
                {qrTip || '用手机 BOSS App「扫一扫」此二维码'}
              </Paragraph>
            </>
          ) : (
            <Spin tip="正在生成二维码…" />
          )}
          {qrWaiting && qrImageUrl && (
            <Paragraph type="secondary" style={{ marginTop: 8 }}>
              <Spin size="small" /> 等待扫码确认中…（约 3 分钟超时）
            </Paragraph>
          )}
        </div>
      </Modal>
    </div>
  );
};

export default Job;
