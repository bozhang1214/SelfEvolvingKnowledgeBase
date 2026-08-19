import apiClient from './api';
import type { ApiResponse } from '@/types/api';

export interface FileInfo {
  file_id: string;
  file_name: string;
  file_size: number;
  file_type: string;
  upload_time: string;
  parse_status: 'pending' | 'parsing' | 'done' | 'failed';
  user_id: string;
  knowledge_count: number;
}

export async function listFiles(): Promise<FileInfo[]> {
  const res = await apiClient.get<ApiResponse<FileInfo[]>>('/files');
  return res.data.data;
}

export async function deleteFile(fileId: string): Promise<void> {
  await apiClient.delete(`/files/${fileId}`);
}

// 上传文件（带进度）
export function uploadFile(
  file: File,
  onProgress: (percent: number) => void,
  onDone: (info: FileInfo) => void,
  onError: (error: string) => void
): XMLHttpRequest {
  const xhr = new XMLHttpRequest();
  const formData = new FormData();
  formData.append('file', file);

  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      onProgress(Math.round((e.loaded / e.total) * 100));
    }
  };

  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      const resp: ApiResponse<FileInfo> = JSON.parse(xhr.responseText);
      onDone(resp.data);
    } else {
      try {
        const resp = JSON.parse(xhr.responseText);
        onError(resp.message || '上传失败');
      } catch {
        onError('上传失败');
      }
    }
  };

  xhr.onerror = () => onError('网络错误');

  const token = localStorage.getItem('sekb_token');
  const uploadUrl = import.meta.env.PROD && import.meta.env.VITE_API_BASE
    ? `${import.meta.env.VITE_API_BASE}/api/v1/files/upload`
    : '/api/v1/files/upload';
  xhr.open('POST', uploadUrl);
  if (token) {
    xhr.setRequestHeader('Authorization', `Bearer ${token}`);
  }
  xhr.send(formData);

  return xhr;
}