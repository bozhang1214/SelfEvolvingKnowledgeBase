import { FileTextOutlined, FolderOutlined, ReadOutlined } from '@ant-design/icons';
import type { SeriesGroup } from '@/services/file';

// 支持的文件扩展名（与后端 FileProcessor 对齐）
export const SUPPORTED_EXTENSIONS = [
  '.pdf', '.docx', '.txt', '.md',
  '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif',
];
export const IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'];

/** 从 antd Upload 的 file 对象或原生 File 中提取支持的文件 */
export function filterSupportedFiles(files: File[]): File[] {
  return files.filter((f) => {
    const ext = f.name.substring(f.name.lastIndexOf('.')).toLowerCase();
    return SUPPORTED_EXTENSIONS.includes(ext);
  });
}

/** 递归展平 DataTransferItemList（支持文件夹拖拽） */
export async function flattenItems(items: DataTransferItemList): Promise<File[]> {
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
export function readEntry(entry: FileSystemEntry, path: string): Promise<File[]> {
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
export function buildSeriesTree(groups: SeriesGroup[]): any[] {
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
