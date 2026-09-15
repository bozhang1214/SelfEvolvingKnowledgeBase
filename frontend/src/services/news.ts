import apiClient from './api';
import { logger } from '@/utils/logger';

/**
 * 科技资讯 API（与 backend/app/api/routes/news.py 对齐）。
 * 后端直接返回裸对象，不包 ApiResponse，直接用 res.data 取业务数据。
 */

export interface NewsReportMeta {
  date: string;
  total_count: number;
  headline: string;
  path: string;
  created_at: string;
}

export interface NewsReport extends NewsReportMeta {
  markdown?: string;
}

export interface NewsRefreshResult {
  date: string;
  fetched: number;
  filtered: number;
  path: string;
}

/** 列出历史日报（元信息）。 */
export async function listReports(): Promise<NewsReportMeta[]> {
  const res = await apiClient.get<{ reports: NewsReportMeta[] }>('/news/reports');
  return res.data.reports || [];
}

/** 读取指定日期的日报（含 markdown 正文）。 */
export async function getReport(date: string): Promise<NewsReport> {
  const res = await apiClient.get<NewsReport>('/news/report', { params: { date } });
  return res.data;
}

/** 手动触发一次日报生成（force=true 重新生成，忽略当日缓存）。 */
export async function refreshNews(force = true): Promise<NewsRefreshResult> {
  const res = await apiClient.post<NewsRefreshResult>('/news/refresh', { force });
  logger.info('news_refresh_triggered', { force });
  return res.data;
}

// ============ 周报 / 月报 ============

export type PeriodicType = 'weekly' | 'monthly';

export interface PeriodicReportMeta {
  type: PeriodicType;
  period: string;
  path: string;
}

export interface PeriodicReport extends PeriodicReportMeta {
  markdown?: string;
}

/** 列出周报/月报。 */
export async function listPeriodic(type: PeriodicType): Promise<PeriodicReportMeta[]> {
  const res = await apiClient.get<{ reports: PeriodicReportMeta[] }>(`/news/${type}`);
  return res.data.reports || [];
}

/** 读取某期周报/月报。 */
export async function getPeriodic(type: PeriodicType, period: string): Promise<PeriodicReport> {
  const res = await apiClient.get<PeriodicReport>(`/news/${type}`, { params: { period } });
  return res.data;
}

/** 生成周报/月报（缺省上一周期）。 */
export async function generatePeriodic(type: PeriodicType): Promise<{ type: string; period: string }> {
  const res = await apiClient.post(`/news/${type}`);
  return res.data;
}


/** 定时任务（日报/周报/月报）最近一次的执行状态。 */
export interface NewsTaskStatus {
  kind: 'daily' | 'weekly' | 'monthly';
  ok: boolean;
  period: string;
  error: string;
  started_at: string;
  finished_at: string;
  duration_s: number;
}

/**
 * 读取最近一次定时任务的执行状态。
 *
 * 为什么要它：定时任务失败以前只在服务端日志里留一行，用户看不到，
 * 只能靠「咦今天怎么没日报」发现。界面据此显示失败原因，并让「提交后轮询」有依据。
 */
export async function getNewsStatus(): Promise<NewsTaskStatus | null> {
  const res = await apiClient.get<{ status: NewsTaskStatus | null }>('/news/status');
  return res.data.status;
}
