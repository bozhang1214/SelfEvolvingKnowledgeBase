import React, { useEffect, useState, useRef } from 'react';
import { Button, Typography, message, Progress, Space, Tag, Alert, Card, Statistic, Row, Col, Modal, List, Checkbox, Tree } from 'antd';
import {
  InboxOutlined, FileOutlined, FileTextOutlined, FileImageOutlined, ReloadOutlined, StopOutlined,
  RadarChartOutlined, FolderOpenOutlined, FolderOutlined, ReadOutlined,
} from '@ant-design/icons';
import {
  getKnowledgeStatus,
  listSeries,
  listFiles,
  analyzeKnowledgeBase,
  computeFileHash,
  reSeriesFiles,
  type UploadResult,
  type KnowledgeStatus,
  type SeriesGroup,
  type UploadedFile,
  type KnowledgeAnalysis,
} from '@/services/file';
import { logger } from '@/utils/logger';
import { useUploadStore, readPendingFiles, clearPendingFiles, fileKey } from '@/stores/upload';

// 支持的文件扩展名（与后端 FileProcessor 对齐）
const SUPPORTED_EXTENSIONS = [
  '.pdf', '.docx', '.txt', '.md',
  '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif',
];
const IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'];

const { Title, Text } = Typography;

interface UploadHistoryItem {
  uid: string;
  result: UploadResult;
  uploaded_at: string;
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

/** 把系列分组转成 antd Tree 的 treeData（系列 → 子目录 → 文件，多级折叠）。 */
function buildSeriesTree(groups: SeriesGroup[]): any[] {
  return groups.map((g) => {
    const rootChildren: any[] = [];
    const dirMap = new Map<string, any>();

    for (const f of g.files) {
      // 去掉系列名前缀，得到相对路径（可能含子目录）
      let rel = f.file_name;
      if (rel.startsWith(g.series + '/')) rel = rel.slice(g.series.length + 1);
      const parts = rel.split('/');
      const fileName = parts[parts.length - 1];
      const dirParts = parts.slice(0, -1);
      const label = (f.part > 0 ? `${f.part}. ` : '') + fileName;

      if (dirParts.length === 0) {
        // 无子目录，直接挂在系列下
        rootChildren.push({ key: f.file_name, title: label, icon: <FileTextOutlined />, isLeaf: true });
      } else {
        // 有子目录，逐级构建
        let currentLevel = rootChildren;
        let currentPath = '';
        for (const dir of dirParts) {
          currentPath = currentPath ? `${currentPath}/${dir}` : dir;
          let node = dirMap.get(currentPath);
          if (!node) {
            node = { key: currentPath, title: dir, icon: <FolderOutlined />, children: [] };
            dirMap.set(currentPath, node);
            currentLevel.push(node);
          }
          currentLevel = node.children;
        }
        currentLevel.push({ key: f.file_name, title: label, icon: <FileTextOutlined />, isLeaf: true });
      }
    }

    return {
      key: g.series,
      title: `${g.series}（${g.count} 篇）`,
      icon: <ReadOutlined />,
      children: rootChildren,
    };
  });
}

const Files: React.FC = () => {
  const { tasks, uploading, startUpload, cancel: cancelUpload } = useUploadStore();
  const [knowledgeStatus, setKnowledgeStatus] = useState<KnowledgeStatus | null>(null);
  const [history, setHistory] = useState<UploadHistoryItem[]>([]);
  const [statusLoading, setStatusLoading] = useState(false);
  const [seriesGroups, setSeriesGroups] = useState<SeriesGroup[]>([]);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [analysis, setAnalysis] = useState<KnowledgeAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  // 续传时勾选的文件路径集合（一次性：选中文件后即清除）
  const resumeFilterRef = useRef<Set<string> | null>(null);

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

  const loadSeries = async () => {
    setSeriesLoading(true);
    try {
      const data = await listSeries();
      setSeriesGroups(data);
    } catch (err: any) {
      logger.warn('load_series_failed', { msg: err?.message });
    } finally {
      setSeriesLoading(false);
    }
  };

  const loadFiles = async () => {
    setFilesLoading(true);
    try {
      const data = await listFiles();
      setUploadedFiles(data);
    } catch (err: any) {
      logger.warn('load_files_failed', { msg: err?.message });
    } finally {
      setFilesLoading(false);
    }
  };

  const handleAnalyze = async () => {
    setAnalyzing(true);
    try {
      const data = await analyzeKnowledgeBase();
      setAnalysis(data);
      message.success(`分析完成：${data.document_files} 个文件，${data.total_entries} 个条目`);
      loadFiles();
      loadSeries();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '知识库分析失败');
    } finally {
      setAnalyzing(false);
    }
  };

