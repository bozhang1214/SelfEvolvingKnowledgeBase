/**
 * 剪贴板与文件下载工具。
 *
 * 复制优先使用异步 Clipboard API（需安全上下文），
 * 失败或不可用时降级到 document.execCommand('copy')。
 */

/** 复制文本到剪贴板，返回是否成功。 */
export async function copyText(text: string): Promise<boolean> {
  if (
    typeof navigator !== 'undefined'
    && navigator.clipboard
    && typeof window !== 'undefined'
    && window.isSecureContext
  ) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 降级到 execCommand
    }
  }

  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.top = '-9999px';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

/** 触发浏览器下载文本文件（Blob + a[download]）。 */
export function downloadTextFile(
  filename: string,
  content: string,
  mimeType = 'text/markdown;charset=utf-8',
): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // 延迟释放，确保下载已触发
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
