import apiClient from './api';
import { logger } from '@/utils/logger';

/**
 * 后端 UploadResponse 数据模型（与 backend/app/api/routes/upload.py 对齐）。
 * 接口：POST /api/v1/upload
 *
 * 注意：后端直接返回 UploadResponse 对象（不包 ApiResponse），
 * 响应体就是 { file_name, file_size, chunks_count, ... }，
 * 没有 { code, data, message } 包装层。
 */
export interface UploadResult {
  file_name: string;
  file_size: number;
  chunks_count: number;
  ingested_count: number;
  status: 'success' | 'partial' | 'error';
  error: string;
  entry_ids: string[];
}

/**
 * 后端 KnowledgeBaseStatus 数据模型。
 * 接口：GET /api/v1/upload/status
 * 后端直接返回裸对象，不包 ApiResponse。
 */
export interface KnowledgeStatus {
  total_entries: number;
  l3_enabled: boolean;
  user_id: string;
}

/**
 * 上传文件（带进度回调）。
 *
 * 使用 XMLHttpRequest 以便拿到 upload.onprogress 进度事件。
 * 与后端 POST /api/v1/upload 对接，返回 UploadResult。
 *
 * 用法：
 *   const xhr = uploadFile(file, onProgress, onDone, onError);
 *   // 如需取消：xhr.abort();
 */
export function uploadFile(
  file: File,
  onProgress: (percent: number) => void,
  onDone: (info: UploadResult) => void,
  onError: (error: string, httpStatus: number) => void
): XMLHttpRequest {
  const xhr = new XMLHttpRequest();
  const formData = new FormData();
  formData.append('file', file);

  // 上传进度
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      onProgress(Math.round((e.loaded / e.total) * 100));
    }
  };

  // 上传完成（无论 2xx 还是 4xx/5xx）
  xhr.onload = () => {
    const httpStatus = xhr.status;
    logger.info('upload_response', {
      file_name: file.name,
      file_size: file.size,
      http_status: httpStatus,
    });

    if (httpStatus >= 200 && httpStatus < 300) {
      try {
        // 后端直接返回 UploadResult 对象，不包 ApiResponse
        const result: UploadResult = JSON.parse(xhr.responseText);
        if (result && result.file_name) {
          onDone(result);
        } else {
          onError('响应数据格式不正确', httpStatus);
        }
      } catch (err) {
        logger.error('upload_parse_response_failed', {
          file_name: file.name,
          http_status: httpStatus,
          raw: xhr.responseText.slice(0, 200),
        });
        onError('解析响应失败', httpStatus);
      }
    } else {
      // 失败：尝试从响应体提取后端错误消息
      let serverMsg = `上传失败（HTTP ${httpStatus}）`;
      try {
        const resp = JSON.parse(xhr.responseText);
        // FastAPI HTTPException 返回 {"detail": "..."} 格式
        // 提取 detail/message 后附加 HTTP 状态码，方便用户排查
        if (resp.detail) {
          serverMsg = `${resp.detail} (HTTP ${httpStatus})`;
        } else if (resp.message) {
          serverMsg = `${resp.message} (HTTP ${httpStatus})`;
        }
      } catch {
        // 非 JSON 响应（如 nginx 413 返回 HTML 错误页）
        if (httpStatus === 413) {
          serverMsg = '文件过大（被 Nginx 拦截），上限 50MB';
        } else if (httpStatus === 0) {
          serverMsg = '网络错误或跨域被拦截';
        } else if (httpStatus === 503) {
          serverMsg = 'L3 知识库未启用，无法上传 (HTTP 503)';
        }
      }
      logger.warn('upload_failed', {
        file_name: file.name,
        http_status: httpStatus,
        server_msg: serverMsg,
        raw: xhr.responseText.slice(0, 200),
      });
      onError(serverMsg, httpStatus);
    }
  };

  // 网络层错误（连不上服务器）
  xhr.onerror = () => {
    logger.error('upload_network_error', {
      file_name: file.name,
      file_size: file.size,
    });
    onError('网络错误，请检查网络或后端服务', 0);
  };

  xhr.ontimeout = () => {
    logger.error('upload_timeout', { file_name: file.name });
    onError('上传超时', 0);
  };

  // 构造 URL（与 api.ts 的 baseURL 对齐逻辑）
  const uploadUrl =
    import.meta.env.PROD && import.meta.env.VITE_API_BASE
      ? `${import.meta.env.VITE_API_BASE}/api/v1/upload`
      : '/api/v1/upload';

  xhr.open('POST', uploadUrl);
  xhr.timeout = 120_000;  // 2 分钟超时，大文件保护
  const token = localStorage.getItem('sekb_token');
  if (token) {
    xhr.setRequestHeader('Authorization', `Bearer ${token}`);
  }
  // 注意：不要手动设置 Content-Type，浏览器会自动设置 multipart boundary
  xhr.send(formData);

  return xhr;
}

/**
 * 查询知识库状态（不是文件列表，后端无文件列表接口）。
 * 返回知识库总条目数 + L3 是否启用。
 */
export async function getKnowledgeStatus(): Promise<KnowledgeStatus> {
  // 后端直接返回 KnowledgeBaseStatus 对象，不包 ApiResponse
  const res = await apiClient.get<KnowledgeStatus>('/upload/status');
  return res.data;
}

/**
 * 删除知识条目（按 entry_id，不是按 file_id）。
 * 后端接口：DELETE /api/v1/upload/entries/{entry_id}
 */
export async function deleteEntry(entryId: string): Promise<void> {
  await apiClient.delete(`/upload/entries/${entryId}`);
}
