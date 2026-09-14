import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Card, Input, Button, Tabs, Typography, Space, Spin, Empty, message, Row, Col, Tag, List, Modal, Divider, Alert, Select, Pagination, Table,
} from 'antd';
import {
  ThunderboltOutlined, ClearOutlined, FileSearchOutlined, SearchOutlined,
  BarChartOutlined, DeleteOutlined, ReloadOutlined, UploadOutlined, HistoryOutlined, DownloadOutlined,
  SendOutlined, PlusOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import {
  analyzeJob,
  fetchJobs,
  batchAnalyze,
  deleteBatchAnalysis,
  importJobFiles,
  listJobReports,
  getJobReport,
  deleteJobReport,
  refreshJob,
  saveJobCache,
  getLatestJobCache,
  getCachedBatchAnalysis,
  listApplyPlan,
  saveApplyPlan,
  deleteApplyPlan,
  type JobAnalyzeResult,
  type JobMeta,
  type FetchedJob,
  type MarketReport,
  type ArchivedReportMeta,
  type ApplyPlanItem,
  type ApplyPlanStats,
} from '@/services/job';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

/** 投递作战计划：分层标签（1=长期主攻 / 2=中期过渡 / 3=短期保底） */
const PLAN_TIER_LABELS: Record<number, { text: string; color: string }> = {
  1: { text: '① 主攻', color: 'red' },
  2: { text: '② 过渡', color: 'orange' },
  3: { text: '③ 保底', color: 'blue' },
};

/** 投递作战计划：状态标签 */
const PLAN_STATUS_LABELS: Record<string, { text: string; color: string }> = {
  planned: { text: '计划投', color: 'default' },
  applied: { text: '已投', color: 'processing' },
  interview: { text: '面试中', color: 'warning' },
  rejected: { text: '已挂', color: 'error' },
  offer: { text: 'Offer', color: 'success' },
};

import { SectionRenderer, MarketSection, KnowledgeSection, JobTitle, classifyRole, isEmptyValue, getMatchScore, matchScoreColor, SECTION_LABELS, SECTION_ORDER, CITY_OPTIONS, SALARY_OPTIONS, type SectionKey } from '@/features/job/render';

const Job: React.FC = () => {
  const [jdText, setJdText] = useState('');
  const [meta, setMeta] = useState<JobMeta>({});
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<JobAnalyzeResult | null>(null);
  const [activeTab, setActiveTab] = useState<SectionKey>('job_analysis');

  // 职位采集（多源）
  const [fetchKeyword, setFetchKeyword] = useState('');
  // 最近一次「实际采集」使用的关键词：用于默认回填与缓存同步，避免输入框被编辑后污染缓存
  const [lastFetchKeyword, setLastFetchKeyword] = useState('');
  const [fetchCity, setFetchCity] = useState('不限');
  const [fetchSalary, setFetchSalary] = useState('不限');
  const [fetching, setFetching] = useState(false);
  const [fetchedJobs, setFetchedJobs] = useState<FetchedJob[]>([]);
  // 复选框选中的职位（多选批量分析）
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  // 收集列表表格分页（受控）
  const [tablePage, setTablePage] = useState(1);
  const [tablePageSize, setTablePageSize] = useState(20);

  // 批量职位分析
  const [batchAnalyzing, setBatchAnalyzing] = useState(false);
  const [marketReport, setMarketReport] = useState<MarketReport | null>(null);
  // 分析模式：职位收集 / 批量分析 / 单职位分析 / 历史报告 / 投递计划
  const [analysisMode, setAnalysisMode] = useState<'collect' | 'batch' | 'single' | 'history' | 'plan'>('collect');

  // 批量上传职位文件
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 历史报告
  const [reports, setReports] = useState<ArchivedReportMeta[]>([]);
  const [reportsLoading, setReportsLoading] = useState(false);
  const [reportDetail, setReportDetail] = useState<{ id: string; type: string; title: string; created_at: string; markdown: string; report: Record<string, any> | null } | null>(null);
  // 历史报告子 tab：批量分析 / 单职位分析
  const [historyTab, setHistoryTab] = useState<'batch' | 'single'>('batch');
  // 最近一次缓存的批量分析报告（用于「批量分析」tab 默认展示）
  const [cachedMarketReport, setCachedMarketReport] = useState<MarketReport | null>(null);

  // 投递作战计划
  const [planItems, setPlanItems] = useState<ApplyPlanItem[]>([]);
  const [planStats, setPlanStats] = useState<ApplyPlanStats | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planModalOpen, setPlanModalOpen] = useState(false);
  const [planSaving, setPlanSaving] = useState(false);
  // 当前编辑的记录（null = 新增）
  const [planEditing, setPlanEditing] = useState<ApplyPlanItem | null>(null);
  const [planForm, setPlanForm] = useState<{
    company: string;
    title: string;
    tier: number;
    status: ApplyPlanItem['status'];
    applied_at: string;
    result_at: string;
    cooldown_months: number;
    url: string;
    note: string;
  }>({
    company: '', title: '', tier: 1, status: 'planned',
    applied_at: '', result_at: '', cooldown_months: 0, url: '', note: '',
  });

  // 挂载时：回填最近一次缓存的职位 + 筛选选项，并预取缓存的批量报告
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const cache = await getLatestJobCache();
        if (!alive || !cache.cached || !cache.jobs?.length) return;
        setFetchKeyword(cache.keyword || '');
        setLastFetchKeyword(cache.keyword || '');
        setFetchCity(cache.city || '不限');
        const salaryLabel = cache.min_salary_k > 0 && SALARY_OPTIONS.includes(`${cache.min_salary_k}K+`)
          ? `${cache.min_salary_k}K+`
          : '不限';
        setFetchSalary(salaryLabel);
        setFetchedJobs(cache.jobs);
      } catch {
        // 静默失败
      }
      try {
        const { report } = await getCachedBatchAnalysis();
        if (alive && report) setCachedMarketReport(report);
      } catch {
        // 静默失败
      }
    })();
    return () => { alive = false; };
  }, []);

  // 进入「批量分析」tab：仅当缓存报告的职位列表与当前采集列表严格一致时才默认展示
  // （保证职位列表信息与批量分析严格一致，避免两份信息错位）
  useEffect(() => {
    if (analysisMode !== 'batch' || marketReport || !cachedMarketReport) return;
    const rk = (j: FetchedJob) => j.job_id || `${j.title}-${j.company}`;
    const reportKeys = new Set((cachedMarketReport.jobs || []).map(rk));
    const fetchKeys = new Set(fetchedJobs.map(rk));
    if (reportKeys.size === 0) return;
    const same = reportKeys.size === fetchKeys.size && [...reportKeys].every((k) => fetchKeys.has(k));
    if (same) setMarketReport(cachedMarketReport);
  }, [analysisMode, cachedMarketReport, fetchedJobs, marketReport]);

  // 进入「历史报告」tab 时加载存档报告
  useEffect(() => {
    if (analysisMode === 'history') {
      loadReports();
    }
  }, [analysisMode]);

  // 进入「投递计划」tab 时加载投递记录
  useEffect(() => {
    if (analysisMode === 'plan') {
      loadApplyPlan();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisMode]);

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

  // ===== 投递作战计划 =====
  const loadApplyPlan = async () => {
    setPlanLoading(true);
    try {
      const { items, stats } = await listApplyPlan();
      setPlanItems(items);
      setPlanStats(stats);
    } catch {
      // 静默失败
    } finally {
      setPlanLoading(false);
    }
  };

  const openPlanModal = (item?: ApplyPlanItem) => {
    setPlanEditing(item || null);
    setPlanForm(
      item
        ? {
            company: item.company, title: item.title, tier: item.tier, status: item.status,
            applied_at: item.applied_at, result_at: item.result_at,
            cooldown_months: item.cooldown_months, url: item.url, note: item.note,
          }
        : {
            company: '', title: '', tier: 1, status: 'planned',
            applied_at: new Date().toISOString().slice(0, 10),
            result_at: '', cooldown_months: 6, url: '', note: '',
          },
    );
    setPlanModalOpen(true);
  };

  const handleSavePlan = async () => {
    if (!planForm.company.trim() && !planForm.title.trim()) {
      message.warning('请至少填写公司或岗位');
      return;
    }
    setPlanSaving(true);
    try {
      await saveApplyPlan({ ...(planEditing ? { id: planEditing.id } : {}), ...planForm });
      message.success(planEditing ? '已更新' : '已添加');
      setPlanModalOpen(false);
      loadApplyPlan();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setPlanSaving(false);
    }
  };

  const handleDeletePlan = async (id: string) => {
    try {
      await deleteApplyPlan(id);
      message.success('已删除');
      loadApplyPlan();
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
      // 带上本次采集的关键词/城市，避免后端回退到配置默认值（曾导致报告恒显示 Agent）
      const { report } = await batchAnalyze({
        jobs: targetJobs,
        force,
        keyword: lastFetchKeyword.trim() || fetchKeyword.trim() || undefined,
        city: fetchCity || undefined,
      });
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
      setCachedMarketReport(null);
      message.success('分析报告已删除，可重新分析');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败');
    }
  };

  const handleFetch = async () => {
    const kw = fetchKeyword.trim();
    if (!kw) {
      message.warning('请输入采集关键词');
      return;
    }
    setLastFetchKeyword(kw);
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
        keyword: lastFetchKeyword.trim() || fetchKeyword.trim() || '未指定',
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

  // 是否已全选全部职位（跨页全选：不受分页「每页最多 100 条」限制）
  const allSelected = fetchedJobs.length > 0 && selectedRowKeys.length >= fetchedJobs.length;

  /** 全选全部职位 / 取消全选（一键跨页全选，避免表头全选只能选当前页）。 */
  const handleToggleSelectAll = () => {
    if (allSelected) {
      setSelectedRowKeys([]);
    } else {
      setSelectedRowKeys(fetchedJobs.map(jobKey));
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

  const batchReports = useMemo(() => reports.filter((r) => r.type === 'batch'), [reports]);
  const singleReports = useMemo(() => reports.filter((r) => r.type === 'single'), [reports]);
  const historyReports = historyTab === 'batch' ? batchReports : singleReports;

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
            <SectionRenderer section={key} data={data as Record<string, any>} />
          ),
        };
      }),
    [result],
  );

  return (
    <div style={{ padding: 24, overflow: 'auto', background: '#fff', minHeight: '100%' }}>
      <Tabs
        activeKey={analysisMode}
        onChange={(k) => setAnalysisMode(k as 'collect' | 'batch' | 'single' | 'history' | 'plan')}
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
              <Space size={12}>
                <Text type="secondary" style={{ fontSize: 12 }}>共 {fetchedJobs.length} 条职位</Text>
                <Button size="small" type="link" onClick={handleToggleSelectAll}>
                  {allSelected ? `取消全选（已选 ${fetchedJobs.length} 条）` : `全选全部 ${fetchedJobs.length} 条`}
                </Button>
              </Space>
              <Button
                type="primary"
                size="small"
                icon={<BarChartOutlined />}
                disabled={selectedRowKeys.length < 1}
                loading={batchAnalyzing}
                onClick={() => {
                  const keys = selectedRowKeys.map(String);
                  const selected = fetchedJobs.filter((j) => keys.includes(j.job_id || `${j.title}-${j.company}`));
                  setAnalysisMode('batch');
                  handleBatchAnalyze(false, selected);
                }}
              >
                前往批量分析{selectedRowKeys.length > 0 ? `（${selectedRowKeys.length}）` : ''}
              </Button>
            </Space>
            <Table
              size="small"
              style={{ marginTop: 8 }}
              rowKey={(j) => j.job_id || `${j.title}-${j.company}`}
              dataSource={fetchedJobs.slice((tablePage - 1) * tablePageSize, tablePage * tablePageSize)}
              rowSelection={{
                selectedRowKeys,
                onChange: setSelectedRowKeys,
                // 跨页保留选中：否则翻页时 antd 会清掉不在当前页的选中项，
                // 导致「全选全部」或逐页勾选在翻页后丢失
                preserveSelectedRowKeys: true,
              }}
              pagination={false}
              columns={[
                {
                  title: '职位名',
                  dataIndex: 'title',
                  key: 'title',
                  render: (_, job) => (
                    <Space size={6}>
                      <JobTitle job={job} />
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
            <Text strong>批量职位分析</Text>
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

            {marketReport.market && !isEmptyValue(marketReport.market) && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <Paragraph strong style={{ marginBottom: 8 }}>市场行情</Paragraph>
                <MarketSection data={marketReport.market as Record<string, any>} />
              </>
            )}

            {marketReport.knowledge_iteration && !isEmptyValue(marketReport.knowledge_iteration) && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <Paragraph strong style={{ marginBottom: 8 }}>知识迭代</Paragraph>
                <KnowledgeSection data={marketReport.knowledge_iteration as Record<string, any>} />
              </>
            )}
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
                  <Tabs
                    size="small"
                    activeKey={historyTab}
                    onChange={(k) => setHistoryTab(k as 'batch' | 'single')}
                    items={[
                      { key: 'batch', label: `批量分析（${batchReports.length}）` },
                      { key: 'single', label: `单职位分析（${singleReports.length}）` },
                    ]}
                    style={{ marginBottom: 8 }}
                  />
                  <Spin spinning={reportsLoading}>
                    {historyReports.length === 0 ? (
                      <Empty description="暂无存档报告，做一次批量/单职位分析后会自动存档" />
                    ) : (
                      <List
                        size="small"
                        dataSource={historyReports}
                        renderItem={(r) => (
                          <List.Item
                            key={r.id}
                            actions={[
                              <Button key="view" size="small" type="link" onClick={() => openReport(r.id)}>查看</Button>,
                              <Button key="del" size="small" type="link" danger onClick={() => handleDeleteReportItem(r.id)}>删除</Button>,
                            ]}
                          >
                            <List.Item.Meta
                              title={<Text strong>{r.title}</Text>}
                              description={<Text type="secondary" style={{ fontSize: 12 }}>{r.created_at ? new Date(r.created_at).toLocaleString('zh-CN') : ''}</Text>}
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
          {
            key: 'plan',
            label: <span><SendOutlined /> 投递计划</span>,
            children: (
              <>
                <Card
                  title={
                    <Space>
                      <SendOutlined />
                      <Text strong>投递作战计划</Text>
                      <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
                        记录投递进度 + 冷却期提醒（大厂面试挂了通常要等 6-12 个月才能再投）
                      </Text>
                    </Space>
                  }
                  extra={
                    <Space>
                      <Button size="small" icon={<ReloadOutlined />} onClick={loadApplyPlan}>刷新</Button>
                      <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => openPlanModal()}>新增投递</Button>
                    </Space>
                  }
                  style={{ marginBottom: 16 }}
                >
                  {/* 进度统计 */}
                  <Row gutter={12} style={{ marginBottom: 16 }}>
                    {[
                      { label: '总投递', value: planStats?.total || 0, color: '#1677ff' },
                      { label: '计划投', value: planStats?.planned || 0, color: '#8c8c8c' },
                      { label: '已投/面试', value: (planStats?.applied || 0) + (planStats?.interview || 0), color: '#faad14' },
                      { label: '已挂', value: planStats?.rejected || 0, color: '#ff4d4f' },
                      { label: '冷却中', value: planStats?.cooling || 0, color: '#d4380d' },
                      { label: 'Offer', value: planStats?.offer || 0, color: '#52c41a' },
                    ].map((s) => (
                      <Col xs={8} sm={4} key={s.label}>
                        <div style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: '10px 0', textAlign: 'center' }}>
                          <div style={{ fontSize: 22, fontWeight: 700, color: s.color }}>{s.value}</div>
                          <Text type="secondary" style={{ fontSize: 12 }}>{s.label}</Text>
                        </div>
                      </Col>
                    ))}
                  </Row>

                  <Spin spinning={planLoading}>
                    {planItems.length === 0 ? (
                      <Empty description="还没有投递记录，点右上角「新增投递」开始记录" />
                    ) : (
                      <Table
                        size="small"
                        rowKey="id"
                        dataSource={planItems}
                        pagination={false}
                        columns={[
                          {
                            title: '公司', dataIndex: 'company', width: 130,
                            render: (v: string, r: ApplyPlanItem) => (
                              r.url ? <a href={r.url} target="_blank" rel="noreferrer"><Text strong>{v || '-'}</Text></a> : <Text strong>{v || '-'}</Text>
                            ),
                          },
                          { title: '岗位', dataIndex: 'title', ellipsis: true, render: (v: string) => v || '-' },
                          {
                            title: '分层', dataIndex: 'tier', width: 90,
                            render: (v: number) => <Tag color={PLAN_TIER_LABELS[v]?.color}>{PLAN_TIER_LABELS[v]?.text || v}</Tag>,
                          },
                          {
                            title: '状态', dataIndex: 'status', width: 90,
                            render: (v: string) => <Tag color={PLAN_STATUS_LABELS[v]?.color}>{PLAN_STATUS_LABELS[v]?.text || v}</Tag>,
                          },
                          { title: '投递日期', dataIndex: 'applied_at', width: 105, render: (v: string) => v || '-' },
                          {
                            title: '能否再投', width: 130,
                            render: (_: unknown, r: ApplyPlanItem) =>
                              r.cooling
                                ? <Tag color="red">冷却中 {r.days_left} 天</Tag>
                                : r.cooldown_until
                                  ? <Tag color="green">已解冻（{r.cooldown_until}）</Tag>
                                  : <Tag color="green">可投</Tag>,
                          },
                          {
                            title: '操作', width: 110,
                            render: (_: unknown, r: ApplyPlanItem) => (
                              <Space size={0}>
                                <Button size="small" type="link" onClick={() => openPlanModal(r)}>编辑</Button>
                                <Button size="small" type="link" danger onClick={() => handleDeletePlan(r.id)}>删除</Button>
                              </Space>
                            ),
                          },
                        ]}
                      />
                    )}
                  </Spin>
                </Card>

                {/* 新增 / 编辑投递记录 */}
                <Modal
                  title={planEditing ? '编辑投递记录' : '新增投递记录'}
                  open={planModalOpen}
                  onCancel={() => setPlanModalOpen(false)}
                  onOk={handleSavePlan}
                  confirmLoading={planSaving}
                  okText="保存"
                  width={560}
                >
                  <Space direction="vertical" size={12} style={{ width: '100%' }}>
                    <Row gutter={12}>
                      <Col span={12}>
                        <Text type="secondary" style={{ fontSize: 12 }}>公司</Text>
                        <Input value={planForm.company} onChange={(e) => setPlanForm({ ...planForm, company: e.target.value })} placeholder="如 字节跳动" />
                      </Col>
                      <Col span={12}>
                        <Text type="secondary" style={{ fontSize: 12 }}>岗位</Text>
                        <Input value={planForm.title} onChange={(e) => setPlanForm({ ...planForm, title: e.target.value })} placeholder="如 产品解决方案架构师" />
                      </Col>
                    </Row>
                    <Row gutter={12}>
                      <Col span={8}>
                        <Text type="secondary" style={{ fontSize: 12 }}>分层</Text>
                        <Select
                          style={{ width: '100%' }}
                          value={planForm.tier}
                          onChange={(v) => setPlanForm({ ...planForm, tier: v })}
                          options={[
                            { value: 1, label: '① 长期主攻' },
                            { value: 2, label: '② 中期过渡' },
                            { value: 3, label: '③ 短期保底' },
                          ]}
                        />
                      </Col>
                      <Col span={8}>
                        <Text type="secondary" style={{ fontSize: 12 }}>状态</Text>
                        <Select
                          style={{ width: '100%' }}
                          value={planForm.status}
                          onChange={(v) => setPlanForm({ ...planForm, status: v })}
                          options={[
                            { value: 'planned', label: '计划投' },
                            { value: 'applied', label: '已投' },
                            { value: 'interview', label: '面试中' },
                            { value: 'rejected', label: '已挂' },
                            { value: 'offer', label: 'Offer' },
                          ]}
                        />
                      </Col>
                      <Col span={8}>
                        <Text type="secondary" style={{ fontSize: 12 }}>冷却月数</Text>
                        <Select
                          style={{ width: '100%' }}
                          value={planForm.cooldown_months}
                          onChange={(v) => setPlanForm({ ...planForm, cooldown_months: v })}
                          options={[
                            { value: 0, label: '无' },
                            { value: 3, label: '3 个月' },
                            { value: 6, label: '6 个月' },
                            { value: 12, label: '12 个月' },
                          ]}
                        />
                      </Col>
                    </Row>
                    <Row gutter={12}>
                      <Col span={12}>
                        <Text type="secondary" style={{ fontSize: 12 }}>投递日期</Text>
                        <Input value={planForm.applied_at} onChange={(e) => setPlanForm({ ...planForm, applied_at: e.target.value })} placeholder="YYYY-MM-DD" />
                      </Col>
                      <Col span={12}>
                        <Text type="secondary" style={{ fontSize: 12 }}>出结果日期（挂了才填，用于算冷却）</Text>
                        <Input value={planForm.result_at} onChange={(e) => setPlanForm({ ...planForm, result_at: e.target.value })} placeholder="YYYY-MM-DD" />
                      </Col>
                    </Row>
                    <div>
                      <Text type="secondary" style={{ fontSize: 12 }}>链接</Text>
                      <Input value={planForm.url} onChange={(e) => setPlanForm({ ...planForm, url: e.target.value })} placeholder="职位链接（可选）" />
                    </div>
                    <div>
                      <Text type="secondary" style={{ fontSize: 12 }}>备注</Text>
                      <TextArea rows={2} value={planForm.note} onChange={(e) => setPlanForm({ ...planForm, note: e.target.value })} placeholder="如：内推人 XXX / 面试反馈（可选）" />
                    </div>
                    {planForm.status === 'rejected' && planForm.result_at && planForm.cooldown_months > 0 && (
                      <Alert type="warning" showIcon message={`冷却 ${planForm.cooldown_months} 个月，期间不要重复投递该公司（避免触发冷冻期）`} />
                    )}
                  </Space>
                </Modal>
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
            {reportDetail.report ? (
              reportDetail.type === 'batch' ? (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Row gutter={16}>
                    <Col xs={24} sm={8}>
                      <Paragraph strong style={{ marginBottom: 4 }}>公司分布</Paragraph>
                      <Space wrap size={[4, 4]}>
                        {(reportDetail.report.stats?.company_distribution || []).map((c: any) => (
                          <Tag key={c.name} color="blue">{c.name} × {c.count}</Tag>
                        ))}
                      </Space>
                    </Col>
                    <Col xs={24} sm={8}>
                      <Paragraph strong style={{ marginBottom: 4 }}>职位方向</Paragraph>
                      <Space wrap size={[4, 4]}>
                        {(reportDetail.report.stats?.role_distribution || []).map((c: any) => (
                          <Tag key={c.name} color="geekblue">{c.name} × {c.count}</Tag>
                        ))}
                      </Space>
                    </Col>
                    <Col xs={24} sm={8}>
                      <Paragraph strong style={{ marginBottom: 4 }}>热点关键词</Paragraph>
                      <Space wrap size={[4, 4]}>
                        {(reportDetail.report.stats?.hot_keywords || []).map((c: any) => (
                          <Tag key={c.keyword} color="purple">{c.keyword} × {c.count}</Tag>
                        ))}
                      </Space>
                    </Col>
                  </Row>
                  {reportDetail.report.market && !isEmptyValue(reportDetail.report.market) && (
                    <>
                      <Divider style={{ margin: 0 }} />
                      <Paragraph strong style={{ marginBottom: 0 }}>市场行情</Paragraph>
                      <MarketSection data={reportDetail.report.market as Record<string, any>} />
                    </>
                  )}
                  {reportDetail.report.knowledge_iteration && !isEmptyValue(reportDetail.report.knowledge_iteration) && (
                    <>
                      <Divider style={{ margin: 0 }} />
                      <Paragraph strong style={{ marginBottom: 0 }}>知识迭代</Paragraph>
                      <KnowledgeSection data={reportDetail.report.knowledge_iteration as Record<string, any>} />
                    </>
                  )}
                </Space>
              ) : (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  {SECTION_ORDER.map((key) => {
                    const data = reportDetail.report?.[key];
                    if (isEmptyValue(data)) return null;
                    return (
                      <div key={key}>
                        <Divider style={{ margin: '4px 0' }} />
                        <Paragraph strong style={{ marginBottom: 8 }}>{SECTION_LABELS[key]}</Paragraph>
                        <SectionRenderer section={key} data={data as Record<string, any>} />
                      </div>
                    );
                  })}
                </Space>
              )
            ) : (
              <ReactMarkdown>{reportDetail.markdown}</ReactMarkdown>
            )}
          </div>
        ) : null}
      </Modal>
    </div>
  );
};

export default Job;
