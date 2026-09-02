import React, { useEffect, useState, useRef } from 'react';
import { Upload, Button, Typography, message, Progress, Space, Tag, Alert, Card, Statistic, Row, Col } from 'antd';
import { InboxOutlined, FileOutlined, FileImageOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons';
import {
  uploadFilePromise,
  getKnowledgeStatus,
  type UploadResult,
  type KnowledgeStatus,
} from '@/services/file';
import { logger } from '@/utils/logger';

// 支持的文件扩展名（与后端 FileProcessor 对齐）
const SUPPORTED_EXTENSIONS = [
  '.pdf', '.docx', '.txt', '.md',
  '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif',
];
const IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'];

const { Title, Text } = Typography;
const { Dragger } = Upload;

interface UploadHistoryItem {
  uid: string;
  result: UploadResult;
  uploaded_at: string;
}

/** 单个文件的上传状态 */
interface FileTask {
  uid: string;
  file: File;
  progress: number;       // 0-100
  status: 'pending' | 'uploading' | 'done' | 'error';
  result?: UploadResult;
  error?: string;
}

/** 从 antd Upload 的 file 对象或原生 File 中提取支持的文件 */
function filterSupportedFiles(files: File[]): File[] {
  return files.filter((f) => {
    const ext = f.name.substring(f.name.lastIndexOf('.')).toLowerCase();
    return SUPPORTED_EXTENSIONS.includes(ext);
  });
}

/** 递归展平 DataTransferItemList（支持文件夹拖拽） */
async function flattenItems(items: DataTransferItemList): Promise<File[]> {
  const result: File[] = [];
  const queue: DataTransferItem[] = Array.from(items);
  while (queue.length > 0) {
    const item = queue.shift()!;
    if (item.kind !== 'file') continue;
    const entry = item.webkitGetAsEntry?.();
    if (entry) {
      // 通过 entry 递归读取目录
      const files = await readEntry(entry, '');
      result.push(...files);
    } else {
      const file = item.getAsFile();
      if (file) result.push(file);
    }
  }
  return result;
}

/** 递归读取 FileSystemEntry，返回所有文件 */
function readEntry(entry: FileSystemEntry, path: string): Promise<File[]> {
  return new Promise((resolve) => {
    if (entry.isFile) {
      (entry as FileSystemFileEntry).file((file) => {
        // 保留相对路径信息到 name（webkitRelativePath 风格）
        Object.defineProperty(file, 'webkitRelativePath', { value: path + file.name });
        resolve([file]);
      }, () => resolve([]));
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      const allFiles: File[] = [];
      const readBatch = () => {
        reader.readEntries(async (entries) => {
          if (entries.length === 0) {
            resolve(allFiles);
            return;
          }
          for (const e of entries) {
            const files = await readEntry(e, path + entry.name + '/');
            allFiles.push(...files);
          }
          readBatch();  // 继续读下一批（readEntries 一次最多返回 100 条）
        }, () => resolve(allFiles));
      };
      readBatch();
    } else {
      resolve([]);
    }
  });
}

const Files: React.FC = () => {
  const [knowledgeStatus, setKnowledgeStatus] = useState<KnowledgeStatus | null>(null);
  const [tasks, setTasks] = useState<FileTask[]>([]);
  const [uploading, setUploading] = useState(false);
  const [history, setHistory] = useState<UploadHistoryItem[]>([]);
  const [statusLoading, setStatusLoading] = useState(false);
  const abortRef = useRef(false);

  const loadStatus = async () => {
    setStatusLoading(true);
    try {
      const data = await getKnowledgeStatus();
      setKnowledgeStatus(data);
    } catch (err: any) {
      logger.warn('load_status_failed', { msg: err?.message });
    } finally {
      setStatusLoading(false);
    }
  };

  useEffect(() => {
    loadStatus();
  }, []);

  /** 批量上传入口：处理 antd Upload 选择的文件列表 */
  const handleBatchUpload = async (fileList: File[]) => {
    const supported = filterSupportedFiles(fileList);
    if (supported.length === 0) {
      message.warning('未选择支持的文件类型');
      return false;
    }

    // 初始化任务列表
    const newTasks: FileTask[] = supported.map((file, idx) => ({
      uid: `${Date.now()}_${idx}`,
      file,
      progress: 0,
      status: 'pending',
    }));

    setTasks(newTasks);
    setUploading(true);
    abortRef.current = false;

    logger.info('batch_upload_start', {
      total_files: supported.length,
      total_size: supported.reduce((s, f) => s + f.size, 0),
    });

    let successCount = 0;
    let errorCount = 0;

    // 串行上传（避免并发太多打满后端连接 + embedding 模型）
    for (const task of newTasks) {
      if (abortRef.current) {
        // 标记剩余为取消
        setTasks((prev) => prev.map((t) =>
          t.status === 'pending' || t.status === 'uploading'
            ? { ...t, status: 'error', error: '已取消' }
            : t
        ));
        break;
      }

      // 标记当前文件 uploading
      setTasks((prev) => prev.map((t) =>
        t.uid === task.uid ? { ...t, status: 'uploading' } : t
      ));

      try {
        const { promise } = uploadFilePromise(task.file, (pct) => {
          setTasks((prev) => prev.map((t) =>
            t.uid === task.uid ? { ...t, progress: pct } : t
          ));
        });

        const result = await promise;

        setTasks((prev) => prev.map((t) =>
          t.uid === task.uid ? { ...t, status: 'done', progress: 100, result } : t
        ));

        // 入历史
        setHistory((prev) => [
          { uid: task.uid, result, uploaded_at: new Date().toISOString() },
          ...prev,
        ]);

        if (result.status === 'success') {
          successCount++;
        } else if (result.status === 'partial') {
          message.warning(`${task.file.name} 部分入库：${result.ingested_count}/${result.chunks_count}`);
        }
      } catch (err: any) {
        errorCount++;
        const errMsg = err?.message || '上传失败';
        setTasks((prev) => prev.map((t) =>
          t.uid === task.uid ? { ...t, status: 'error', error: errMsg } : t
        ));
        logger.warn('batch_upload_file_failed', {
          file_name: task.file.name,
          error: errMsg,
          http_status: err?.httpStatus,
        });
      }
    }

    setUploading(false);
    abortRef.current = false;

    const total = supported.length;
    const finalSuccess = successCount;
    const finalError = errorCount + newTasks.filter(t => t.status === 'error').length;

    logger.info('batch_upload_done', {
      total, success: finalSuccess, error: finalError,
    });

    if (finalError === 0) {
      message.success(`全部 ${total} 个文件上传成功`);
    } else if (finalSuccess > 0) {
      message.warning(`上传完成：${finalSuccess} 成功，${finalError} 失败`);
    } else {
      message.error(`全部 ${total} 个文件上传失败`);
    }

    // 刷新知识库状态
    loadStatus();
    return false;  // 阻止 antd 默认上传
  };

  /** 取消上传（标记 abort，当前文件完成后停止后续） */
  const handleCancel = () => {
    abortRef.current = true;
    message.info('正在取消上传...');
  };

  const formatSize = (size: number): string => {
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  };

  const isImage = (name: string) => {
    const ext = name.substring(name.lastIndexOf('.')).toLowerCase();
    return IMAGE_EXTENSIONS.includes(ext);
  };

  const statusColor: Record<string, string> = {
    success: 'success',
    partial: 'warning',
    error: 'error',
  };

  const statusLabel: Record<string, string> = {
    success: '成功',
    partial: '部分入库',
    error: '失败',
  };

  const taskStatusColor: Record<string, string> = {
    pending: 'default',
    uploading: 'processing',
    done: 'success',
    error: 'error',
  };

  const taskStatusLabel: Record<string, string> = {
    pending: '等待中',
    uploading: '上传中',
    done: '完成',
    error: '失败',
  };

  // 总体进度
  const totalProgress = tasks.length > 0
    ? Math.round(tasks.reduce((sum, t) => sum + t.progress, 0) / tasks.length)
    : 0;
  const doneCount = tasks.filter((t) => t.status === 'done' || t.status === 'error').length;

  return (
    <div style={{ padding: 24 }}>
      <Title level={4}>文件管理</Title>

      {/* 知识库状态卡片 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={16}>
          <Col span={6}>
            <Statistic
              title="知识库条目"
              value={knowledgeStatus?.total_entries ?? '-'}
              loading={statusLoading}
            />
          </Col>
          <Col span={6}>
            <Statistic
              title="L3 知识库"
              value={knowledgeStatus?.l3_enabled ? '已启用' : '未启用'}
              valueStyle={{
                color: knowledgeStatus?.l3_enabled ? '#3f8600' : '#cf1322',
              }}
            />
          </Col>
          <Col span={12} style={{ textAlign: 'right' }}>
            <Button
              icon={<ReloadOutlined />}
              onClick={loadStatus}
              loading={statusLoading}
              type="text"
            >
              刷新
            </Button>
          </Col>
        </Row>
      </Card>

      {/* L3 未启用提示 */}
      {knowledgeStatus && !knowledgeStatus?.l3_enabled && (
        <Alert
          type="error"
          showIcon
          message="L3 知识库未启用"
          description="后端未启用 ChromaDB，文件上传无法入库。请检查后端配置（KNOWLEDGE_BASE_ENABLED / ChromaDB 连接）后重试。"
          style={{ marginBottom: 16 }}
        />
      )}

      {/* 上传区域 */}
      <div style={{ marginBottom: 24 }}>
        <Dragger
          accept={SUPPORTED_EXTENSIONS.join(',')}
          multiple
          beforeUpload={(file, fileList) => {
            // antd 会对每个文件调用一次 beforeUpload；
            // 仅在最后一个文件时触发一次批量上传（此时 fileList 已完整）
            if (file.uid === fileList[fileList.length - 1].uid) {
              void handleBatchUpload(fileList);
            }
            return false; // 阻止 antd 默认上传（改由 handleBatchUpload 手动控制）
          }}
          showUploadList={false}
          disabled={uploading || (knowledgeStatus?.l3_enabled === false)}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击选择文件，或拖拽到此区域上传</p>
          <p className="ant-upload-hint">
            支持多选文件（按住 Cmd/Ctrl 可多选）：PDF、Word、TXT、Markdown、图片(JPG/PNG/WebP 等)
            <br />
            <span style={{ color: '#999', fontSize: 12 }}>
              单文件不超过 50MB，选择后会自动逐个上传并显示进度
            </span>
          </p>
        </Dragger>

        {/* 总体进度条 */}
        {tasks.length > 0 && (
          <Card size="small" style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <Space>
                <Text strong>批量上传进度</Text>
                <Tag color={uploading ? 'processing' : (doneCount === tasks.length ? 'success' : 'default')}>
                  {doneCount} / {tasks.length}
                </Tag>
              </Space>
              {uploading && (
                <Button
                  size="small"
                  danger
                  icon={<StopOutlined />}
                  onClick={handleCancel}
                >
                  取消
                </Button>
              )}
            </div>
            <Progress
              percent={totalProgress}
              status={uploading ? 'active' : (doneCount === tasks.length ? 'success' : 'normal')}
            />
          </Card>
        )}

        {/* 逐文件进度列表 */}
        {tasks.length > 0 && (
          <Card size="small" style={{ marginTop: 8 }} title="文件列表">
            {tasks.map((task) => (
              <div
                key={task.uid}
                style={{
                  padding: '6px 0',
                  borderBottom: '1px solid #f0f0f0',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                <span>
                  {isImage(task.file.name) ? <FileImageOutlined /> : <FileOutlined />}
                </span>
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {task.file.webkitRelativePath || task.file.name}
                </span>
                <span style={{ color: '#999', fontSize: 12, minWidth: 60 }}>
                  {formatSize(task.file.size)}
                </span>
                <Tag color={taskStatusColor[task.status]} style={{ minWidth: 56, textAlign: 'center' }}>
                  {taskStatusLabel[task.status]}
                </Tag>
                {task.status === 'uploading' && (
                  <Progress
                    type="circle"
                    size={24}
                    percent={task.progress}
                  />
                )}
                {task.status === 'done' && task.result && (
                  <Tag color={statusColor[task.result.status]} style={{ minWidth: 56, textAlign: 'center' }}>
                    {statusLabel[task.result.status]}
                  </Tag>
                )}
                {task.status === 'error' && (
                  <Text type="danger" style={{ fontSize: 12, maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {task.error}
                  </Text>
                )}
              </div>
            ))}
          </Card>
        )}
      </div>

      {/* 上传历史 */}
      <Card
        size="small"
        title={`上传历史 (${history.length})`}
        extra={
          history.length > 0 && (
            <Button type="link" onClick={() => setHistory([])}>清空</Button>
          )
        }
      >
        {history.length === 0 ? (
          <Text type="secondary">暂无上传记录（仅当前会话有效，刷新页面会清空）</Text>
        ) : (
          <div>
            {history.map((item) => {
              const r = item.result;
              return (
                <div
                  key={item.uid}
                  style={{
                    padding: '8px 0',
                    borderBottom: '1px solid #f0f0f0',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                  }}
                >
                  <span>
                    {isImage(r.file_name) ? <FileImageOutlined /> : <FileOutlined />}
                  </span>
                  <span style={{ flex: 1 }}>{r.file_name}</span>
                  <span style={{ color: '#999' }}>{formatSize(r.file_size)}</span>
                  <Tag color={statusColor[r.status]}>
                    {statusLabel[r.status]}
                  </Tag>
                  <span style={{ color: '#666', fontSize: 12 }}>
                    分块 {r.chunks_count} / 入库 {r.ingested_count}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
};

export default Files;
