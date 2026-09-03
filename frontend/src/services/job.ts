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

/** 分析单个职位 JD，返回聚合的结构化结果。 */
export async function analyzeJob(payload: JobAnalyzeRequest): Promise<JobAnalyzeResult> {
  const res = await apiClient.post<JobAnalyzeResult>('/job/analyze', payload);
  logger.info('job_analyze_done', {
    has_job_analysis: !!(res.data as JobAnalyzeResult)?.job_analysis,
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

/** 从猎聘采集真实职位列表。 */
export async function fetchJobs(payload: {
  keyword: string;
  city?: string;
  page?: number;
  limit?: number;
}): Promise<JobFetchResult> {
  const res = await apiClient.post<JobFetchResult>('/job/fetch', payload);
  logger.info('job_fetch_done', { keyword: payload.keyword, count: res.data.count });
  return res.data;
}

/** BOSS 扫码登录：启动，返回二维码图片 URL + qr_id。 */
export async function bossQrStart(): Promise<{ qr_id: string; qr_image_url: string }> {
  const res = await apiClient.post('/job/boss/qr/start');
  return res.data;
}

/** BOSS 扫码登录：等待扫码确认，返回登录结果。 */
export async function bossQrComplete(qr_id: string, timeoutSeconds = 180): Promise<{ ok: boolean; cookie_header?: string; reason?: string }> {
  const res = await apiClient.post('/job/boss/qr/complete', { qr_id, timeout_seconds: timeoutSeconds });
  return res.data;
}
