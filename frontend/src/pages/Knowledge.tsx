import React, { useEffect, useMemo, useState } from 'react';
import {
  Layout, Table, Input, Typography, Button, Space, Tag, Popconfirm, message,
  Tree, Cascader, Modal, Form, Spin, Empty, Tooltip, Rate,
} from 'antd';
import type { TreeDataNode } from 'antd';
import {
  SearchOutlined, DeleteOutlined, ReloadOutlined, ShareAltOutlined,
  FolderOutlined, EditOutlined, LinkOutlined, CopyOutlined, CheckOutlined,
} from '@ant-design/icons';
import apiClient, { unwrap } from '@/services/api';
import { useUserStore } from '@/stores/user';

const { Title, Text, Paragraph } = Typography;
const { Sider, Content } = Layout;

interface KnowledgeEntry {
  entry_id: string;
  content: string;
  source: string;
  source_id: string;
  importance_score: number;
  version: number;
  created_at: string;
  similarity_score?: number;
  category_l1: string;
  category_l2: string;
  category_l3: string;
  category_confidence: number;
  category_source: string;
}

interface CategoryRaw {
  [l1: string]: { [l2: string]: string[] };
}

interface ShareItem {
  share_id: string;
  title: string;
  created_at: string;
  is_active: boolean;
  has_expired: boolean;
  share_url: string;
  entries_count: number;
  category_l1?: string;
  category_l2?: string;
  category_l3?: string;
  category_label?: string;
}

/** 将分类原始结构转为 Cascader 选项 */
function buildCascaderOptions(raw: CategoryRaw) {
  return Object.entries(raw).map(([l1, l2Map]) => ({
    value: l1,
    label: l1,
    children: Object.entries(l2Map).map(([l2, l3List]) => ({
      value: l2,
      label: l2,
      children: l3List.map((l3) => ({ value: l3, label: l3 })),
    })),
  }));
}

/** 将分类原始结构转为 Antd Tree 数据（带条目数） */
function buildCategoryTree(raw: CategoryRaw, counts: Record<string, number>): TreeDataNode[] {
  const allCount = counts['__all__'] || 0;
  const nodes: TreeDataNode[] = [
    { key: '__all__', title: <Text strong>全部 ({allCount})</Text> },
  ];
  for (const [l1, l2Map] of Object.entries(raw)) {
    const l1Count = counts[`l1::${l1}`] || 0;
    const l2Nodes: TreeDataNode[] = [];
    for (const [l2, l3List] of Object.entries(l2Map)) {
      const l2Count = counts[`l2::${l1}::${l2}`] || 0;
      const l3Nodes: TreeDataNode[] = l3List.map((l3) => {
        const c = counts[`l3::${l1}::${l2}::${l3}`] || 0;
        return { key: `l3::${l1}::${l2}::${l3}`, title: <span>{l3} <Text type="secondary" style={{ fontSize: 12 }}>({c})</Text></span> };
      });
      l2Nodes.push({
        key: `l2::${l1}::${l2}`,
        title: <span>{l2} <Text type="secondary" style={{ fontSize: 12 }}>({l2Count})</Text></span>,
        children: l3Nodes,
      });
    }
    nodes.push({
      key: `l1::${l1}`,
      title: <span><FolderOutlined /> <Text strong>{l1}</Text> <Text type="secondary" style={{ fontSize: 12 }}>({l1Count})</Text></span>,
      children: l2Nodes,
    });
  }
  return nodes;
}

