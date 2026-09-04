import apiClient from './api';
import { logger } from '@/utils/logger';

/**
 * 招聘分析 API（与 backend/app/api/routes/job.py 对齐）。
 * 后端直接返回聚合对象，不包 ApiResponse，直接用 res.data 取业务数据。
 */

export interface JobMeta {
  company?: string;
  position?: string;
  salary?: string;
  city?: string;
  [key: string]: unknown;
}

/** 02 深度分析结果（字段为宽松类型，因为 LLM 输出结构可能略有出入）。 */
export interface JobAnalysis {
  position?: string;
  company?: string;
  positioning?: string;
  responsibilities?: string[];
  knowledge_points?: string[];
  hard_requirements?: string[];
  interview_questions?: string[];
  conclusion?: string;
  [key: string]: unknown;
}

export type JsonMap = Record<string, unknown>;

/** analyze_job 返回的聚合结果。 */
export interface JobAnalyzeResult {
  job_analysis: JobAnalysis;
  knowledge_priority: JsonMap;
  interview_qa: JsonMap;
  gap_analysis: JsonMap;
  resume_advice: JsonMap;
  learning_plan: JsonMap;
  project_iteration: JsonMap;
  job_strategy: JsonMap;
}

export interface JobAnalyzeRequest {
  jd_text: string;
  job_meta?: JobMeta;
}

/** 分析单个职位 JD，返回聚合的结构化结果（14 天缓存）。 */
export async function analyzeJob(payload: JobAnalyzeRequest): Promise<JobAnalyzeResult & { cached?: boolean }> {
  const res = await apiClient.post<JobAnalyzeResult & { cached?: boolean }>('/job/analyze', payload, {
    timeout: 180000, // 单职位分析要串行 8 步 LLM，超过默认 30s
  });
  logger.info('job_analyze_done', {
    has_job_analysis: !!(res.data as JobAnalyzeResult)?.job_analysis,
    cached: !!(res.data as { cached?: boolean })?.cached,
  });
  return res.data;
}

/** 采集到的单个职位。 */
export interface FetchedJob {
  job_id: string;
  title: string;
  company: string;
  salary: string;
  city: string;
  job_url: string;
  jd_text: string;
  source?: string;
}

export interface JobFetchResult {
  keyword: string;
  city?: string;
  min_salary_k?: number;
  source_count?: number;
  sources?: Record<string, { raw: number; count: number }>;
  count: number;
  jobs: FetchedJob[];
}

/** 从多源采集真实职位列表。 */
export async function fetchJobs(payload: {
  keyword: string;
  city?: string;
  min_salary_k?: number;
  page?: number;
  limit?: number;
}): Promise<JobFetchResult> {
  const res = await apiClient.post<JobFetchResult>('/job/fetch', payload);
  logger.info('job_fetch_done', { keyword: payload.keyword, count: res.data.count });
  return res.data;
}

/** BOSS 扫码登录：启动，返回第一张二维码（data URL）+ qr_id。 */
export async function bossQrStart(): Promise<{ qr_id: string; qr_image_url: string; phase: string }> {
  const res = await apiClient.post('/job/boss/qr/start');
  return res.data;
}

/** BOSS 扫码状态（含第二张码 / 登录结果）。 */
export interface BossQrStatus {
  phase: 'waiting_scan' | 'waiting_second_scan' | 'waiting_confirm' | 'success' | 'expired' | 'login_failed' | string;
  qr_image_url?: string;
  ok?: boolean;
  cookie_header?: string;
  message?: string;
}

/** 轮询 BOSS 扫码状态机。 */
export async function bossQrStatus(qr_id: string): Promise<BossQrStatus> {
  const res = await apiClient.post('/job/boss/qr/status', { qr_id });
  return res.data;
}

/** 批量市场分析报告。 */
export interface MarketReport {
  analyzed_at: string;
  keyword: string;
  city: string;
  job_count: number;
  stats: {
    company_distribution: Array<{ name: string; count: number }>;
    role_distribution: Array<{ name: string; count: number }>;
    hot_keywords: Array<{ keyword: string; count: number }>;
  };
  overview: string;
  trends: string[];
  opportunities: Array<{ title: string; company: string; reason: string }>;
  recommendations: string[];
  jobs: FetchedJob[];
}

/** 一键批量分析采集结果（7 天缓存；传入 jobs 则分析这批职位，不重复采集）。 */
export async function batchAnalyze(payload: {
  keyword?: string;
  city?: string;
  force?: boolean;
  jobs?: FetchedJob[];
}): Promise<{ cached: boolean; report: MarketReport }> {
  const res = await apiClient.post('/job/batch-analyze', payload, { timeout: 120000 });
  return res.data;
}

/** 删除批量分析缓存（强制下次重新分析）。 */
export async function deleteBatchAnalysis(): Promise<{ deleted: boolean }> {
  const res = await apiClient.delete('/job/batch-analyze');
  return res.data;
}

/** 批量导入职位文件（每个文件一个职位），返回解析后的职位列表。 */
export async function importJobFiles(files: File[]): Promise<{ jobs: FetchedJob[]; count: number }> {
  const fd = new FormData();
  files.forEach((f) => fd.append('files', f));
  const res = await apiClient.post('/job/import', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  });
  return res.data;
}

/** 历史存档报告元信息。 */
export interface ArchivedReportMeta {
  id: string;
  type: 'batch' | 'single';
  title: string;
  created_at: string;
}

/** 列出历史存档报告。 */
export async function listJobReports(): Promise<{ reports: ArchivedReportMeta[] }> {
  const res = await apiClient.get('/job/reports');
  return res.data;
}

/** 读取一份历史存档报告（含正文）。 */
export async function getJobReport(reportId: string): Promise<{ id: string; type: string; title: string; created_at: string; report: unknown }> {
  const res = await apiClient.get(`/job/reports/${reportId}`);
  return res.data;
}

/** 删除一份历史存档报告。 */
export async function deleteJobReport(reportId: string): Promise<{ deleted: boolean }> {
  const res = await apiClient.delete(`/job/reports/${reportId}`);
  return res.data;
}