  /** 重新识别所有文档的系列名（不重新分类，轻量）。 */
  const handleReSeries = async () => {
    setSeriesLoading(true);
    try {
      const data = await reSeriesFiles();
      message.success(`系列重新识别完成：${data.files_re_series} 个文件`);
      loadSeries();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '系列重新识别失败');
    } finally {
      setSeriesLoading(false);
    }
  };

  useEffect(() => {
    loadStatus();
    loadSeries();
    loadFiles();

    // 检测上次刷新/中断遗留的未完成上传，弹窗让用户勾选需续传的文件
    void (async () => {
      const pending = readPendingFiles();
      if (pending.length === 0) return;

      // 过滤掉已经入库的文件（pending 里可能残留「已成功但未及时移除」的条目）
      let uploadedNames = new Set<string>();
      try {
        const uploaded = await listFiles();
        uploadedNames = new Set(uploaded.map((f) => f.file_name));
      } catch {
        // 拉取失败则不过滤，按全部未完成处理
      }
      const todo = pending.filter((p) => !uploadedNames.has(p));
      if (todo.length === 0) {
        clearPendingFiles();
        return;
      }

      // 复选框状态（受控于 onChange，闭包变量在 onOk 时读取）
      const checked = new Set(todo);
      const hasFolderPaths = todo.some((p) => p.includes('/'));

      Modal.confirm({
        title: `检测到 ${todo.length} 个未完成的上传`,
        icon: null,
        width: 560,
        content: (
          <div>
            <p style={{ marginBottom: 6 }}>以下文件上次未上传完成，勾选需要继续上传的文件：</p>
            <div style={{ maxHeight: 220, overflow: 'auto', marginBottom: 8, background: '#fafafa', padding: '8px 10px', borderRadius: 6 }}>
              {todo.map((n) => (
                <div key={n} style={{ margin: '3px 0' }}>
                  <Checkbox
                    defaultChecked
                    onChange={(e) => {
                      if (e.target.checked) checked.add(n);
                      else checked.delete(n);
                    }}
                  >
                    <span style={{ fontSize: 13, wordBreak: 'break-all' }}>{n}</span>
                  </Checkbox>
                </div>
              ))}
            </div>
            <p style={{ color: '#999', fontSize: 12, marginBottom: 0 }}>
              文件内容无法跨刷新保存，需重新选择对应文件后继续上传（仅上传勾选的文件）
            </p>
          </div>
        ),
        okText: '选择文件并上传',
        cancelText: '忽略',
        onOk: () => {
          if (checked.size === 0) {
            message.warning('未勾选任何文件');
            return;
          }
          resumeFilterRef.current = new Set(checked);
          // 文件夹上传（路径含 /）用文件夹选择器以保留相对路径；单文件用文件选择器
          if (hasFolderPaths) folderInputRef.current?.click();
          else fileInputRef.current?.click();
        },
        onCancel: () => {
          resumeFilterRef.current = null;
          clearPendingFiles();
        },
      });
    })();
  }, []);

  /** 处理一批选中的文件（文件/文件夹展开后的统一入口）；filter 用于续传时只上传勾选的文件。 */
  const handleFilesSelected = (files: File[], filter?: Set<string>) => {
    let toUpload = files;
    if (filter) {
      toUpload = files.filter((f) => filter.has(fileKey(f)));
      if (toUpload.length === 0) {
        message.warning('所选文件中没有勾选需要续传的文件，请重新选择');
        return;
      }
    }
    if (toUpload.length > 0) {
      void handleBatchUpload(toUpload);
    } else {
      message.warning('未选择任何文件');
    }
  };

  /** 拖拽放下：递归展开文件和文件夹（文件夹递归到最深一层）。 */
  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (uploading) return;
    resumeFilterRef.current = null; // 拖拽是新的上传意图，清除续传过滤
    const items = e.dataTransfer?.items;
    if (items && items.length > 0) {
      // 优先用 DataTransferItemList（webkitGetAsEntry）递归展开文件夹
      const files = await flattenItems(items);
      handleFilesSelected(files);
    } else {
      handleFilesSelected(Array.from(e.dataTransfer?.files || []));
    }
  };

  /** 批量上传入口：处理 antd Upload 选择的文件列表 */
  const handleBatchUpload = async (fileList: File[]) => {
    const supported = filterSupportedFiles(fileList);
    if (supported.length === 0) {
      message.warning('未选择支持的文件类型');
      return false;
    }

    // 去重检查：对比已上传文件的名称与内容哈希（SHA-256）
    let existingFiles: UploadedFile[] = [];
    try {
      existingFiles = await listFiles();
    } catch {
      // listFiles 失败不阻断上传，按非重复处理
    }
    const existingByName = new Map(existingFiles.map((f) => [f.file_name, f]));
    const existingNames = new Set(existingFiles.map((f) => f.file_name));

    // 计算每个文件的哈希（用于同名内容对比）
    const withHash = await Promise.all(
      supported.map(async (f) => ({ file: f, hash: await computeFileHash(f) }))
    );

    // 分类：同名同内容（可跳过）/ 同名不同内容（需覆盖）。
    // 用相对路径（fileKey）匹配后端入库的 file_name，文件夹上传时才是完整路径而非 basename。
    const sameContent: File[] = [];
    const sameNameDiff: File[] = [];
    for (const { file, hash } of withHash) {
      const existing = existingByName.get(fileKey(file));
      if (!existing) continue;
      if (hash && existing.md5 && hash === existing.md5) {
        sameContent.push(file);
      } else {
        sameNameDiff.push(file);
      }
    }

    // 实际要上传的文件列表（默认全部，跳过同名同内容时移除）
    let filesToUpload = supported;

    // 1. 处理「同名同内容」：提供跳过 + 复选框
    if (sameContent.length > 0) {
      const result = await new Promise<'skip' | 'upload' | 'cancel'>((resolve) => {
        let skipAll = true; // 默认勾选跳过
        Modal.confirm({
          title: `发现 ${sameContent.length} 个内容相同的文件`,
          icon: null,
          content: (
            <div>
              <p style={{ marginBottom: 6 }}>以下文件与知识库已有文件「同名且内容一致」：</p>
              <div style={{ maxHeight: 140, overflow: 'auto', marginBottom: 8, background: '#fafafa', padding: '6px 10px', borderRadius: 6 }}>
                {sameContent.map((f) => <div key={f.name} style={{ fontSize: 12 }}>· {f.name}</div>)}
              </div>
              <Checkbox defaultChecked onChange={(e) => { skipAll = e.target.checked; }}>
                跳过这些内容相同的文件
              </Checkbox>
              <p style={{ color: '#999', fontSize: 12, marginTop: 4, marginBottom: 0 }}>
                勾选后，同名且内容一致的文件会被跳过、不重复上传；取消勾选则仍会重新上传
              </p>
            </div>
          ),
          okText: '确定',
          cancelText: '取消上传',
          onOk: () => resolve(skipAll ? 'skip' : 'upload'),
          onCancel: () => resolve('cancel'),
        });
      });
      if (result === 'cancel') {
        message.info('已取消上传');
        return false;
      }
      if (result === 'skip') {
        const skipSet = new Set(sameContent);
        filesToUpload = supported.filter((f) => !skipSet.has(f));
        if (filesToUpload.length === 0) {
          message.info(`已跳过 ${sameContent.length} 个内容相同的文件，没有需要上传的文件`);
          return false;
        }
        message.info(`已跳过 ${sameContent.length} 个内容相同的文件`);
      }
    }

    // 2. 处理「同名不同内容」：询问覆盖还是取消（只针对 filesToUpload 里的文件）
    let overwrite = false;
    const stillDup = filesToUpload.filter((f) => sameNameDiff.includes(f));
    if (stillDup.length > 0) {
      const names = stillDup.map((d) => d.name).slice(0, 6).join('、');
      const more = stillDup.length > 6 ? ` 等 ${stillDup.length} 个` : '';
      const choice = await new Promise<'overwrite' | 'cancel'>((resolve) => {
        Modal.confirm({
          title: '发现同名但内容不同的文件',
          content: `以下 ${stillDup.length} 个文件已存在于知识库，但内容不同：${names}${more}。是否覆盖旧文件？（覆盖会删除旧版本再重新入库）`,
          okText: '覆盖旧文件',
          cancelText: '取消上传',
          onOk: () => resolve('overwrite'),
          onCancel: () => resolve('cancel'),
        });
      });
      if (choice === 'cancel') {
        message.info('已取消上传');
        return false;
      }
      overwrite = true;
    }

    // 调 store 上传（上传循环跑在 store 里，切换导航/组件卸载不中断）
    const { success, error, total } = await startUpload(filesToUpload, { overwrite, existingNames });

    // 把成功上传的文件补入「上传历史」（当前会话展示）
    const doneTasks = useUploadStore.getState().tasks.filter((t) => t.status === 'done' && t.result);
    if (doneTasks.length > 0) {
      setHistory((prev) => [
        ...doneTasks.map((t) => ({ uid: t.uid, result: t.result!, uploaded_at: new Date().toISOString() })),
        ...prev,
      ]);
    }

    if (error === 0) {
      message.success(`全部 ${total} 个文件上传成功`);
    } else if (success > 0) {
      message.warning(`上传完成：${success} 成功，${error} 失败`);
    } else {
      message.error(`全部 ${total} 个文件上传失败`);
    }

    // 刷新知识库状态、系列分组与文件历史
    loadStatus();
    loadSeries();
    loadFiles();
    return false; // 阻止 antd 默认上传
  };

  /** 取消上传（标记 abort，当前文件完成后停止后续） */
  const handleCancel = () => {
    cancelUpload();
    message.info('正在取消上传...');
  };

  /** 一键重试所有失败的文件（后端重启导致的 502/503 等临时错误）。 */
  const retryFailed = () => {
    const failedFiles = tasks.filter((t) => t.status === 'error').map((t) => t.file);
    if (failedFiles.length === 0) return;
    void (async () => {
      await startUpload(failedFiles, { overwrite: false, existingNames: new Set() });
      loadStatus();
      loadSeries();
      loadFiles();
    })();
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
            <Space>
              <Button
                icon={<RadarChartOutlined />}
                onClick={handleAnalyze}
                loading={analyzing}
              >
                知识库分析
              </Button>
              <Button
                icon={<ReloadOutlined />}
                onClick={() => { loadStatus(); loadFiles(); loadSeries(); }}
                loading={statusLoading}
                type="text"
              >
                刷新
              </Button>
            </Space>
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
        {/* 拖拽区：点击选文件；拖入文件/文件夹则递归展开上传 */}
        <div
          onClick={() => {
            if (!uploading && knowledgeStatus?.l3_enabled !== false) fileInputRef.current?.click();
          }}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          style={{
            border: `1px dashed ${dragOver ? '#1677ff' : '#d9d9d9'}`,
            borderRadius: 8,
            background: dragOver ? '#f0f5ff' : '#fafafa',
            padding: '32px 16px',
            textAlign: 'center',
            cursor: uploading || knowledgeStatus?.l3_enabled === false ? 'not-allowed' : 'pointer',
            transition: 'all 0.2s',
            opacity: uploading || knowledgeStatus?.l3_enabled === false ? 0.6 : 1,
          }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击选择文件，或拖拽文件/文件夹到此上传</p>
          <p className="ant-upload-hint">
            支持文件与文件夹混合拖入，文件夹会递归上传到最深层
            <br />
            支持格式：PDF、Word、TXT、Markdown、图片(JPG/PNG/WebP 等)
            <br />
            <span style={{ color: '#999', fontSize: 12 }}>
              单文件不超过 50MB，选择后会自动逐个上传并显示进度
            </span>
          </p>
        </div>

        <div style={{ marginTop: 12, textAlign: 'center' }}>
          <Button
            icon={<FolderOpenOutlined />}
            onClick={() => folderInputRef.current?.click()}
            disabled={uploading || knowledgeStatus?.l3_enabled === false}
          >
            选择文件夹
          </Button>
        </div>

        {/* 原生 input：多选文件 */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={SUPPORTED_EXTENSIONS.join(',')}
          style={{ display: 'none' }}
          onChange={(e) => {
            const filter = resumeFilterRef.current;
            resumeFilterRef.current = null;
            handleFilesSelected(Array.from(e.target.files || []), filter ?? undefined);
            e.target.value = '';
          }}
        />
        {/* 原生 input：选择文件夹（webkitdirectory 由浏览器递归展开所有层级） */}
        <input
          ref={folderInputRef}
          type="file"
          // @ts-expect-error webkitdirectory 为非标准属性
          webkitdirectory=""
          multiple
          style={{ display: 'none' }}
          onChange={(e) => {
            const filter = resumeFilterRef.current;
            resumeFilterRef.current = null;
            handleFilesSelected(Array.from(e.target.files || []), filter ?? undefined);
            e.target.value = '';
          }}
        />

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
              {!uploading && tasks.some((t) => t.status === 'error') && (
                <Button
                  size="small"
                  type="primary"
                  icon={<ReloadOutlined />}
                  onClick={retryFailed}
                >
                  重试失败的文件（{tasks.filter((t) => t.status === 'error').length}）
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
                  {r.category && (
                    <Tag color="purple" style={{ fontSize: 11 }}>
                      {r.category.l1} / {r.category.l2} / {r.category.l3}
                    </Tag>
                  )}
                  {r.series && (
                    <Tag color="gold" style={{ fontSize: 11 }}>📚 {r.series}</Tag>
                  )}
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

      {/* 系列文章分组 */}
      <Card
        size="small"
        title={`系列文章 (${seriesGroups.length})`}
        extra={
          <Button size="small" icon={<ReloadOutlined />} onClick={handleReSeries} loading={seriesLoading}>
            重新识别系列
          </Button>
        }
        loading={seriesLoading}
        style={{ marginTop: 16 }}
      >
        {seriesGroups.length === 0 ? (
          <Text type="secondary">
            暂无识别到的系列文章。文件名为「XX 第1篇/第2篇」「XX Part 1」「XX（上/中/下）」「XX 01/02」「第N天」等会被自动归组。
          </Text>
        ) : (
          <Tree
            treeData={buildSeriesTree(seriesGroups)}
            showLine
            showIcon
            blockNode
            style={{ background: 'transparent' }}
          />
        )}
      </Card>

      {/* 知识库分析结果 */}
      {analysis && (
        <Card size="small" title="知识库分析结果" style={{ marginTop: 16 }}>
          <Space wrap size={[8, 8]} style={{ marginBottom: 8 }}>
            {analysis.category_distribution.map((c) => (
              <Tag key={c.category} color="geekblue">
                {c.category} × {c.count}
              </Tag>
            ))}
          </Space>
          {analysis.overview && (
            <Alert type="info" message="概览" description={analysis.overview} style={{ marginBottom: 8 }} />
          )}
          <Text type="secondary">
            共 {analysis.document_files} 个文件、{analysis.total_entries} 个条目
          </Text>
        </Card>
      )}

      {/* 已上传文件历史（跨会话持久） */}
      <Card
        size="small"
        title={`已上传文件 (${uploadedFiles.length})`}
        loading={filesLoading}
        style={{ marginTop: 16 }}
      >
        {uploadedFiles.length === 0 ? (
          <Text type="secondary">暂无已上传文件</Text>
        ) : (
          <List
            size="small"
            dataSource={uploadedFiles}
            renderItem={(f) => (
              <List.Item
                key={f.file_name}
                actions={[
                  <Tag key="c" color="purple" style={{ fontSize: 11 }}>
                    {f.category ? `${f.category.l1}/${f.category.l2}/${f.category.l3}` : '未分类'}
                  </Tag>,
                  f.series && <Tag key="s" color="gold" style={{ fontSize: 11 }}>📚 {f.series}</Tag>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space size={8}>
                      {f.source === 'image' ? <FileImageOutlined /> : <FileOutlined />}
                      <Text>{f.file_name}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>{f.chunk_count} 块</Text>
                    </Space>
                  }
                  description={
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {f.uploaded_at ? new Date(f.uploaded_at).toLocaleString('zh-CN') : ''}
                    </Text>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>
    </div>
  );
};

export default Files;
