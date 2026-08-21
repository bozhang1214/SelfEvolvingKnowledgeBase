import React, { useEffect, useState } from 'react';
import { Upload, Button, Typography, message, Progress, Space, Tag, Alert, Card, Statistic, Row, Col } from 'antd';
import { InboxOutlined, FileOutlined, FileImageOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  uploadFile,
  getKnowledgeStatus,
  type UploadResult,
  type KnowledgeStatus,
} from '@/services/file';
import { logger } from '@/utils/logger';

// 支持的图片扩展名
const IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'];

const { Title, Text } = Typography;
const { Dragger } = Upload;

interface UploadHistoryItem {
  uid: string;
  result: UploadResult;
  uploaded_at: string;
}

const Files: React.FC = () => {
  const [knowledgeStatus, setKnowledgeStatus] = useState<KnowledgeStatus | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<Record<string, number>>({});
  const [history, setHistory] = useState<UploadHistoryItem[]>([]);
  const [statusLoading, setStatusLoading] = useState(false);

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

  const handleUpload = (file: File) => {
    setUploading(true);
    setUploadProgress((prev) => ({ ...prev, [file.name]: 0 }));

    logger.info('upload_submit', {
      file_name: file.name,
      file_size: file.size,
      file_type: file.type,
    });

    uploadFile(
      file,
      (percent) => {
        setUploadProgress((prev) => ({ ...prev, [file.name]: percent }));
      },
      (info) => {
        setUploadProgress((prev) => ({ ...prev, [file.name]: 100 }));
        setUploading(false);
        // 入历史列表
        setHistory((prev) => [
          { uid: `${Date.now()}_${file.name}`, result: info, uploaded_at: new Date().toISOString() },
          ...prev,
        ]);
        // 根据入库状态显示不同消息
        if (info.status === 'success') {
          message.success(
            `${file.name} 上传成功：${info.chunks_count} 分块，${info.ingested_count} 入库`
          );
        } else if (info.status === 'partial') {
          message.warning(
            `${file.name} 部分入库：${info.ingested_count}/${info.chunks_count} 成功，${info.error}`
          );
        } else {
          message.error(`${file.name} 处理失败：${info.error || '未知错误'}`);
        }
        // 刷新知识库状态
        loadStatus();
      },
      (error, httpStatus) => {
        setUploading(false);
        const statusHint = httpStatus > 0 ? `（HTTP ${httpStatus}）` : '';
        message.error(`${file.name} 上传失败${statusHint}：${error}`);
      }
    );

    return false; // 阻止 antd Dragger 默认上传
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
      {knowledgeStatus && !knowledgeStatus.l3_enabled && (
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
          accept=".pdf,.docx,.txt,.md,.jpg,.jpeg,.png,.webp,.bmp,.gif"
          beforeUpload={handleUpload as any}
          showUploadList={false}
          disabled={uploading || (knowledgeStatus?.l3_enabled === false)}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
          <p className="ant-upload-hint">
            支持 PDF、Word、TXT、Markdown、图片(JPG/PNG/WebP 等)，单文件不超过 50MB
            <br />
            <span style={{ color: '#999', fontSize: 12 }}>
              文件将被解析、分块并入库到知识库；图片将自动 OCR 提取文字并打标签入库
            </span>
          </p>
        </Dragger>
        {uploading && Object.entries(uploadProgress).map(([name, progress]) => (
          <div key={name} style={{ marginTop: 8 }}>
            <Space>
              <span>{name}</span>
              <Progress percent={progress} size="small" style={{ width: 200 }} />
            </Space>
          </div>
        ))}
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
