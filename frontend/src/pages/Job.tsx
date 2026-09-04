import React, { useMemo, useRef, useState } from 'react';
import {
  Card, Input, Button, Tabs, Typography, Space, Spin, Empty, message, Row, Col, Tag, List, Modal, Divider, Alert, Select, Pagination, Popover, Table,
} from 'antd';
import {
  ThunderboltOutlined, ClearOutlined, FileSearchOutlined, SearchOutlined, QrcodeOutlined,
  BarChartOutlined, DeleteOutlined, ReloadOutlined, UploadOutlined, HistoryOutlined, DownloadOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import {
  analyzeJob,
  fetchJobs,
  bossQrStart,
  bossQrStatus,
  batchAnalyze,
  deleteBatchAnalysis,
  importJobFiles,
  listJobReports,
  getJobReport,
  deleteJobReport,
  refreshJob,
  saveJobCache,
  type JobAnalyzeResult,
  type JobMeta,
  type FetchedJob,
  type MarketReport,
  type ArchivedReportMeta,
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

// 职位类型分类（按标题关键词）
const ROLE_RULES: Array<[string, string[]]> = [
  ['评测/质量', ['评测', '评估', 'Evaluation', '测试']],
  ['安全', ['安全']],
  ['算法/模型', ['算法', 'NLP', '大模型', 'LLM', '模型', 'AIOps']],
  ['架构师/Leader', ['架构师', 'Tech Lead', '技术负责人', 'Leader', '架构研发']],
  ['产品经理', ['产品经理', '产品', 'PM', '策略']],
  ['运营/策略', ['运营', '数据策略', '数据']],
  ['研发/工程', ['后端', '引擎', '研发工程师', '开发工程师', 'Harness', 'Infra', '基础设施', '编排', 'Orchestration', '应用']],
];

function classifyRole(title: string): string {
  const t = (title || '').toLowerCase();
  for (const [role, kws] of ROLE_RULES) {
    if (kws.some((k) => t.includes(k.toLowerCase()))) return role;
  }
  return '其他';
}

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

/** 职位详情悬浮弹窗（悬停展示标题/公司/薪资/城市 + JD 全文）。 */const JobDetailPopover: React.FC<{ job: FetchedJob; children: React.ReactNode }> = ({ job, children }) => (
  <Popover
    title={
      <Space direction="vertical" size={0}>
        <Text strong>{job.title || '（无标题）'}</Text>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {[job.company, job.salary, job.city, job.source].filter(Boolean).join(' · ')}
        </Text>
      </Space>
    }
    content={
      <div style={{ maxWidth: 460, maxHeight: 320, overflow: 'auto' }}>
        {job.jd_text ? (
          <Text style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{job.jd_text}</Text>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>该职位暂无 JD 详情</Text>
        )}
      </div>
    }
    trigger="hover"
    placement="right"
  >
    <span>{children}</span>
  </Popover>
);

/** 从分析结果里提取匹配度评分（0~100），无则返回 null。 */function getMatchScore(result: JobAnalyzeResult | null): number | null {
  const ranking = (result as { job_strategy?: { match_ranking?: Array<{ match_score?: number }> } })?.job_strategy?.match_ranking;
  if (Array.isArray(ranking) && ranking.length > 0 && typeof ranking[0]?.match_score === 'number') {
    return ranking[0].match_score;
  }
  return null;
}

function matchScoreColor(score: number): string {
  if (score >= 80) return 'green';
  if (score >= 60) return 'blue';
  return 'orange';
}

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
  // 复选框选中的职位（多选批量分析）
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  // 批量报告职位列表分页（受控，修复「N 条/页」不生效）
  const [jobPage, setJobPage] = useState(1);
  const [jobPageSize, setJobPageSize] = useState(20);
  // 收集列表表格分页（受控）
  const [tablePage, setTablePage] = useState(1);
  const [tablePageSize, setTablePageSize] = useState(20);

  // BOSS 扫码登录
  const [qrOpen, setQrOpen] = useState(false);
  const [qrImageUrl, setQrImageUrl] = useState('');
  const [qrWaiting, setQrWaiting] = useState(false);
  const [qrTip, setQrTip] = useState('');

  // 批量市场分析
  const [batchAnalyzing, setBatchAnalyzing] = useState(false);
  const [marketReport, setMarketReport] = useState<MarketReport | null>(null);
  // 分析模式：职位收集 / 批量分析 / 单职位分析 / BOSS 登录 / 历史报告
  const [analysisMode, setAnalysisMode] = useState<'collect' | 'batch' | 'single' | 'boss' | 'history'>('collect');

  // 批量上传职位文件
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 历史报告
  const [reports, setReports] = useState<ArchivedReportMeta[]>([]);
  const [reportsLoading, setReportsLoading] = useState(false);
  const [reportDetail, setReportDetail] = useState<{ id: string; type: string; title: string; created_at: string; markdown: string } | null>(null);

  const handleImportClick = () => fileInputRef.current?.click();

  const handleImportFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;
    setImporting(true);
    try {
      const { jobs } = await importJobFiles(files);
      setFetchedJobs((prev) => [...jobs, ...prev]);
      message.success(`已导入 ${jobs.length} 个职位，可在下方列表查看`);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '导入职位文件失败');
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const loadReports = async () => {
    setReportsLoading(true);
    try {
      const { reports: list } = await listJobReports();
      setReports(list);
    } catch {
      // 静默失败
    } finally {
      setReportsLoading(false);
    }
  };

  const openReport = async (id: string) => {
    try {
      const detail = await getJobReport(id);
      setReportDetail(detail);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '读取报告失败');
    }
  };

  const handleDeleteReportItem = async (id: string) => {
    try {
      await deleteJobReport(id);
      message.success('报告已删除');
      loadReports();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败');
    }
  };

  const handleBatchAnalyze = async (force = false, jobs?: FetchedJob[]) => {
    const targetJobs = jobs ?? fetchedJobs;
    if (targetJobs.length === 0) {
      message.warning('请先在「职位收集」tab 收集职位');
      return;
    }
    setBatchAnalyzing(true);
    try {
      const { report } = await batchAnalyze({ jobs: targetJobs, force });
      setMarketReport(report);
      message.success('批量分析完成');
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
    setJdText(job.jd_text || '');
    setMeta({ company: job.company, position: job.title, city: job.city, salary: job.salary });
    setAnalysisMode('single');
    if (job.jd_text && job.jd_text.trim()) {
      message.success('已填入职位描述，请点击「开始分析」');
    } else {
      message.info('该职位暂无 JD 文本，请粘贴 JD 后点击「开始分析」');
    }
  };

  /** 职位唯一 key（job_id 缺失时用标题+公司兜底）。 */
  const jobKey = (j: FetchedJob) => j.job_id || `${j.title}-${j.company}`;

  /** 把当前列表同步回缓存（删除/刷新后调用）。 */
  const syncJobCache = async (jobs: FetchedJob[]) => {
    const salaryK = fetchSalary === '不限' ? 0 : parseInt(fetchSalary, 10) || 0;
    try {
      await saveJobCache({
        keyword: fetchKeyword.trim() || 'Agent',
        city: fetchCity === '不限' ? '' : fetchCity,
        min_salary_k: salaryK,
        jobs,
      });
    } catch {
      // 缓存同步失败不阻断主流程
    }
  };

  /** 刷新单个职位的 JD（重新抓详情页）并同步缓存。 */
  const handleRefreshJob = async (job: FetchedJob) => {
    try {
      const { jd_text } = await refreshJob(job.job_url || '', job.source || '');
      const key = jobKey(job);
      const newJobs = fetchedJobs.map((j) => (jobKey(j) === key ? { ...j, jd_text: jd_text || j.jd_text } : j));
      setFetchedJobs(newJobs);
      await syncJobCache(newJobs);
      message.success(jd_text ? '已刷新职位 JD' : '未获取到 JD（该职位可能已下线或非猎聘源）');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '刷新失败');
    }
  };

  /** 删除单个职位并同步缓存。 */
  const handleDeleteJob = async (job: FetchedJob) => {
    const key = jobKey(job);
    const newJobs = fetchedJobs.filter((j) => jobKey(j) !== key);
    setFetchedJobs(newJobs);
    setSelectedRowKeys((prev) => prev.filter((k) => String(k) !== key));
    await syncJobCache(newJobs);
    message.success('已删除该职位');
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
      <Tabs
        activeKey={analysisMode}
        onChange={(k) => setAnalysisMode(k as 'collect' | 'batch' | 'single' | 'boss' | 'history')}
        style={{ maxWidth: 1080, margin: '0 auto' }}
        items={[
          {
            key: 'collect',
            label: <span><SearchOutlined /> 职位收集</span>,
            children: (
              <>
                <Card
                  title={
                    <Space>
                      <SearchOutlined />
                      <Text strong>职位收集</Text>
                      <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
                        设置筛选条件，收集并浏览真实职位（9 家免登录渠道）
                      </Text>
                    </Space>
                  }
                  extra={
                    <Button
                      size="small"
                      type="link"
                      icon={<BarChartOutlined />}
                      onClick={() => {
                        setAnalysisMode('batch');
                        handleBatchAnalyze(false);
                      }}
                    >
                      去批量分析 →
                    </Button>
                  }
                  style={{ marginBottom: 16 }}
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
                收集职位
              </Button>
          </Col>
        </Row>
        <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
          说明：工作地/薪资默认「不限」；关键字支持按空格拼接多个关键词（如「Agent 大模型」），空则用默认「Agent」。
        </Paragraph>
        <Space style={{ marginTop: 12 }} align="center">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".txt,.md,.markdown,.pdf,.docx"
            style={{ display: 'none' }}
            onChange={handleImportFiles}
          />
          <Button icon={<UploadOutlined />} loading={importing} onClick={handleImportClick}>
            批量上传职位文件
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            支持 .txt/.md/.docx/.pdf，每个文件一个职位（文件名作标题、正文作 JD），导入后补充到下方列表（不覆盖）
          </Text>
        </Space>
        {fetchedJobs.length > 0 && (
          <>
            <Space style={{ marginTop: 12, width: '100%', justifyContent: 'space-between' }}>
              <Text type="secondary" style={{ fontSize: 12 }}>共 {fetchedJobs.length} 条职位</Text>
              {selectedRowKeys.length > 0 && (
                <Button
                  type="primary"
                  size="small"
                  icon={<BarChartOutlined />}
                  loading={batchAnalyzing}
                  onClick={() => {
                    const keys = selectedRowKeys.map(String);
                    const selected = fetchedJobs.filter((j) => keys.includes(j.job_id || `${j.title}-${j.company}`));
                    setAnalysisMode('batch');
                    handleBatchAnalyze(false, selected);
                  }}
                >
                  批量分析选中（{selectedRowKeys.length}）
                </Button>
              )}
            </Space>
            <Table
              size="small"
              style={{ marginTop: 8 }}
              rowKey={(j) => j.job_id || `${j.title}-${j.company}`}
              dataSource={fetchedJobs.slice((tablePage - 1) * tablePageSize, tablePage * tablePageSize)}
              rowSelection={{
                selectedRowKeys,
                onChange: setSelectedRowKeys,
              }}
              pagination={false}
              columns={[
                {
                  title: '职位名',
                  dataIndex: 'title',
                  key: 'title',
                  render: (_, job) => (
                    <Space size={6}>
                      <JobDetailPopover job={job}><Text strong>{job.title}</Text></JobDetailPopover>
                      {job.salary && <Tag color="blue" style={{ fontSize: 11 }}>{job.salary}</Tag>}
                    </Space>
                  ),
                },
                {
                  title: '职位类型',
                  key: 'role',
                  width: 120,
                  render: (_, job) => <Tag style={{ fontSize: 11 }}>{classifyRole(job.title)}</Tag>,
                  filters: [...new Set(fetchedJobs.map((j) => classifyRole(j.title)))].map((r) => ({ text: r, value: r })),
                  onFilter: (value, job) => classifyRole(job.title) === value,
                },
                {
                  title: '数据来源',
                  dataIndex: 'source',
                  key: 'source',
                  width: 100,
                  render: (s) => <Tag style={{ fontSize: 11 }}>{s || '-'}</Tag>,
                  filters: [...new Set(fetchedJobs.map((j) => j.source || '未知'))].map((s) => ({ text: s, value: s })),
                  onFilter: (value, job) => (job.source || '未知') === value,
                },
                {
                  title: '公司',
                  dataIndex: 'company',
                  key: 'company',
                  render: (c) => <Text strong>{c || '-'}</Text>,
                  filters: [...new Set(fetchedJobs.map((j) => j.company || '未知'))].map((c) => ({ text: c, value: c })),
                  onFilter: (value, job) => (job.company || '未知') === value,
                  filterSearch: true,
                },
                {
                  title: '操作',
                  key: 'action',
                  width: 180,
                  render: (_, job) => (
                    <Space size={0}>
                      <Button size="small" type="link" onClick={() => handlePickJob(job)}>填入分析</Button>
                      <Button size="small" type="link" icon={<ReloadOutlined />} onClick={() => handleRefreshJob(job)}>刷新</Button>
                      <Button size="small" type="link" danger icon={<DeleteOutlined />} onClick={() => handleDeleteJob(job)}>删除</Button>
                    </Space>
                  ),
                },
              ]}
            />
            <div style={{ textAlign: 'right', marginTop: 12 }}>
              <Pagination
                current={tablePage}
                pageSize={tablePageSize}
                total={fetchedJobs.length}
                size="small"
                showSizeChanger
                pageSizeOptions={[10, 20, 50, 100]}
                showTotal={(t) => `共 ${t} 条`}
                onChange={(page, size) => {
                  setTablePage(page);
                  setTablePageSize(size);
                }}
              />
            </div>
          </>
        )}
                </Card>
              </>
            ),
          },
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
              对「职位收集」里已收集的职位（多个）做整体市场分析
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
                        <JobDetailPopover job={job}><Text strong>{job.title}</Text></JobDetailPopover>
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
                      <Text strong>单职位分析</Text>
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
            <Space style={{ marginBottom: 8 }} align="center">
              {getMatchScore(result) !== null && (
                <Tag color={matchScoreColor(getMatchScore(result) as number)} style={{ fontSize: 13, padding: '2px 12px' }}>
                  匹配度 {getMatchScore(result)} 分
                </Tag>
              )}
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
          {
            key: 'history',
            label: <span><HistoryOutlined /> 历史报告</span>,
            children: (
              <>
                <Card
                  title={
                    <Space>
                      <HistoryOutlined />
                      <Text strong>历史报告</Text>
                      <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
                        批量分析 / 单职位分析的结果自动存档，可随时回顾
                      </Text>
                    </Space>
                  }
                  extra={<Button size="small" icon={<ReloadOutlined />} onClick={loadReports}>刷新</Button>}
                  style={{ marginBottom: 16 }}
                >
                  <Spin spinning={reportsLoading}>
                    {reports.length === 0 ? (
                      <Empty description="暂无存档报告，做一次批量/单职位分析后会自动存档" />
                    ) : (
                      <List
                        size="small"
                        dataSource={reports}
                        renderItem={(r) => (
                          <List.Item
                            key={r.id}
                            actions={[
                              <Button key="view" size="small" type="link" onClick={() => openReport(r.id)}>查看</Button>,
                              <Button key="del" size="small" type="link" danger onClick={() => handleDeleteReportItem(r.id)}>删除</Button>,
                            ]}
                          >
                            <List.Item.Meta
                              title={
                                <Space size={8}>
                                  <Tag color={r.type === 'batch' ? 'geekblue' : 'purple'} style={{ fontSize: 11 }}>
                                    {r.type === 'batch' ? '批量' : '单职位'}
                                  </Tag>
                                  <Text strong>{r.title}</Text>
                                </Space>
                              }
                              description={<Text type="secondary" style={{ fontSize: 12 }}>{r.created_at?.replace('T', ' ').slice(0, 19)}</Text>}
                            />
                          </List.Item>
                        )}
                      />
                    )}
                  </Spin>
                </Card>
              </>
            ),
          },
        ]}
      />

      {/* 历史报告详情弹窗 */}
      <Modal
        title={reportDetail?.title || '报告详情'}
        open={!!reportDetail}
        onCancel={() => setReportDetail(null)}
        footer={
          reportDetail ? (
            <Button
              icon={<DownloadOutlined />}
              onClick={() => {
                const blob = new Blob([reportDetail.markdown], { type: 'text/markdown;charset=utf-8' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${reportDetail.title || '报告'}.md`;
                a.click();
                URL.revokeObjectURL(url);
              }}
            >
              下载 Markdown
            </Button>
          ) : null
        }
        width={860}
      >
        {reportDetail ? (
          <div style={{ maxHeight: '70vh', overflow: 'auto' }}>
            <ReactMarkdown>{reportDetail.markdown}</ReactMarkdown>
          </div>
        ) : null}
      </Modal>

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
