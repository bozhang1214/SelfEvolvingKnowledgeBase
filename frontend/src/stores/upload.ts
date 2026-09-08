import { create } from 'zustand';
import { uploadFilePromise, type UploadResult } from '@/services/file';
import { logger } from '@/utils/logger';

/** 单个文件的上传状态 */
export interface FileTask {
  uid: string;
  file: File;
  progress: number; // 0-100
  status: 'pending' | 'uploading' | 'done' | 'error';
  result?: UploadResult;
  error?: string;
}

// 未完成上传的「文件名列表」持久化（轻量：只记文件名，不存文件内容；
// File 对象刷新即失，恢复时让用户重新选择这些文件再上传）
const PENDING_KEY = 'sekb_upload_pending';

/**
 * 文件在知识库中的唯一标识：优先用相对路径（文件夹上传时浏览器提供 webkitRelativePath，
 * 与后端入库的 file_name 一致），单个文件上传则退回 basename。
 */
export function fileKey(file: File): string {
  return file.webkitRelativePath || file.name;
}

export function readPendingFiles(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(PENDING_KEY) || '[]');
    return Array.isArray(raw) ? raw.filter((x) => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

export function clearPendingFiles(): void {
  localStorage.removeItem(PENDING_KEY);
}

function writePendingFiles(names: string[]): void {
  const uniq = Array.from(new Set(names.filter(Boolean)));
  if (uniq.length === 0) {
    localStorage.removeItem(PENDING_KEY);
  } else {
    localStorage.setItem(PENDING_KEY, JSON.stringify(uniq));
  }
}

interface UploadState {
  tasks: FileTask[];
  uploading: boolean;
  _abort: boolean;
  /** 串行上传一批文件；返回成功/失败计数。上传循环跑在 store 里，组件卸载（切导航）不中断。 */
  startUpload: (
    files: File[],
    opts: { overwrite: boolean; existingNames: Set<string> }
  ) => Promise<{ success: number; error: number; total: number }>;
  cancel: () => void;
  clearTasks: () => void;
}

/**
 * 上传任务全局 store。
 *
 * 把上传循环从 Files 组件抽到模块级 store，使得切换左侧导航（组件卸载/重挂）时，
 * 上传循环与 XHR 不随组件销毁而中断；组件重挂后直接从 store 读回进度。
 */
export const useUploadStore = create<UploadState>((set, get) => ({
  tasks: [],
  uploading: false,
  _abort: false,

  startUpload: async (files, { overwrite, existingNames }) => {
    if (files.length === 0) {
      return { success: 0, error: 0, total: 0 };
    }
    const newTasks: FileTask[] = files.map((file, idx) => ({
      uid: `${Date.now()}_${idx}`,
      file,
      progress: 0,
      status: 'pending',
    }));

    // 记录未完成文件（刷新恢复用）：成功上传后才从列表移除；用相对路径标识（与后端 file_name 对齐）
    const pendingNames = new Set(files.map((f) => fileKey(f)));
    writePendingFiles([...pendingNames]);

    set({ tasks: newTasks, uploading: true, _abort: false });
    logger.info('batch_upload_start', {
      total_files: files.length,
      total_size: files.reduce((s, f) => s + f.size, 0),
    });

    let successCount = 0;
    let errorCount = 0;

    for (const task of newTasks) {
      if (get()._abort) {
        set((s) => ({
          tasks: s.tasks.map((t) =>
            t.status === 'pending' || t.status === 'uploading'
              ? { ...t, status: 'error', error: '已取消' }
              : t
          ),
        }));
        break;
      }

      set((s) => ({
        tasks: s.tasks.map((t) => (t.uid === task.uid ? { ...t, status: 'uploading' } : t)),
      }));

      try {
        const isDup = existingNames.has(fileKey(task.file));
        const { promise } = uploadFilePromise(
          task.file,
          (pct) => {
            set((s) => ({
              tasks: s.tasks.map((t) => (t.uid === task.uid ? { ...t, progress: pct } : t)),
            }));
          },
          overwrite && isDup
        );
        const result = await promise;

        set((s) => ({
          tasks: s.tasks.map((t) =>
            t.uid === task.uid ? { ...t, status: 'done', progress: 100, result } : t
          ),
        }));

        if (result.status === 'success' || result.status === 'partial') {
          successCount++;
          // 成功上传后从「未完成」列表移除
          pendingNames.delete(fileKey(task.file));
          writePendingFiles([...pendingNames]);
        }
      } catch (err: any) {
        errorCount++;
        const errMsg = err?.message || '上传失败';
        set((s) => ({
          tasks: s.tasks.map((t) =>
            t.uid === task.uid ? { ...t, status: 'error', error: errMsg } : t
          ),
        }));
        logger.warn('batch_upload_file_failed', {
          file_name: task.file.name,
          error: errMsg,
          http_status: err?.httpStatus,
        });
      }
    }

    set({ uploading: false, _abort: false });
    logger.info('batch_upload_done', {
      total: newTasks.length,
      success: successCount,
      error: errorCount,
    });
    return { success: successCount, error: errorCount, total: newTasks.length };
  },

  cancel: () => {
    set({ _abort: true });
  },

  clearTasks: () => {
    set({ tasks: [] });
  },
}));
