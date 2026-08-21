/**
 * file service 单元测试
 *
 * 覆盖 ISSUE-008 上传文件功能修复：
 * - TC-016-U：上传 URL 与后端路由对齐（POST /api/v1/upload）
 * - TC-017-U：响应解析为裸对象（不期望 ApiResponse 包装）
 * - TC-018-U：413 错误处理（"文件过大"提示）
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { uploadFile } from '@/services/file';

/**
 * Mock XMLHttpRequest 的最小实现。
 *
 * 真实的 XHR 实例有这些方法：
 *   open(method, url, async)
 *   setRequestHeader(k, v)
 *   send(body)
 *   abort()
 *
 * 和这些属性：
 *   status, responseText, timeout
 *   upload.onprogress
 *   onload, onerror, ontimeout
 */
class MockXHR {
  static instances: MockXHR[] = [];
  static lastInstance: MockXHR | null = null;

  method?: string;
  url?: string;
  headers: Record<string, string> = {};
  body?: any;
  timeout = 0;

  status = 200;
  responseText = '';

  upload = { onprogress: null as ((e: any) => void) | null };
  onload: ((e: any) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  ontimeout: ((e: any) => void) | null = null;

  constructor() {
    MockXHR.instances.push(this);
    MockXHR.lastInstance = this;
  }

  open(method: string, url: string, _async?: boolean): void {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(k: string, v: string): void {
    this.headers[k] = v;
  }
  send(body?: any): void {
    this.body = body;
    // 不自动触发 onload，让测试用 triggerResponse() 手动触发
  }
  abort(): void {
    // 简单 no-op
  }

  // 测试辅助方法
  triggerResponse(status: number, responseText: string): void {
    this.status = status;
    this.responseText = responseText;
    if (this.onload) {
      this.onload(new Event('load'));
    }
  }
}

// 替换全局 XMLHttpRequest
(globalThis as any).XMLHttpRequest = MockXHR;

describe('file service', () => {
  beforeEach(() => {
    MockXHR.instances = [];
    MockXHR.lastInstance = null;
    // localStorage setup
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation((key) => {
      if (key === 'sekb_token') return 'fake-token';
      return null;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('TC-016-U: 上传 URL 与后端路由对齐', () => {
    it('XHR.open 第二个参数应为 /api/v1/upload（不是 /files/upload）', () => {
      const file = new File(['hello'], 'test.txt', { type: 'text/plain' });
      uploadFile(file, () => {}, () => {}, () => {});

      const xhr = MockXHR.lastInstance!;
      expect(xhr.method).toBe('POST');
      expect(xhr.url).toContain('/api/v1/upload');
      // 确保不包含旧的 /files/upload
      expect(xhr.url).not.toContain('/files/upload');
    });

    it('应带 Authorization 头', () => {
      const file = new File(['hello'], 'test.txt', { type: 'text/plain' });
      uploadFile(file, () => {}, () => {}, () => {});

      const xhr = MockXHR.lastInstance!;
      expect(xhr.headers['Authorization']).toBe('Bearer fake-token');
    });

    it('应使用 multipart/form-data 而不手动设置 Content-Type', () => {
      const file = new File(['hello'], 'test.txt', { type: 'text/plain' });
      uploadFile(file, () => {}, () => {}, () => {});

      const xhr = MockXHR.lastInstance!;
      // 不应手动设置 Content-Type（浏览器自动加 multipart boundary）
      expect(xhr.headers['Content-Type']).toBeUndefined();
    });
  });

  describe('TC-017-U: 响应解析为裸对象', () => {
    it('成功响应应解析为 UploadResult 对象', () => {
      const file = new File(['hello'], 'test.txt', { type: 'text/plain' });
      const onDone = vi.fn();
      const onError = vi.fn();

      uploadFile(file, () => {}, onDone, onError);

      const xhr = MockXHR.lastInstance!;
      // 模拟后端返回裸 UploadResponse（不包 ApiResponse）
      const mockResult = {
        file_name: 'test.txt',
        file_size: 5,
        chunks_count: 3,
        ingested_count: 3,
        status: 'success',
        error: '',
        entry_ids: ['e1', 'e2', 'e3'],
      };
      xhr.triggerResponse(200, JSON.stringify(mockResult));

      expect(onDone).toHaveBeenCalledTimes(1);
      const result = onDone.mock.calls[0][0];
      expect(result.file_name).toBe('test.txt');
      expect(result.chunks_count).toBe(3);
      expect(result.ingested_count).toBe(3);
      expect(result.status).toBe('success');
      expect(result.entry_ids).toEqual(['e1', 'e2', 'e3']);
      expect(onError).not.toHaveBeenCalled();
    });

    it('响应不包含 file_name 时应触发 onError', () => {
      const file = new File(['hello'], 'test.txt', { type: 'text/plain' });
      const onDone = vi.fn();
      const onError = vi.fn();

      uploadFile(file, () => {}, onDone, onError);

      const xhr = MockXHR.lastInstance!;
      xhr.triggerResponse(200, JSON.stringify({ foo: 'bar' }));

      expect(onDone).not.toHaveBeenCalled();
      expect(onError).toHaveBeenCalledTimes(1);
      expect(onError.mock.calls[0][0]).toContain('响应数据格式');
    });

    it('响应不是 JSON 时应触发 onError', () => {
      const file = new File(['hello'], 'test.txt', { type: 'text/plain' });
      const onDone = vi.fn();
      const onError = vi.fn();

      uploadFile(file, () => {}, onDone, onError);

      const xhr = MockXHR.lastInstance!;
      xhr.triggerResponse(200, '<html>not json</html>');

      expect(onDone).not.toHaveBeenCalled();
      expect(onError).toHaveBeenCalledTimes(1);
      expect(onError.mock.calls[0][0]).toContain('解析响应');
    });
  });

  describe('TC-018-U: 413 错误处理', () => {
    it('413 应识别为"文件过大"提示', () => {
      const file = new File(['hello'], 'big.txt', { type: 'text/plain' });
      const onDone = vi.fn();
      const onError = vi.fn();

      uploadFile(file, () => {}, onDone, onError);

      const xhr = MockXHR.lastInstance!;
      xhr.triggerResponse(413, '<html>413 Request Entity Too Large</html>');

      expect(onDone).not.toHaveBeenCalled();
      expect(onError).toHaveBeenCalledTimes(1);
      const errorMsg = onError.mock.calls[0][0];
      expect(errorMsg).toMatch(/文件过大|50MB|413/);
    });

    it('HTTP 状态码应作为第二个参数传入 onError', () => {
      const file = new File(['hello'], 'test.txt', { type: 'text/plain' });
      const onDone = vi.fn();
      const onError = vi.fn();

      uploadFile(file, () => {}, onDone, onError);

      const xhr = MockXHR.lastInstance!;
      xhr.triggerResponse(413, '');

      expect(onError).toHaveBeenCalledTimes(1);
      expect(onError.mock.calls[0][1]).toBe(413);
    });

    it('404 应显示包含状态码的错误', () => {
      const file = new File(['hello'], 'test.txt', { type: 'text/plain' });
      const onDone = vi.fn();
      const onError = vi.fn();

      uploadFile(file, () => {}, onDone, onError);

      const xhr = MockXHR.lastInstance!;
      xhr.triggerResponse(404, '{"detail":"not found"}');

      expect(onError).toHaveBeenCalledTimes(1);
      expect(onError.mock.calls[0][1]).toBe(404);
      expect(onError.mock.calls[0][0]).toContain('404');
    });

    it('FastAPI HTTPException 的 detail 字段应被提取', () => {
      const file = new File(['hello'], 'test.txt', { type: 'text/plain' });
      const onDone = vi.fn();
      const onError = vi.fn();

      uploadFile(file, () => {}, onDone, onError);

      const xhr = MockXHR.lastInstance!;
      xhr.triggerResponse(503, '{"detail":"L3 知识库未启用"}');

      expect(onError).toHaveBeenCalledTimes(1);
      expect(onError.mock.calls[0][0]).toContain('L3 知识库未启用');
    });
  });
});
