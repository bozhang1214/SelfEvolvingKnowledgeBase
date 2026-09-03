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
