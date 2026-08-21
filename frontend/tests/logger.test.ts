/**
 * logger 单元测试
 *
 * 覆盖：
 * - TC-007-U：logger.info / warn / error 输出格式
 * - TC-008-U：logger.perf 耗时计算
 * - TC-009-U：maskEmail / maskToken 脱敏
 * - TC-013-U：VITE_LOG_REPORT_ENABLED 关闭后不上报
 * - TC-014-U：连续失败退避（5 次失败后 60s 暂停）
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { logger, maskEmail, maskToken } from '@/utils/logger';

describe('logger', () => {
  describe('TC-007-U: 日志级别路由与格式', () => {
    it('info 调用 console.log', () => {
      const spy = vi.spyOn(console, 'log').mockImplementation(() => {});
      logger.info('test_event', { foo: 'bar' });
      expect(spy).toHaveBeenCalledTimes(1);
      // 第一个参数应包含 [INFO] 和事件名
      expect(spy.mock.calls[0][0]).toContain('[INFO]');
      expect(spy.mock.calls[0][0]).toContain('test_event');
      // 第三个参数应包含字段
      const payload = spy.mock.calls[0][2];
      expect(payload).toMatchObject({ foo: 'bar' });
      expect(payload).toHaveProperty('ts');
      expect(payload).toHaveProperty('level', 'info');
      expect(payload).toHaveProperty('event', 'test_event');
    });

    it('warn 调用 console.warn', () => {
      const spy = vi.spyOn(console, 'warn').mockImplementation(() => {});
      logger.warn('warn_event');
      expect(spy).toHaveBeenCalledTimes(1);
      expect(spy.mock.calls[0][0]).toContain('[WARN]');
      expect(spy.mock.calls[0][0]).toContain('warn_event');
    });

    it('error 调用 console.error', () => {
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
      logger.error('error_event', { code: 500 });
      expect(spy).toHaveBeenCalledTimes(1);
      expect(spy.mock.calls[0][0]).toContain('[ERROR]');
      const payload = spy.mock.calls[0][2];
      expect(payload).toHaveProperty('code', 500);
    });
  });

  describe('TC-008-U: logger.perf 耗时计算', () => {
    it('done 回调应在 perf 事件中包含 duration_ms', () => {
      const spy = vi.spyOn(console, 'log').mockImplementation(() => {});
      // Mock performance.now 第一次返回 1000，第二次返回 1500
      const perfSpy = vi
        .spyOn(performance, 'now')
        .mockReturnValueOnce(1000)
        .mockReturnValueOnce(1500);

      const done = logger.perf('api_request', { url: '/auth/login' });
      done({ status: 200 });

      expect(spy).toHaveBeenCalledTimes(1);
      const payload = spy.mock.calls[0][2];
      expect(payload).toHaveProperty('url', '/auth/login');
      expect(payload).toHaveProperty('status', 200);
      expect(payload).toHaveProperty('duration_ms', 500);
    });
  });

  describe('TC-009-U: maskEmail / maskToken 脱敏', () => {
    it('maskEmail 保留前 3 位 + ***', () => {
      expect(maskEmail('abcdef@example.com')).toBe('abc***');
      // 长度 > 3 但前 3 位含 @ 时仍按 slice(0,3) 处理
      expect(maskEmail('ab@example.com')).toBe('ab@***');
      // 长度 <= 3 时全脱敏
      expect(maskEmail('ab')).toBe('***');
      expect(maskEmail('')).toBe('');
      expect(maskEmail(null as any)).toBe('');
    });

    it('maskToken 保留前 8 位 + ...', () => {
      expect(maskToken('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig'))
        .toBe('eyJhbGci...');
      expect(maskToken('short')).toBe('short...');
      expect(maskToken('')).toBe('');
    });
  });

  describe('TC-013-U: VITE_LOG_REPORT_ENABLED 关闭后不上报', () => {
    it('关闭后不应发起 sendBeacon / fetch', async () => {
      // 通过 vi.stubEnv 模拟环境变量
      vi.stubEnv('VITE_LOG_REPORT_ENABLED', 'false');

      // 重新 import 模块以使新环境变量生效
      vi.resetModules();
      const { logger: freshLogger } = await import('@/utils/logger');

      const sendBeaconSpy = vi.spyOn(navigator, 'sendBeacon');
      const fetchSpy = vi.spyOn(globalThis, 'fetch');

      freshLogger.warn('test_event_should_not_report');
      // 手动触发 flush
      await freshLogger.flush();

      expect(sendBeaconSpy).not.toHaveBeenCalled();
      expect(fetchSpy).not.toHaveBeenCalled();

      vi.unstubAllEnvs();
    });
  });

  describe('TC-014-U: 连续失败退避', () => {
    it('5 次连续失败后暂停上报，60s 后探针恢复', async () => {
      vi.useFakeTimers();

      // 让 sendBeacon 失败 + fetch reject
      vi.spyOn(navigator, 'sendBeacon').mockReturnValue(false);
      vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network'));

      // 重新加载模块以拿到全新内部状态
      vi.resetModules();
      const { logger: freshLogger } = await import('@/utils/logger');

      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

      // 触发 5 次 flush（每次都失败）
      for (let i = 0; i < 5; i++) {
        freshLogger.error('test_fail_event');
        await freshLogger.flush();
      }

      // 第 5 次失败后应输出"暂停上报"提示
      const pauseLogCalled = warnSpy.mock.calls.some((call) =>
        String(call[0]).includes('暂停上报')
      );
      expect(pauseLogCalled).toBe(true);

      // 第 6 次 flush 应该被暂停，不应再发起 fetch
      // 清空之前的调用记录（保留 mock 拒绝行为），便于断言"无新增调用"
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network'));
      fetchSpy.mockClear();
      freshLogger.error('test_paused_event');
      await freshLogger.flush();
      expect(fetchSpy).not.toHaveBeenCalled();

      // 推进 60 秒后，暂停期结束
      vi.advanceTimersByTime(61_000);

      // 探针恢复：下次 flush 应再次发起请求
      fetchSpy.mockResolvedValue(new Response('', { status: 200 }));
      freshLogger.error('test_recovery_event');
      await freshLogger.flush();
      expect(fetchSpy).toHaveBeenCalled();

      vi.useRealTimers();
    });
  });
});
