/**
 * Vitest 全局 setup
 *
 * 在每个测试运行前调用，用于：
 * - Mock 浏览器 API（performance.now / sendBeacon / fetch）
 * - 清理 logger 全局状态
 */

// 引入 jest-dom 匹配器（toBeInTheDocument 等）
import '@testing-library/jest-dom/vitest';

// jsdom 没有 performance.now，Mock 一个稳定的实现
if (!('performance' in globalThis)) {
  Object.defineProperty(globalThis, 'performance', {
    value: {
      now: () => Date.now(),
    },
    configurable: true,
  });
}

// Mock navigator.sendBeacon（jsdom 默认不提供）
if (!('sendBeacon' in navigator)) {
  Object.defineProperty(navigator, 'sendBeacon', {
    value: () => true,
    configurable: true,
    writable: true,
  });
}

// 每个测试后清理 console 调用 spy
afterEach(() => {
  vi.restoreAllMocks();
});
