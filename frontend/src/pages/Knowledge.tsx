import React, { useEffect, useState } from 'react';
import { Table, Input, Typography, Button, Space, Tag, Popconfirm, message, Rate } from 'antd';
import { SearchOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import apiClient from '@/services/api';
import type { ApiResponse } from '@/types/api';

const { Title } = Typography;

interface KnowledgeEntry {
  entry_id: string;
  content: string;
  source: string;
  source_id: string;
  importance_score: number;
  version: number;
  created_at: string;
  similarity_score?: number;
}

const Knowledge: React.FC = () => {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [searchText, setSearchText] = useState('');
  const [page, setPage] = useState(1);

  const loadEntries = async () => {
    setLoading(true);
    try {
      const res = await apiClient.get<ApiResponse<{ entries: KnowledgeEntry[]; total: number }>>('/knowledge', {
        params: { page, page_size: 20 },
      });
      setEntries(res.data.data.entries);
      setTotal(res.data.data.total);
    } catch {
      // 静默处理
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadEntries();
  }, [page]);

  const handleSearch = async () => {
    if (!searchText.trim()) {
      loadEntries();
      return;
    }
    setLoading(true);
    try {
      const res = await apiClient.get<ApiResponse<{ entries: KnowledgeEntry[] }>>('/knowledge/search', {
        params: { q: searchText, top_k: 50 },
      });
      setEntries(res.data.data.entries);
      setTotal(res.data.data.entries.length);
    } catch {
      // 静默处理
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (entryId: string) => {
    try {
      await apiClient.delete(`/knowledge/${entryId}`);
      message.success('删除成功');
      loadEntries();
    } catch {
      message.error('删除失败');
    }
  };

  const columns = [
    {
      title: '内容',
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 100,
      render: (s: string) => <Tag>{s || '未知'}</Tag>,
    },
    {
      title: '重要性',
      dataIndex: 'importance_score',
      key: 'importance_score',
      width: 150,
      render: (score: number) => (
        <Rate disabled value={Math.round(score * 5)} count={5} />
      ),
    },
    {
      title: '相似度',
      dataIndex: 'similarity_score',
      key: 'similarity_score',
      width: 100,
      render: (s: number | undefined) => s ? s.toFixed(3) : '-',
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 60,
    },
    {
      title: '入库时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (t: string) => t ? new Date(t).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      render: (_: unknown, record: KnowledgeEntry) => (
        <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.entry_id)}>
          <Button type="link" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <Title level={4} style={{ margin: 0 }}>知识库</Title>
        <Space>
          <Input.Search
            placeholder="搜索知识库..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            onSearch={handleSearch}
            enterButton={<SearchOutlined />}
            style={{ width: 300 }}
          />
          <Button icon={<ReloadOutlined />} onClick={loadEntries}>刷新</Button>
        </Space>
      </Space>

      <Table
        dataSource={entries}
        columns={columns}
        rowKey="entry_id"
        loading={loading}
        pagination={{
          current: page,
          pageSize: 20,
          total,
          onChange: (p) => setPage(p),
        }}
        locale={{ emptyText: '知识库为空' }}
      />
    </div>
  );
};

export default Knowledge;