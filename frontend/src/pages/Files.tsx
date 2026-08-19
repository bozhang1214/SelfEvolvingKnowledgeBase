import React, { useEffect, useState } from 'react';
import { Table, Upload, Button, Typography, message, Progress, Space, Popconfirm, Tag } from 'antd';
import { InboxOutlined, DeleteOutlined, FileOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd';
import { listFiles, deleteFile, uploadFile, FileInfo } from '@/services/file';

const { Title } = Typography;
const { Dragger } = Upload;

const Files: React.FC = () => {
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<Record<string, number>>({});

  const loadFiles = async () => {
    setLoading(true);
    try {
      const data = await listFiles();
      setFiles(data);
    } catch {
      // 静默处理
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFiles();
  }, []);

  const handleUpload = (file: File) => {
    setUploading(true);
    setUploadProgress((prev) => ({ ...prev, [file.name]: 0 }));

    uploadFile(
      file,
      (percent) => {
        setUploadProgress((prev) => ({ ...prev, [file.name]: percent }));
      },
      (info) => {
        setUploadProgress((prev) => ({ ...prev, [file.name]: 100 }));
        setUploading(false);
        message.success(`${file.name} 上传成功`);
        loadFiles();
      },
      (error) => {
        setUploading(false);
        message.error(`${file.name} 上传失败: ${error}`);
      }
    );

    return false; // 阻止默认上传
  };

  const handleDelete = async (fileId: string) => {
    try {
      await deleteFile(fileId);
      message.success('删除成功');
      loadFiles();
    } catch {
      message.error('删除失败');
    }
  };

  const columns = [
    {
      title: '文件名',
      dataIndex: 'file_name',
      key: 'file_name',
      render: (name: string) => (
        <Space>
          <FileOutlined />
          {name}
        </Space>
      ),
    },
    {
      title: '大小',
      dataIndex: 'file_size',
      key: 'file_size',
      width: 120,
      render: (size: number) => {
        if (size < 1024) return `${size} B`;
        if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
        return `${(size / (1024 * 1024)).toFixed(1)} MB`;
      },
    },
    {
      title: '状态',
      dataIndex: 'parse_status',
      key: 'parse_status',
      width: 120,
      render: (status: string) => {
        const colors: Record<string, string> = {
          pending: 'default',
          parsing: 'processing',
          done: 'success',
          failed: 'error',
        };
        const labels: Record<string, string> = {
          pending: '排队中',
          parsing: '解析中',
          done: '已入库',
          failed: '失败',
        };
        return <Tag color={colors[status] || 'default'}>{labels[status] || status}</Tag>;
      },
    },
    {
      title: '上传时间',
      dataIndex: 'upload_time',
      key: 'upload_time',
      width: 180,
      render: (t: string) => t ? new Date(t).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      render: (_: unknown, record: FileInfo) => (
        <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.file_id)}>
          <Button type="link" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Title level={4}>文件管理</Title>

      <div style={{ marginBottom: 24 }}>
        <Dragger
          accept=".pdf,.docx,.txt,.md"
          beforeUpload={handleUpload as any}
          showUploadList={false}
          disabled={uploading}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
          <p className="ant-upload-hint">
            支持 PDF、Word(.docx)、TXT、Markdown，单个文件不超过 50MB
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

      <Table
        dataSource={files}
        columns={columns}
        rowKey="file_id"
        loading={loading}
        pagination={{ pageSize: 20 }}
        locale={{ emptyText: '暂无文件' }}
      />
    </div>
  );
};

export default Files;