const Knowledge: React.FC = () => {
  const { user } = useUserStore();
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [searchText, setSearchText] = useState('');
  const [page, setPage] = useState(1);

  const [categoryRaw, setCategoryRaw] = useState<CategoryRaw>({});
  const [categoryCounts, setCategoryCounts] = useState<Record<string, number>>({});
  const [selectedKey, setSelectedKey] = useState<string>('__all__');
  const [treeLoading, setTreeLoading] = useState(false);

  // 重分类
  const [reclassifyEntry, setReclassifyEntry] = useState<KnowledgeEntry | null>(null);
  const [reclassifying, setReclassifying] = useState(false);

  // 分享
  const [shareModalOpen, setShareModalOpen] = useState(false);
  const [shareListOpen, setShareListOpen] = useState(false);
  const [shares, setShares] = useState<ShareItem[]>([]);
  const [createdShare, setCreatedShare] = useState<ShareItem | null>(null);
  const [creatingShare, setCreatingShare] = useState(false);
  const [shareForm] = Form.useForm();
  const [copied, setCopied] = useState(false);

  const cascaderOptions = useMemo(() => buildCascaderOptions(categoryRaw), [categoryRaw]);
  const treeData = useMemo(
    () => buildCategoryTree(categoryRaw, categoryCounts),
    [categoryRaw, categoryCounts],
  );

  // 加载分类目录
  const loadCategories = async () => {
    setTreeLoading(true);
    try {
      const res = await apiClient.get('/knowledge/categories');
      const data = unwrap<{ raw: CategoryRaw }>(res);
      setCategoryRaw(data.raw || {});
    } catch {
      // 静默
    } finally {
      setTreeLoading(false);
    }
  };

  // 加载分类统计
  const loadCategoryStats = async () => {
    try {
      const res = await apiClient.get('/knowledge/categories/stats');
      const data = unwrap<{ stats: Array<{ category_l1: string; category_l2: string; category_l3: string; count: number }>; total: number }>(res);
      const counts: Record<string, number> = { __all__: data.total || 0 };
      for (const s of data.stats || []) {
        counts[`l3::${s.category_l1}::${s.category_l2}::${s.category_l3}`] = s.count;
        const l2Key = `l2::${s.category_l1}::${s.category_l2}`;
        const l1Key = `l1::${s.category_l1}`;
        counts[l2Key] = (counts[l2Key] || 0) + s.count;
        counts[l1Key] = (counts[l1Key] || 0) + s.count;
      }
      setCategoryCounts(counts);
    } catch {
      // 静默
    }
  };

  // 解析当前选中分类为过滤参数
  const categoryFilter = useMemo(() => {
    if (selectedKey === '__all__') return {};
    const parts = selectedKey.split('::');
    if (parts[0] === 'l1') return { category_l1: parts[1] };
    if (parts[0] === 'l2') return { category_l1: parts[1], category_l2: parts[2] };
    if (parts[0] === 'l3') return { category_l1: parts[1], category_l2: parts[2], category_l3: parts[3] };
    return {};
  }, [selectedKey]);

  const loadEntries = async () => {
    setLoading(true);
    try {
      const res = await apiClient.get('/knowledge', {
        params: { page, page_size: 20, ...categoryFilter },
      });
      const data = unwrap<{ entries: KnowledgeEntry[]; total: number }>(res);
      setEntries(data.entries || []);
      setTotal(data.total || 0);
    } catch {
      setEntries([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCategories();
    loadCategoryStats();
  }, []);

  useEffect(() => {
    loadEntries();
  }, [page, selectedKey]);

  const handleSearch = async () => {
    if (!searchText.trim()) {
      loadEntries();
      return;
    }
    setLoading(true);
    try {
      const res = await apiClient.get('/knowledge/search', {
        params: { q: searchText, top_k: 50 },
      });
      const data = unwrap<{ entries: KnowledgeEntry[] }>(res);
      setEntries(data.entries || []);
      setTotal((data.entries || []).length);
    } catch {
      setEntries([]);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (entryId: string) => {
    try {
      await apiClient.delete(`/knowledge/${entryId}`);
      message.success('删除成功');
      loadEntries();
      loadCategoryStats();
    } catch {
      message.error('删除失败');
    }
  };

  const handleReclassify = async (values: { category: string[] }) => {
    if (!reclassifyEntry || !values.category || values.category.length < 3) return;
    setReclassifying(true);
    try {
      await apiClient.patch(`/knowledge/${reclassifyEntry.entry_id}/category`, {
        category_l1: values.category[0],
        category_l2: values.category[1],
        category_l3: values.category[2],
      });
      message.success('分类已更新');
      setReclassifyEntry(null);
      loadEntries();
      loadCategoryStats();
    } catch {
      message.error('更新分类失败');
    } finally {
      setReclassifying(false);
    }
  };

  // 分享
  const loadShares = async () => {
    try {
      const res = await apiClient.get('/share');
      const data = unwrap<{ shares: ShareItem[] }>(res);
      setShares(data.shares || []);
    } catch {
      setShares([]);
    }
  };

  const handleCreateShare = async () => {
    const title = shareForm.getFieldValue('title') || '';
    // 分类范围：Cascader 选中的 [l1, l2?, l3?]，空 = 分享整个知识库
    const category: string[] = shareForm.getFieldValue('category') || [];
    setCreatingShare(true);
    try {
      const res = await apiClient.post('/share', {
        title,
        category_l1: category[0] || '',
        category_l2: category[1] || '',
        category_l3: category[2] || '',
      });
      const data = unwrap<ShareItem>(res);
      const origin = window.location.origin;
      setCreatedShare({ ...data, share_url: `${origin}${data.share_url}` });
      message.success('分享链接已生成');
      shareForm.resetFields();
      loadShares();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '创建分享失败');
    } finally {
      setCreatingShare(false);
    }
  };

  const handleRevokeShare = async (shareId: string) => {
    try {
      await apiClient.delete(`/share/${shareId}`);
      message.success('已撤销分享');
      loadShares();
    } catch {
      message.error('撤销失败');
    }
  };

  const copyShareLink = (url: string) => {
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true);
      message.success('链接已复制');
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const columns = [
    {
      title: '内容',
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
    },
    {
      title: '分类',
      key: 'category',
      width: 220,
      render: (_: unknown, r: KnowledgeEntry) => (
        <Space size={4} wrap>
          <Tag color="blue">{r.category_l1}</Tag>
          <Tag color="geekblue">{r.category_l2}</Tag>
          <Tag color="purple">{r.category_l3}</Tag>
          {r.category_source === 'manual' && (
            <Tooltip title="手动修正"><EditOutlined style={{ fontSize: 11, color: '#faad14' }} /></Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 90,
      render: (s: string) => <Tag>{s || '未知'}</Tag>,
    },
    {
      title: '置信度',
      dataIndex: 'category_confidence',
      key: 'category_confidence',
      width: 90,
      render: (c: number) => (c != null ? `${Math.round((c || 0) * 100)}%` : '-'),
    },
    {
      title: '重要性',
      dataIndex: 'importance_score',
      key: 'importance_score',
      width: 120,
      render: (score: number) => <Rate disabled value={Math.round((score || 0) * 5)} count={5} />,
    },
    {
      title: '入库时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (t: string) => (t ? new Date(t).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 110,
      render: (_: unknown, r: KnowledgeEntry) => (
        <Space size={4}>
          <Tooltip title="修正分类">
            <Button type="link" size="small" icon={<EditOutlined />} onClick={() => setReclassifyEntry(r)} />
          </Tooltip>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(r.entry_id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Layout style={{ minHeight: 'calc(100vh - 0px)' }}>
      {/* 分类目录树侧边栏 */}
      <Sider width={280} theme="light" style={{ borderRight: '1px solid #f0f0f0', overflow: 'auto', background: '#fafafa' }}>
        <div style={{ padding: '16px 12px 8px', borderBottom: '1px solid #f0f0f0' }}>
          <Text strong>分类目录</Text>
        </div>
        <Spin spinning={treeLoading}>
          <Tree
            treeData={treeData}
            defaultExpandedKeys={['__all__']}
            selectedKeys={[selectedKey]}
            onSelect={(keys) => {
              if (keys.length > 0) {
                setSelectedKey(keys[0] as string);
                setPage(1);
              }
            }}
            style={{ padding: '8px 8px', background: 'transparent' }}
          />
        </Spin>
      </Sider>

      {/* 主区域 */}
      <Content style={{ padding: 24, background: '#fff', overflow: 'auto' }}>
        <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
          <Title level={4} style={{ margin: 0 }}>知识库</Title>
          <Space>
            <Input.Search
              placeholder="搜索知识库..."
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              onSearch={handleSearch}
              enterButton={<SearchOutlined />}
              style={{ width: 280 }}
            />
            <Button icon={<ShareAltOutlined />} onClick={() => { setShareModalOpen(true); loadShares(); }}>分享</Button>
            <Button icon={<LinkOutlined />} onClick={() => { setShareListOpen(true); loadShares(); }}>我的分享</Button>
            <Button icon={<ReloadOutlined />} onClick={() => { loadEntries(); loadCategoryStats(); }}>刷新</Button>
          </Space>
        </Space>

        <Table
          dataSource={entries}
          columns={columns}
          rowKey="entry_id"
          loading={loading}
          size="middle"
          pagination={{
            current: page,
            pageSize: 20,
            total,
            onChange: (p) => setPage(p),
            showTotal: (t) => `共 ${t} 条`,
          }}
          locale={{ emptyText: <Empty description="知识库为空，上传文档后将自动分类入库" /> }}
        />
      </Content>

      {/* 手动重分类弹窗 */}
      <Modal
        title="修正分类"
        open={!!reclassifyEntry}
        onCancel={() => setReclassifyEntry(null)}
        footer={null}
        destroyOnClose
      >
        {reclassifyEntry && (
          <Form
            layout="vertical"
            onFinish={handleReclassify}
            initialValues={{
              category: [reclassifyEntry.category_l1, reclassifyEntry.category_l2, reclassifyEntry.category_l3],
            }}
          >
            <Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ marginBottom: 12 }}>
              {reclassifyEntry.content}
            </Paragraph>
            <Form.Item name="category" label="选择分类" rules={[{ required: true, message: '请选择分类' }]}>
              <Cascader options={cascaderOptions} placeholder="请选择 大类 / 子类 / 细类" />
            </Form.Item>
            <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
              <Space>
                <Button onClick={() => setReclassifyEntry(null)}>取消</Button>
                <Button type="primary" htmlType="submit" loading={reclassifying}>保存</Button>
              </Space>
            </Form.Item>
          </Form>
        )}
      </Modal>

      {/* 创建分享弹窗 */}
      <Modal
        title="分享我的知识库"
        open={shareModalOpen}
        onCancel={() => { setShareModalOpen(false); setCreatedShare(null); }}
        footer={createdShare ? [
          <Button key="close" onClick={() => { setShareModalOpen(false); setCreatedShare(null); }}>完成</Button>,
        ] : [
          <Button key="cancel" onClick={() => { setShareModalOpen(false); setCreatedShare(null); }}>取消</Button>,
          <Button key="create" type="primary" loading={creatingShare} onClick={handleCreateShare}>生成链接</Button>,
        ]}
        destroyOnClose
      >
        {createdShare ? (
          <div>
            <Paragraph>分享链接已生成，其他用户登录后即可访问并基于你的知识库对话：</Paragraph>
            <Input.Group compact>
              <Input value={createdShare.share_url} readOnly style={{ width: 'calc(100% - 90px)' }} />
              <Button
                type="primary"
                icon={copied ? <CheckOutlined /> : <CopyOutlined />}
                onClick={() => copyShareLink(createdShare.share_url)}
                style={{ width: 90 }}
              >
                {copied ? '已复制' : '复制'}
              </Button>
            </Input.Group>
            <Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12 }}>
              分享范围：{createdShare.category_label || '全部'} · 权限：只读浏览 + 可对话 · 共享知识条目 {createdShare.entries_count} 条
            </Paragraph>
          </div>
        ) : (
          <Form form={shareForm} layout="vertical">
            <Form.Item name="title" label="分享标题">
              <Input placeholder={`${user?.name || user?.email || '我'}的知识库`} />
            </Form.Item>
            <Form.Item
              name="category"
              label="分享范围（可选，默认整个知识库）"
              extra="可只分享某一大类（如「技术开发」）、子类或细类，选到哪一级就只分享到哪一级"
            >
              <Cascader
                options={cascaderOptions}
                placeholder="全部（整个知识库）"
                allowClear
                changeOnSelect
                style={{ width: '100%' }}
              />
            </Form.Item>
            <Paragraph type="secondary" style={{ fontSize: 12 }}>
              分享后，其他已注册用户可通过链接只读浏览分享范围内的知识，并基于其内容进行问答对话，无法修改或删除你的文档。
            </Paragraph>
          </Form>
        )}
      </Modal>

      {/* 我的分享列表 */}
      <Modal
        title="我的分享"
        open={shareListOpen}
        onCancel={() => setShareListOpen(false)}
        footer={[<Button key="close" onClick={() => setShareListOpen(false)}>关闭</Button>]}
      >
        <Table
          dataSource={shares}
          rowKey="share_id"
          size="small"
          pagination={false}
          locale={{ emptyText: '暂无分享' }}
          columns={[
            { title: '标题', dataIndex: 'title', key: 'title' },
            {
              title: '范围', key: 'scope', width: 130,
              render: (_: unknown, r: ShareItem) => (
                <Tag color={r.category_label ? 'geekblue' : 'default'}>{r.category_label || '全部'}</Tag>
              ),
            },
            { title: '条目', dataIndex: 'entries_count', key: 'entries_count', width: 70 },
            {
              title: '状态', key: 'status', width: 90,
              render: (_: unknown, r: ShareItem) => r.has_expired ? <Tag>已过期</Tag> : (r.is_active ? <Tag color="green">有效</Tag> : <Tag>已撤销</Tag>),
            },
            {
              title: '操作', key: 'op', width: 140,
              render: (_: unknown, r: ShareItem) => (
                <Space size={4}>
                  <Button size="small" icon={<CopyOutlined />} onClick={() => copyShareLink(`${window.location.origin}${r.share_url}`)}>链接</Button>
                  <Popconfirm title="确定撤销该分享？" onConfirm={() => handleRevokeShare(r.share_id)}>
                    <Button size="small" danger>撤销</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Modal>
    </Layout>
  );
};

export default Knowledge;
