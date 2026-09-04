/**
 * 前端统一日志工具 + Prometheus 指标上报。
 *
 * 设计目标：
 * 1. 关键路径埋点：登录/注册/对话/API 调用/错误等，便于线上排查
 * 2. 结构化输出：所有日志带时间戳、模块标签、事件名、KV 字段
 * 3. 性能统计：perf() 测量关键操作耗时
 * 4. 控制台可见：开发环境输出到 console（生产环境也可输出，便于浏览器排查）
 * 5. 后端聚合：warn/error/perf 事件批量上报到后端
 *    /api/v1/monitoring/client-event，转写为 Prometheus 指标
 *
 * 使用方式：
 *   import { logger } from '@/utils/logger';
 *   logger.info('login_submit', { email: 'a***' });
 *   logger.error('login_failed', { status: 401, msg: '...' });
 *   const done = logger.perf('api_request', { url: '/auth/login' });
 *   done({ status: 200 }); // 自动记录耗时
 */

type LogLevel = 'debug' | 'info' | 'warn' | 'error';

interface LogFields {
  [key: string]: unknown;
}

const LEVEL_PRIORITY: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
};

// 通过环境变量控制日志级别：VITE_LOG_LEVEL=error 只看错误
const ENV_LEVEL = (import.meta.env.VITE_LOG_LEVEL as LogLevel) || 'info';
const CURRENT_LEVEL = LEVEL_PRIORITY[ENV_LEVEL] ?? LEVEL_PRIORITY.info;

// 颜色映射（控制台输出更易读）
const LEVEL_COLOR: Record<LogLevel, string> = {
  debug: '#909399',
  info: '#409eff',
  warn: '#e6a23c',
  error: '#f56c6c',
};

function formatTimestamp(): string {
  return new Date().toISOString();
}

/**
 * 脱敏邮箱：仅保留前 3 个字符 + ***
 */
export function maskEmail(email: string): string {
  if (!email || typeof email !== 'string') return '';
  if (email.length <= 3) return '***';
  return email.slice(0, 3) + '***';
}

/**
 * 脱敏 token：截断显示前 8 个字符
 */
export function maskToken(token: string): string {
  if (!token) return '';
  return token.slice(0, 8) + '...';
}

// ============================================================
// 事件队列 + 后端上报
// ============================================================

interface QueuedEvent {
  ts: string;
  level: LogLevel;
  event: string;
  fields?: LogFields;
}

// 只上报 warn/error + perf（info/debug 仅本地 console，避免日志量过大）
const REPORT_LEVELS: Set<LogLevel> = new Set(['warn', 'error']);
// perf 事件虽然以 info 级别记录，但用于性能统计，需要上报
// 在 emit() 内部单独处理

const MAX_QUEUE_SIZE = 20;        // 队列满立即 flush
const FLUSH_INTERVAL_MS = 5000;   // 定时 flush 周期
// 上报端点：生产部署在 /sekb/ 子路径，开发走 Vite 代理 /api
const _API_BASE = import.meta.env.PROD
  ? `${import.meta.env.VITE_API_BASE || '/sekb'}/api/v1`
  : '/api/v1';
const REPORT_ENDPOINT = `${_API_BASE}/monitoring/client-event`;

// 上报开关与采样率（通过环境变量配置）
// VITE_LOG_REPORT_ENABLED=false 完全关闭上报（仅本地 console）
// VITE_LOG_REPORT_RATE=0.1 采样 10% 事件上报（高流量场景降本）
const REPORT_ENABLED =
  (import.meta.env.VITE_LOG_REPORT_ENABLED ?? 'true') !== 'false';
const REPORT_RATE = Math.min(
  1,
  Math.max(0, Number(import.meta.env.VITE_LOG_REPORT_RATE ?? '1') || 1)
);

const _eventQueue: QueuedEvent[] = [];
let _flushTimer: ReturnType<typeof setInterval> | null = null;
let _isFlushing = false;   // 防止并发 flush

// 连续失败退避状态
// 5 次连续失败后暂停上报 60s，60s 后探针恢复
const REPORT_FAIL_THRESHOLD = 5;
const REPORT_PAUSE_MS = 60_000;
let _consecutiveFailures = 0;
let _pausedUntil = 0;  // 暂停截止时间戳（0 表示未暂停）

function isReportingPaused(): boolean {
  if (_pausedUntil === 0) return false;
  if (Date.now() >= _pausedUntil) {
    // 暂停期结束，自动清除
    _pausedUntil = 0;
    return false;
  }
  return true;
}

function enqueueForReport(level: LogLevel, event: string, fields?: LogFields): void {
  // 1. 全局开关
  if (!REPORT_ENABLED) return;

  // 2. 暂停期不上报（perf 事件也不上报，避免无用流量）
  if (isReportingPaused()) return;

  // 3. perf 事件特殊处理：以 info 级别记录但需要上报
  const isPerf = event === 'api_request';
  if (!isPerf && !REPORT_LEVELS.has(level)) return;

  // 4. 采样：error 级别始终上报（避免错过关键错误），其他按采样率
  if (level !== 'error' && REPORT_RATE < 1) {
    if (Math.random() > REPORT_RATE) return;
  }

  _eventQueue.push({ ts: formatTimestamp(), level, event, fields });

  // 队列满立即 flush
  if (_eventQueue.length >= MAX_QUEUE_SIZE) {
    void flushQueue();
  }
}

