import { describe, expect, it } from 'vitest';

import { buildSeriesTree } from '@/features/files/helpers';

describe('buildSeriesTree', () => {
  it('剥离系列名段：跨父目录前缀的同系列文件直接挂在系列下', () => {
    const groups = [
      {
        series: '3-技术文章汇总',
        category: [],
        count: 2,
        files: [
          { file_name: '3-技术文章汇总/第1篇：A.md', part: 1 },
          { file_name: '2-Agent全栈开发学习实践/3-技术文章汇总/第2篇：B.md', part: 2 },
        ],
      },
    ];
    const tree = buildSeriesTree(groups as never);
    expect(tree).toHaveLength(1);
    const root = tree[0];
    expect(root.title).toContain('3-技术文章汇总');
    const titles = root.children.map((c: { title: string }) => c.title);
    expect(titles).toContain('1. 第1篇：A.md');
    expect(titles).toContain('2. 第2篇：B.md');
    // 不应再出现「父目录 → 系列名」的冗余嵌套
    expect(root.children.every((c: { isLeaf?: boolean }) => c.isLeaf)).toBe(true);
  });

  it('保留系列内的子目录层级', () => {
    const groups = [
      {
        series: '2-Agent全栈开发学习实践',
        category: [],
        count: 1,
        files: [
          { file_name: '2-Agent全栈开发学习实践/2-s1-w1/d1-HTML 基础.md', part: 1 },
        ],
      },
    ];
    const tree = buildSeriesTree(groups as never);
    const root = tree[0];
    expect(root.children).toHaveLength(1);
    expect(root.children[0].title).toBe('2-s1-w1');
    expect(root.children[0].children[0].title).toBe('1. d1-HTML 基础.md');
  });
});