async function flushQueue(): Promise<void> {
  if (_isFlushing || _eventQueue.length === 0) return;

  // 暂停期内不做实际 flush，但仍消费队列避免内存堆积
  // （关键错误本地 console 已可见，丢失上报可接受）
  if (isReportingPaused()) {
    _eventQueue.splice(0, _eventQueue.length);
    return;
  }

  _isFlushing = true;

  // 取出当前队列快照
  const batch = _eventQueue.splice(0, _eventQueue.length);

  try {
    // 优先用 sendBeacon（异步、不阻塞页面）但不支持时降级 fetch
    // 注意：sendBeacon 只支持 POST + blob，且数据量受限（一般 64KB）
    const payload = JSON.stringify({ events: batch });
    const blob = new Blob([payload], { type: 'application/json' });

    let ok = false;
    if (navigator.sendBeacon) {
      ok = navigator.sendBeacon(REPORT_ENDPOINT, blob);
    }
    if (!ok) {
      // sendBeacon 失败或不支持，降级到 fetch keepalive
      const resp = await fetch(REPORT_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true,
      });
      // 2xx 视为成功
      ok = resp.ok;
    }

    if (ok) {
      // 成功：重置失败计数
      _consecutiveFailures = 0;
    } else {
      // HTTP 4xx/5xx 失败：计入退避
      handleReportFailure();
    }
  } catch {
    // 网络异常失败：计入退避
    handleReportFailure();
  } finally {
    _isFlushing = false;
  }
}

function handleReportFailure(): void {
  _consecutiveFailures += 1;
  if (_consecutiveFailures >= REPORT_FAIL_THRESHOLD) {
    // 触发退避：暂停 60s
    _pausedUntil = Date.now() + REPORT_PAUSE_MS;
    // 控制台输出一次告警，便于排查
    // 不通过 emit() 输出避免又触发上报
    console.warn(
      `[LOGGER] 上报连续失败 ${_consecutiveFailures} 次，暂停上报 ${REPORT_PAUSE_MS / 1000}s`
    );
  }
}

// 启动定时 flush
function startFlushTimer(): void {
  if (_flushTimer !== null) return;  // 已启动
  if (typeof window === 'undefined') return;  // SSR 防护

  _flushTimer = setInterval(() => {
    void flushQueue();
  }, FLUSH_INTERVAL_MS);

  // 页面关闭/隐藏时尽力 flush 残余事件
  // visibilitychange 比 beforeunload 更可靠（移动端也会触发）
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      void flushQueue();
    }
  });
}

// 模块加载即启动定时 flush
if (typeof window !== 'undefined') {
  startFlushTimer();
}

function emit(level: LogLevel, event: string, fields: LogFields = {}): void {
  if (LEVEL_PRIORITY[level] < CURRENT_LEVEL) return;

  const ts = formatTimestamp();
  const payload = { ts, level, event, ...fields };

  // 控制台输出：使用样式让日志更醒目
  const style = `color:${LEVEL_COLOR[level]};font-weight:bold;`;
  const prefix = `%c[${level.toUpperCase()}] ${event}`;
  // info/debug 用 console.log，warn 用 console.warn，error 用 console.error
  const consoleFn =
    level === 'error' ? console.error : level === 'warn' ? console.warn : console.log;

  consoleFn(prefix, style, payload);

  // 入队上报
  enqueueForReport(level, event, fields);
}

class Logger {
  debug(event: string, fields?: LogFields): void {
    emit('debug', event, fields);
  }

  info(event: string, fields?: LogFields): void {
    emit('info', event, fields);
  }

  warn(event: string, fields?: LogFields): void {
    emit('warn', event, fields);
  }

  error(event: string, fields?: LogFields): void {
    emit('error', event, fields);
  }

  /**
   * 性能埋点：返回一个 done 回调，调用时自动计算耗时并记录。
   *
   * 用法：
   *   const done = logger.perf('api_request', { url: '/auth/login' });
   *   try { ... } finally { done({ status: 200 }); }
   */
  perf(event: string, startFields?: LogFields): (endFields?: LogFields) => void {
    const start = performance.now();
    return (endFields?: LogFields) => {
      const durationMs = Math.round(performance.now() - start);
      // perf 事件用 info 级别记录，但 enqueueForReport 会特殊处理为上报
      emit('info', event, { ...startFields, ...endFields, duration_ms: durationMs });
    };
  }

  /**
   * 手动触发 flush（用于关键场景立即上报，如登出后离开页面）。
   */
  flush(): Promise<void> {
    return flushQueue();
  }
}

export const logger = new Logger();
