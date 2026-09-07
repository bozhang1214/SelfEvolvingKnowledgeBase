import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Card, Input, Button, Tabs, Typography, Space, Spin, Empty, message, Row, Col, Tag, List, Modal, Divider, Alert, Select, Pagination, Popover, Table,
} from 'antd';
import {
  ThunderboltOutlined, ClearOutlined, FileSearchOutlined, SearchOutlined,
  BarChartOutlined, DeleteOutlined, ReloadOutlined, UploadOutlined, HistoryOutlined, DownloadOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import {
  analyzeJob,
  fetchJobs,
  batchAnalyze,
  deleteBatchAnalysis,
  importJobFiles,
  listJobReports,
  getJobReport,
  deleteJobReport,
  refreshJob,
  saveJobCache,
  getLatestJobCache,
  getCachedBatchAnalysis,
  type JobAnalyzeResult,
  type JobMeta,
  type FetchedJob,
  type MarketReport,
  type ArchivedReportMeta,
} from '@/services/job';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

type SectionKey =
  | 'job_analysis'
  | 'knowledge_priority'
  | 'interview_qa'
  | 'gap_analysis'
  | 'resume_advice'
  | 'project_iteration'
  | 'job_strategy';

const SECTION_LABELS: Record<SectionKey, string> = {
  job_analysis: '岗位分析',
  knowledge_priority: '知识点优先级',
  interview_qa: '面试 Q&A',
  gap_analysis: '差距分析',
  resume_advice: '简历建议',
  project_iteration: '项目迭代',
  job_strategy: '求职策略',
};

const SECTION_ORDER: SectionKey[] = [
  'job_analysis',
  'knowledge_priority',
  'interview_qa',
  'gap_analysis',
  'resume_advice',
  'project_iteration',
  'job_strategy',
];

// 职位采集筛选选项
const CITY_OPTIONS = ['不限', '北京', '上海', '深圳', '杭州', '广州', '成都', '全国'];
const SALARY_OPTIONS = ['不限', '20K+', '30K+', '40K+', '50K+', '60K+', '80K+', '100K+'];

// 职位类型分类（按标题关键词）
const ROLE_RULES: Array<[string, string[]]> = [
  ['评测/质量', ['评测', '评估', 'Evaluation', '测试']],
  ['安全', ['安全']],
  ['算法/模型', ['算法', 'NLP', '大模型', 'LLM', '模型', 'AIOps']],
  ['架构师/Leader', ['架构师', 'Tech Lead', '技术负责人', 'Leader', '架构研发']],
  ['产品经理', ['产品经理', '产品', 'PM', '策略']],
  ['运营/策略', ['运营', '数据策略', '数据']],
  ['研发/工程', ['后端', '引擎', '研发工程师', '开发工程师', 'Harness', 'Infra', '基础设施', '编排', 'Orchestration', '应用']],
];

function classifyRole(title: string): string {
  const t = (title || '').toLowerCase();
  for (const [role, kws] of ROLE_RULES) {
    if (kws.some((k) => t.includes(k.toLowerCase()))) return role;
  }
  return '其他';
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function isEmptyValue(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  if (Array.isArray(v)) return v.length === 0;
  if (isPlainObject(v)) return Object.keys(v).length === 0;
  if (typeof v === 'string') return v.trim() === '';
  return false;
}

/** 递归渲染任意 JSON（对象/数组/标量），用于展示 LLM 结构化输出。 */
const JsonBlock: React.FC<{ data: unknown }> = ({ data }) => {
  if (data === null || data === undefined) return <Text type="secondary">—</Text>;

  if (Array.isArray(data)) {
    if (data.length === 0) return <Text type="secondary">（空）</Text>;
    return (
      <ul style={{ paddingLeft: 18, margin: 0 }}>
        {data.map((item, i) => (
          <li key={i} style={{ marginBottom: 4 }}>
            {isPlainObject(item) || Array.isArray(item) ? (
              <JsonBlock data={item} />
            ) : (
              <Text>{String(item)}</Text>
            )}
          </li>
        ))}
      </ul>
    );
  }

  if (isPlainObject(data)) {
    const entries = Object.entries(data);
    if (entries.length === 0) return <Text type="secondary">（空）</Text>;
    return (
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <tbody>
          {entries.map(([key, value]) => (
            <tr key={key} style={{ borderBottom: '1px solid #f0f0f0', verticalAlign: 'top' }}>
              <td
                style={{
                  padding: '6px 10px',
                  width: 180,
                  fontWeight: 600,
                  color: '#333',
                  background: '#fafafa',
                  whiteSpace: 'nowrap',
                }}
              >
                {key}
              </td>
              <td style={{ padding: '6px 10px' }}>
                {isPlainObject(value) || Array.isArray(value) ? (
                  <JsonBlock data={value} />
                ) : (
                  <Text>{String(value)}</Text>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  return <Text>{String(data)}</Text>;
};

/** 市场行情区块（批量职位分析.md 输出）：中文标签 + 易读布局，替代生硬的通用 JSON 表格。 */
const MarketSection: React.FC<{ data: Record<string, any> }> = ({ data }) => {
  const tracks = Array.isArray(data.track_heatmap) ? data.track_heatmap : [];
  const skill = data.skill_threshold || {};
  const must = Array.isArray(skill.must) ? skill.must : [];
  const nice = Array.isArray(skill.nice_to_have) ? skill.nice_to_have : [];
  const anchors = Array.isArray(data.salary_anchor) ? data.salary_anchor : [];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {tracks.length > 0 && (
        <div>
          <Paragraph strong style={{ marginBottom: 8 }}>赛道热力分布</Paragraph>
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {tracks.map((t: any, i: number) => (
              <div key={i}>
                <Text strong>{t.track || `赛道 ${i + 1}`}</Text>
                {t.job_count != null && <Tag color="blue" style={{ marginLeft: 8 }}>招聘 {t.job_count} 个</Tag>}
                {t.salary_signal && <Tag color="gold">{t.salary_signal}</Tag>}
                {t.comment && <Text type="secondary">　{t.comment}</Text>}
              </div>
            ))}
          </Space>
        </div>
      )}

      {(must.length > 0 || nice.length > 0) && (
        <div>
          <Paragraph strong style={{ marginBottom: 8 }}>技能栈：门槛线与加分项</Paragraph>
          {must.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <Text strong style={{ marginRight: 8, color: '#389e0d' }}>入场券</Text>
              <Space wrap size={[4, 4]}>
                {must.map((s: any, i: number) => <Tag key={i} color="green">{String(s)}</Tag>)}
              </Space>
            </div>
          )}
          {nice.length > 0 && (
            <div>
              <Text strong style={{ marginRight: 8, color: '#d46b08' }}>加钱项</Text>
              <Space wrap size={[4, 4]}>
                {nice.map((s: any, i: number) => <Tag key={i} color="orange">{String(s)}</Tag>)}
              </Space>
            </div>
          )}
        </div>
      )}

      {data.experience_reality && (
        <div>
          <Paragraph strong style={{ marginBottom: 4 }}>经验年限真实水位</Paragraph>
          <Paragraph style={{ marginBottom: 0 }}>{String(data.experience_reality)}</Paragraph>
        </div>
      )}

      {anchors.length > 0 && (
        <div>
          <Paragraph strong style={{ marginBottom: 8 }}>薪资锚点</Paragraph>
          <Space direction="vertical" size={4}>
            {anchors.map((a: any, i: number) => (
              <div key={i}>
                <Text strong>{a.scope || ''}</Text>
                {a.median && <Tag color="geekblue" style={{ marginLeft: 8 }}>中位 {String(a.median)}</Tag>}
                {a.note && <Text type="secondary">　{String(a.note)}</Text>}
              </div>
            ))}
          </Space>
        </div>
      )}

      {data.bottom_line && (
        <Alert type="warning" showIcon message="结论" description={String(data.bottom_line)} />
      )}
    </Space>
  );
};

/** 知识迭代区块（职位知识迭代.md 输出）：中文标签 + 易读布局。 */
const KnowledgeSection: React.FC<{ data: Record<string, any> }> = ({ data }) => {
  const foundation = Array.isArray(data.foundation) ? data.foundation : [];
  const highlights = Array.isArray(data.highlights) ? data.highlights : [];
  const skip = Array.isArray(data.skip) ? data.skip : [];
  const milestones = Array.isArray(data.milestones) ? data.milestones : [];
  const checkpoints = Array.isArray(data.checkpoints) ? data.checkpoints : [];
  const polish = Array.isArray(data.polish) ? data.polish : [];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {foundation.length > 0 && (
        <div>
          <Paragraph strong style={{ marginBottom: 8 }}>必保底盘</Paragraph>
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            {foundation.map((f: any, i: number) => (
              <div key={i}>
                <Text strong>{f.topic || ''}</Text>
                {f.estimated_days != null && <Tag color="red" style={{ marginLeft: 8 }}>{f.estimated_days} 天</Tag>}
                {f.why && <div><Text type="secondary">{String(f.why)}</Text></div>}
              </div>
            ))}
          </Space>
        </div>
      )}

      {highlights.length > 0 && (
        <div>
          <Paragraph strong style={{ marginBottom: 8 }}>亮点加分</Paragraph>
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            {highlights.map((f: any, i: number) => (
              <div key={i}>
                <Text strong>{f.topic || ''}</Text>
                {f.why && <div><Text type="secondary">{String(f.why)}</Text></div>}
              </div>
            ))}
          </Space>
        </div>
      )}

      {skip.length > 0 && (
        <div>
          <Paragraph strong style={{ marginBottom: 8 }}>暂时别看（市场噱头）</Paragraph>
          <Space wrap size={[4, 4]}>
            {skip.map((s: any, i: number) => <Tag key={i}>{String(s)}</Tag>)}
          </Space>
        </div>
      )}

      {milestones.length > 0 && (
        <div>
          <Paragraph strong style={{ marginBottom: 8 }}>里程碑路线图</Paragraph>
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {milestones.map((m: any, i: number) => (
              <div key={i} style={{ paddingLeft: 8, borderLeft: '3px solid #1677ff' }}>
                <Text strong>{m.period || ''}</Text>
                {m.goal && <Text>　{String(m.goal)}</Text>}
                {m.project && <div><Tag color="blue">{String(m.project)}</Tag></div>}
              </div>
            ))}
          </Space>
        </div>
      )}

      {checkpoints.length > 0 && (
        <div>
          <Paragraph strong style={{ marginBottom: 8 }}>面试试金石</Paragraph>
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            {checkpoints.map((c: any, i: number) => (
              <div key={i}>
                <Text strong>{c.period || ''}</Text>
                {Array.isArray(c.questions) && c.questions.length > 0 && (
                  <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                    {c.questions.map((q: any, j: number) => <li key={j}>{String(q)}</li>)}
                  </ul>
                )}
              </div>
            ))}
          </Space>
        </div>
      )}

      {polish.length > 0 && (
        <div>
          <Paragraph strong style={{ marginBottom: 8 }}>开源/博客镀金</Paragraph>
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            {polish.map((p: any, i: number) => (
              <div key={i}>
                {p.action && <Text strong>{String(p.action)}</Text>}
                {p.target && <Text>：{String(p.target)}</Text>}
              </div>
            ))}
          </Space>
        </div>
      )}
    </Space>
  );
};

/** 把 1~5 的重要度数字渲染成 ⭐ 串（安全截断）。 */
const renderStars = (n: unknown): string => '⭐'.repeat(Math.min(5, Math.max(0, Math.round(Number(n) || 0))));

/** 单职位分析各板块的语义化渲染（中文标签 + 易读布局，替代生硬 JSON 表格）。 */
const SectionRenderer: React.FC<{ section: SectionKey; data: Record<string, any> }> = ({ section, data }) => {
  switch (section) {
    case 'job_analysis': {
      const decoding = Array.isArray(data.jd_decoding) ? data.jd_decoding : [];
      const resp = Array.isArray(data.responsibilities) ? data.responsibilities : [];
      const kp = Array.isArray(data.knowledge_points) ? data.knowledge_points : [];
      const hard = Array.isArray(data.hard_requirements) ? data.hard_requirements : [];
      const iq = Array.isArray(data.interview_questions) ? data.interview_questions : [];
      return (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {data.positioning && (
            <div>
              <Paragraph strong style={{ marginBottom: 4 }}>岗位定位</Paragraph>
              <Paragraph style={{ marginBottom: 0 }}>{String(data.positioning)}</Paragraph>
            </div>
          )}
          {decoding.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>JD 潜台词翻译</Paragraph>
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                {decoding.map((d: any, i: number) => (
                  <div key={i}>
                    <Text type="secondary" delete>{String(d.surface)}</Text>
                    <Text>　→　</Text>
                    <Text>{String(d.meaning)}</Text>
                  </div>
                ))}
              </Space>
            </div>
          )}
          {resp.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>核心职责</Paragraph>
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {resp.map((r: any, i: number) => <li key={i}>{String(r)}</li>)}
              </ul>
            </div>
          )}
          {kp.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>核心知识点</Paragraph>
              <Space wrap size={[4, 4]}>
                {kp.map((k: any, i: number) => <Tag key={i} color="blue">{String(k)}</Tag>)}
              </Space>
            </div>
          )}
          {hard.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>硬性门槛</Paragraph>
              <Space wrap size={[4, 4]}>
                {hard.map((k: any, i: number) => <Tag key={i} color="red">{String(k)}</Tag>)}
              </Space>
            </div>
          )}
          {iq.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>关键面试问题</Paragraph>
              <ol style={{ margin: 0, paddingLeft: 20 }}>
                {iq.map((q: any, i: number) => <li key={i}>{String(q)}</li>)}
              </ol>
            </div>
          )}
          {data.conclusion && <Alert type="info" showIcon message="分析结论" description={String(data.conclusion)} />}
        </Space>
      );
    }

    case 'knowledge_priority': {
      const p = data.priorities || {};
      const groups = [
        { key: 'p1_core', label: '第一优先级 · 必考核心' },
        { key: 'p2_framework', label: '第二优先级 · 框架与工程' },
        { key: 'p3_advanced', label: '第三优先级 · 工程化与进阶' },
      ];
      return (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {groups.map((g) => {
            const items = Array.isArray(p[g.key]) ? p[g.key] : [];
            if (items.length === 0) return null;
            return (
              <div key={g.key}>
                <Paragraph strong style={{ marginBottom: 8 }}>{g.label}</Paragraph>
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  {items.map((it: any, i: number) => (
                    <div key={i}>
                      <Text strong>{String(it.topic)}</Text>
                      {it.importance != null && <Text> {renderStars(it.importance)}</Text>}
                      {it.how_examined && <div><Text type="secondary">考察方式：{String(it.how_examined)}</Text></div>}
                      {it.why && <div><Text type="secondary">为什么重要：{String(it.why)}</Text></div>}
                    </div>
                  ))}
                </Space>
              </div>
            );
          })}
          {data.summary && <Alert type="info" showIcon message="优先级总结" description={String(data.summary)} />}
        </Space>
      );
    }

    case 'interview_qa': {
      const modules = Array.isArray(data.modules) ? data.modules : [];
      return (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {modules.map((m: any, i: number) => {
            const qs = Array.isArray(m.questions) ? m.questions : [];
            return (
              <div key={i}>
                {m.module && <Paragraph strong style={{ marginBottom: 8 }}>{String(m.module)}</Paragraph>}
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  {qs.map((q: any, j: number) => (
                    <div key={j} style={{ padding: '10px 12px', background: '#fafafa', borderRadius: 8 }}>
                      <Text strong>{String(q.question)}</Text>
                      {q.source && <div><Tag color="blue" style={{ marginTop: 4 }}>{String(q.source)}</Tag></div>}
                      {q.what_examiner_wants && <div style={{ marginTop: 4 }}><Text type="secondary">面试官想听：{String(q.what_examiner_wants)}</Text></div>}
                      {Array.isArray(q.answer_framework) && q.answer_framework.length > 0 && (
                        <ul style={{ margin: '6px 0 0', paddingLeft: 20 }}>
                          {q.answer_framework.map((a: any, k: number) => <li key={k}>{String(a)}</li>)}
                        </ul>
                      )}
                      {q.bonus && <div style={{ marginTop: 4 }}><Text type="success">加分项：{String(q.bonus)}</Text></div>}
                    </div>
                  ))}
                </Space>
              </div>
            );
          })}
        </Space>
      );
    }

    case 'gap_analysis': {
      const hits = Array.isArray(data.hits) ? data.hits : [];
      const partial = Array.isArray(data.partial) ? data.partial : [];
      const gaps = Array.isArray(data.gaps) ? data.gaps : [];
      return (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {hits.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8, color: '#389e0d' }}>命中（已有能力）</Paragraph>
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                {hits.map((h: any, i: number) => (
                  <div key={i}>
                    <Tag color="green">{String(h.requirement)}</Tag>
                    {h.evidence && <Text type="secondary">　{String(h.evidence)}</Text>}
                  </div>
                ))}
              </Space>
            </div>
          )}
          {partial.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8, color: '#d46b08' }}>部分命中（可迁移，深度不足）</Paragraph>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {partial.map((h: any, i: number) => (
                  <div key={i}>
                    <Tag color="orange">{String(h.requirement)}</Tag>
                    {h.priority && <Tag>{String(h.priority)}</Tag>}
                    {h.current && <div><Text type="secondary">现状：{String(h.current)}</Text></div>}
                    {h.action && <div><Text>补法：{String(h.action)}</Text></div>}
                  </div>
                ))}
              </Space>
            </div>
          )}
          {gaps.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8, color: '#cf1322' }}>缺口（需补齐）</Paragraph>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {gaps.map((g: any, i: number) => (
                  <div key={i}>
                    <Tag color="red">{String(g.requirement)}</Tag>
                    {g.priority && <Tag>{String(g.priority)}</Tag>}
                    {g.evidence && <div><Text type="secondary">依据：{String(g.evidence)}</Text></div>}
                    {g.action && <div><Text>补法：{String(g.action)}</Text></div>}
                  </div>
                ))}
              </Space>
            </div>
          )}
          {data.gap_verdict && <Alert type="warning" showIcon message="差距结论" description={String(data.gap_verdict)} />}
        </Space>
      );
    }

    case 'resume_advice': {
      const emphasize = Array.isArray(data.emphasize) ? data.emphasize : [];
      const align = Array.isArray(data.align_keywords) ? data.align_keywords : [];
      const de = Array.isArray(data.de_emphasize) ? data.de_emphasize : [];
      const rewrites = Array.isArray(data.rewrites) ? data.rewrites : [];
      const variant = data.variant_adjustments || {};
      const bulletOrder = Array.isArray(variant.bullet_order) ? variant.bullet_order : [];
      const skillOrder = Array.isArray(variant.skill_order) ? variant.skill_order : [];
      return (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {data.company_type && <Tag color="purple">目标公司类型：{String(data.company_type)}</Tag>}
          {emphasize.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>要突出强调</Paragraph>
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {emphasize.map((e: any, i: number) => (
                  <li key={i} style={{ marginBottom: 6 }}>
                    <Text strong>{String(e.item)}</Text>
                    {e.reason && <Text type="secondary">（{String(e.reason)}）</Text>}
                    {e.suggestion && <div><Text>{String(e.suggestion)}</Text></div>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {align.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>关键词对齐（不造假）</Paragraph>
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                {align.map((a: any, i: number) => (
                  <div key={i}>
                    <Text type="secondary" delete>{String(a.original)}</Text>
                    <Text>　→　</Text>
                    <Text strong>{String(a.rewrite)}</Text>
                  </div>
                ))}
              </Space>
            </div>
          )}
          {rewrites.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>具体改写示例</Paragraph>
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                {rewrites.map((a: any, i: number) => (
                  <div key={i}>
                    <Text type="secondary" delete>{String(a.original)}</Text>
                    <Text>　→　</Text>
                    <Text strong>{String(a.rewrite)}</Text>
                  </div>
                ))}
              </Space>
            </div>
          )}
          {de.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>弱化/删除</Paragraph>
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                {de.map((d: any, i: number) => (
                  <div key={i}>
                    <Text type="secondary" delete>{String(d.item)}</Text>
                    {d.reason && <Text type="secondary">　（{String(d.reason)}）</Text>}
                  </div>
                ))}
              </Space>
            </div>
          )}
          {(variant.intro_first_line || bulletOrder.length > 0 || skillOrder.length > 0) && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>简历变体调整（按公司类型）</Paragraph>
              {variant.intro_first_line && <Paragraph style={{ marginBottom: 4 }}>个人简介首句：{String(variant.intro_first_line)}</Paragraph>}
              {bulletOrder.length > 0 && <div style={{ marginBottom: 4 }}>项目排序：{bulletOrder.join(' → ')}</div>}
              {skillOrder.length > 0 && <div>技能排序：{skillOrder.join(' → ')}</div>}
            </div>
          )}
        </Space>
      );
    }

    case 'project_iteration': {
      const existing = Array.isArray(data.existing) ? data.existing : [];
      const fresh = Array.isArray(data.new) ? data.new : [];
      const renderProject = (p: any, i: number) => (
        <div key={i} style={{ padding: '8px 12px', background: '#fafafa', borderRadius: 8 }}>
          {p.gap && <div><Text type="secondary">对应缺口：{String(p.gap)}</Text></div>}
          {p.idea && <div><Text strong>{String(p.idea)}</Text></div>}
          {Array.isArray(p.tech_stack) && p.tech_stack.length > 0 && (
            <div style={{ marginTop: 4 }}>
              {p.tech_stack.map((t: any, j: number) => <Tag key={j} color="blue" style={{ marginRight: 4 }}>{String(t)}</Tag>)}
            </div>
          )}
          {p.resume_value && <div style={{ marginTop: 4 }}><Text>简历表述：{String(p.resume_value)}</Text></div>}
          {p.effort && <Tag style={{ marginTop: 4 }}>投入：{String(p.effort)}</Tag>}
        </div>
      );
      return (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {existing.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>现有项目迭代</Paragraph>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {existing.map(renderProject)}
              </Space>
            </div>
          )}
          {fresh.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>新项目建议</Paragraph>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {fresh.map(renderProject)}
              </Space>
            </div>
          )}
          {data.summary && <Alert type="info" showIcon message="迭代总结" description={String(data.summary)} />}
        </Space>
      );
    }

    case 'job_strategy': {
      const insights = Array.isArray(data.company_insights) ? data.company_insights : [];
      const tier = data.tier_strategy || {};
      const practice = Array.isArray(tier.practice) ? tier.practice : [];
      const main = Array.isArray(tier.main_attack) ? tier.main_attack : [];
      const backup = Array.isArray(tier.backup) ? tier.backup : [];
      const ranking = Array.isArray(data.match_ranking) ? data.match_ranking : [];
      const prep = Array.isArray(data.prep_focus) ? data.prep_focus : [];
      const renderTier = (items: any[], label: string, color: string) => items.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <Tag color={color}>{label}</Tag>
          <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>
            {items.map((t: any, i: number) => (
              <li key={i}><Text strong>{String(t.position)}</Text>{t.reason && <Text type="secondary">　{String(t.reason)}</Text>}</li>
            ))}
          </ul>
        </div>
      );
      return (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {insights.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>公司岗位特点</Paragraph>
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                {insights.map((c: any, i: number) => (
                  <div key={i}>
                    <Text strong>{String(c.company)}</Text>
                    {c.characteristic && <Text type="secondary">　{String(c.characteristic)}</Text>}
                  </div>
                ))}
              </Space>
            </div>
          )}
          {(practice.length > 0 || main.length > 0 || backup.length > 0) && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>投递分层策略</Paragraph>
              {renderTier(practice, '练手层', 'default')}
              {renderTier(main, '主攻层', 'gold')}
              {renderTier(backup, '保底层', 'blue')}
            </div>
          )}
          {ranking.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>匹配度排序</Paragraph>
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                {ranking.map((r: any, i: number) => (
                  <div key={i}>
                    <Text strong>{i + 1}. {String(r.position)}</Text>
                    {r.match_score != null && <Tag color={matchScoreColor(Number(r.match_score))} style={{ marginLeft: 8 }}>{r.match_score} 分</Tag>}
                    {r.recommendation && <div><Text type="secondary">{String(r.recommendation)}</Text></div>}
                  </div>
                ))}
              </Space>
            </div>
          )}
          {prep.length > 0 && (
            <div>
              <Paragraph strong style={{ marginBottom: 8 }}>准备重点</Paragraph>
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {prep.map((p: any, i: number) => <li key={i}>{String(p)}</li>)}
              </ul>
            </div>
          )}
        </Space>
      );
    }

    default:
      return <JsonBlock data={data} />;
  }
};

/** 职位详情悬浮弹窗（悬停展示标题/公司/薪资/城市 + JD 全文）。 */const JobDetailPopover: React.FC<{ job: FetchedJob; children: React.ReactNode }> = ({ job, children }) => (
  <Popover
    title={
      <Space direction="vertical" size={0}>
        <Text strong>{job.title || '（无标题）'}</Text>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {[job.company, job.salary, job.city, job.source].filter(Boolean).join(' · ')}
        </Text>
      </Space>
    }
    content={
      <div style={{ maxWidth: 460, maxHeight: 320, overflow: 'auto' }}>
        {job.jd_text ? (
          <Text style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{job.jd_text}</Text>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>该职位暂无 JD 详情</Text>
        )}
      </div>
    }
    trigger="hover"
    placement="right"
  >
    <span>{children}</span>
  </Popover>
);

/** 职位名（超链接 + 悬浮详情）：点击打开原文，悬停看 JD。 */
const JobTitle: React.FC<{ job: FetchedJob }> = ({ job }) => (
  <JobDetailPopover job={job}>
    {job.job_url ? (
      <a href={job.job_url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
        <Text strong>{job.title}</Text>
      </a>
    ) : (
      <Text strong>{job.title}</Text>
    )}
  </JobDetailPopover>
);

/** 从分析结果里提取匹配度评分（0~100），无则返回 null。 */function getMatchScore(result: JobAnalyzeResult | null): number | null {
  const ranking = (result as { job_strategy?: { match_ranking?: Array<{ match_score?: number }> } })?.job_strategy?.match_ranking;
  if (Array.isArray(ranking) && ranking.length > 0 && typeof ranking[0]?.match_score === 'number') {
    return ranking[0].match_score;
  }
  return null;
}

function matchScoreColor(score: number): string {
  if (score >= 80) return 'green';
  if (score >= 60) return 'blue';
  return 'orange';
}

const Job: React.FC = () => {
  const [jdText, setJdText] = useState('');
  const [meta, setMeta] = useState<JobMeta>({});
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<JobAnalyzeResult | null>(null);
  const [activeTab, setActiveTab] = useState<SectionKey>('job_analysis');

  // 职位采集（多源）
  const [fetchKeyword, setFetchKeyword] = useState('');
  const [fetchCity, setFetchCity] = useState('不限');
  const [fetchSalary, setFetchSalary] = useState('不限');
  const [fetching, setFetching] = useState(false);
  const [fetchedJobs, setFetchedJobs] = useState<FetchedJob[]>([]);
  // 复选框选中的职位（多选批量分析）
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  // 收集列表表格分页（受控）
  const [tablePage, setTablePage] = useState(1);
  const [tablePageSize, setTablePageSize] = useState(20);

  // 批量职位分析
  const [batchAnalyzing, setBatchAnalyzing] = useState(false);
  const [marketReport, setMarketReport] = useState<MarketReport | null>(null);
  // 分析模式：职位收集 / 批量分析 / 单职位分析 / 历史报告
  const [analysisMode, setAnalysisMode] = useState<'collect' | 'batch' | 'single' | 'history'>('collect');

  // 批量上传职位文件
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 历史报告
  const [reports, setReports] = useState<ArchivedReportMeta[]>([]);
  const [reportsLoading, setReportsLoading] = useState(false);
  const [reportDetail, setReportDetail] = useState<{ id: string; type: string; title: string; created_at: string; markdown: string; report: Record<string, any> | null } | null>(null);
  // 历史报告子 tab：批量分析 / 单职位分析
  const [historyTab, setHistoryTab] = useState<'batch' | 'single'>('batch');
  // 最近一次缓存的批量分析报告（用于「批量分析」tab 默认展示）
  const [cachedMarketReport, setCachedMarketReport] = useState<MarketReport | null>(null);

  // 挂载时：回填最近一次缓存的职位 + 筛选选项，并预取缓存的批量报告
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const cache = await getLatestJobCache();
        if (!alive || !cache.cached || !cache.jobs?.length) return;
        setFetchKeyword(cache.keyword || '');
        setFetchCity(cache.city || '不限');
        const salaryLabel = cache.min_salary_k > 0 && SALARY_OPTIONS.includes(`${cache.min_salary_k}K+`)
          ? `${cache.min_salary_k}K+`
          : '不限';
        setFetchSalary(salaryLabel);
        setFetchedJobs(cache.jobs);
      } catch {
        // 静默失败
      }
      try {
        const { report } = await getCachedBatchAnalysis();
        if (alive && report) setCachedMarketReport(report);
      } catch {
        // 静默失败
      }
    })();
    return () => { alive = false; };
  }, []);

  // 进入「批量分析」tab：仅当缓存报告的职位列表与当前采集列表严格一致时才默认展示（避免展示过时报告引起误解）
  useEffect(() => {
    if (analysisMode !== 'batch' || marketReport || !cachedMarketReport) return;
    const rk = (j: FetchedJob) => j.job_id || `${j.title}-${j.company}`;
    const reportKeys = new Set((cachedMarketReport.jobs || []).map(rk));
    const fetchKeys = new Set(fetchedJobs.map(rk));
    if (reportKeys.size === 0) return;
    const same = reportKeys.size === fetchKeys.size && [...reportKeys].every((k) => fetchKeys.has(k));
    if (same) setMarketReport(cachedMarketReport);
  }, [analysisMode, cachedMarketReport, fetchedJobs, marketReport]);

  // 进入「历史报告」tab 时加载存档报告
  useEffect(() => {
    if (analysisMode === 'history') {
      loadReports();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisMode]);

  const handleImportClick = () => fileInputRef.current?.click();

  const handleImportFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;
    setImporting(true);
    try {
      const { jobs } = await importJobFiles(files);
      setFetchedJobs((prev) => [...jobs, ...prev]);
      message.success(`已导入 ${jobs.length} 个职位，可在下方列表查看`);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '导入职位文件失败');
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const loadReports = async () => {
    setReportsLoading(true);
    try {
      const { reports: list } = await listJobReports();
      setReports(list);
    } catch {
      // 静默失败
    } finally {
      setReportsLoading(false);
    }
  };

  const openReport = async (id: string) => {
    try {
      const detail = await getJobReport(id);
      setReportDetail(detail);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '读取报告失败');
    }
  };

  const handleDeleteReportItem = async (id: string) => {
    try {
      await deleteJobReport(id);
      message.success('报告已删除');
      loadReports();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败');
    }
  };

  const handleBatchAnalyze = async (force = false, jobs?: FetchedJob[]) => {
    const targetJobs = jobs ?? fetchedJobs;
    if (targetJobs.length === 0) {
      message.warning('请先在「职位收集」tab 收集职位');
      return;
    }
    setBatchAnalyzing(true);
    try {
      const { report } = await batchAnalyze({ jobs: targetJobs, force });
      setMarketReport(report);
      message.success('批量分析完成');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '批量分析失败');
    } finally {
      setBatchAnalyzing(false);
    }
  };

  const handleDeleteReport = async () => {
    try {
      await deleteBatchAnalysis();
      setMarketReport(null);
      setCachedMarketReport(null);
      message.success('分析报告已删除，可重新分析');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败');
    }
  };

  const handleFetch = async () => {
    const kw = fetchKeyword.trim();
    if (!kw) {
      message.warning('请输入采集关键词');
      return;
    }
    // 薪资「不限」→ 0；否则解析「30K+」→ 30
    const salaryK = fetchSalary === '不限' ? 0 : parseInt(fetchSalary, 10) || 0;
    setFetching(true);
    setFetchedJobs([]);
    try {
      const data = await fetchJobs({
        keyword: kw,
        city: fetchCity === '不限' ? '' : fetchCity,
        min_salary_k: salaryK,
      });
      setFetchedJobs(data.jobs);
      if (data.jobs.length === 0) {
        message.info('未采集到职位（接口可能被限流或关键词无结果）');
      } else {
        message.success(`采集到 ${data.count} 个职位`);
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '职位采集失败');
    } finally {
      setFetching(false);
    }
  };

  /** 点击采集到的职位：有 JD 文本则填入分析框并切到「单职位分析」，否则跳转原链接 */
  const handlePickJob = (job: FetchedJob) => {
    setJdText(job.jd_text || '');
    setMeta({ company: job.company, position: job.title, city: job.city, salary: job.salary });
    setAnalysisMode('single');
    if (job.jd_text && job.jd_text.trim()) {
      message.success('已填入职位描述，请点击「开始分析」');
    } else {
      message.info('该职位暂无 JD 文本，请粘贴 JD 后点击「开始分析」');
    }
  };

  /** 职位唯一 key（job_id 缺失时用标题+公司兜底）。 */
  const jobKey = (j: FetchedJob) => j.job_id || `${j.title}-${j.company}`;

  /** 把当前列表同步回缓存（删除/刷新后调用）。 */
  const syncJobCache = async (jobs: FetchedJob[]) => {
    const salaryK = fetchSalary === '不限' ? 0 : parseInt(fetchSalary, 10) || 0;
    try {
      await saveJobCache({
        keyword: fetchKeyword.trim() || 'Agent',
        city: fetchCity === '不限' ? '' : fetchCity,
        min_salary_k: salaryK,
        jobs,
      });
    } catch {
      // 缓存同步失败不阻断主流程
    }
  };

  /** 刷新单个职位的 JD（重新抓详情页）并同步缓存。 */
  const handleRefreshJob = async (job: FetchedJob) => {
    try {
      const { jd_text } = await refreshJob(job.job_url || '', job.source || '');
      const key = jobKey(job);
      const newJobs = fetchedJobs.map((j) => (jobKey(j) === key ? { ...j, jd_text: jd_text || j.jd_text } : j));
      setFetchedJobs(newJobs);
      await syncJobCache(newJobs);
      message.success(jd_text ? '已刷新职位 JD' : '未获取到 JD（该职位可能已下线或非猎聘源）');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '刷新失败');
    }
  };

  /** 删除单个职位并同步缓存。 */
  const handleDeleteJob = async (job: FetchedJob) => {
    const key = jobKey(job);
    const newJobs = fetchedJobs.filter((j) => jobKey(j) !== key);
    setFetchedJobs(newJobs);
    setSelectedRowKeys((prev) => prev.filter((k) => String(k) !== key));
    await syncJobCache(newJobs);
    message.success('已删除该职位');
  };

  // 是否已全选全部职位（跨页全选：不受分页「每页最多 100 条」限制）
  const allSelected = fetchedJobs.length > 0 && selectedRowKeys.length >= fetchedJobs.length;

  /** 全选全部职位 / 取消全选（一键跨页全选，避免表头全选只能选当前页）。 */
  const handleToggleSelectAll = () => {
    if (allSelected) {
      setSelectedRowKeys([]);
    } else {
      setSelectedRowKeys(fetchedJobs.map(jobKey));
    }
  };

  const handleAnalyze = async () => {
    const text = jdText.trim();
    if (!text) {
      message.warning('请先粘贴职位描述（JD）');
      return;
    }
    const jobMeta = Object.fromEntries(
      Object.entries(meta).filter(([, v]) => (v as string)?.trim() !== ''),
    ) as JobMeta;

    setAnalyzing(true);
    setResult(null);
    try {
      const data = await analyzeJob({ jd_text: text, job_meta: jobMeta });
      setResult(data);
      setActiveTab('job_analysis');
      message.success(data.cached ? '已加载缓存的分析结果（14 天内）' : '分析完成');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '分析失败，请稍后重试');
    } finally {
      setAnalyzing(false);
    }
  };

  const handleClear = () => {
    setJdText('');
    setMeta({});
    setResult(null);
  };

  const batchReports = useMemo(() => reports.filter((r) => r.type === 'batch'), [reports]);
  const singleReports = useMemo(() => reports.filter((r) => r.type === 'single'), [reports]);
  const historyReports = historyTab === 'batch' ? batchReports : singleReports;

  const tabItems = useMemo(
    () =>
      SECTION_ORDER.map((key) => {
        const data = result ? result[key] : undefined;
        const empty = result === null || isEmptyValue(data);
        return {
          key,
          label: (
            <span>
              {SECTION_LABELS[key]}
              {result !== null && empty && <Tag color="default" style={{ marginLeft: 4 }}>无结果</Tag>}
            </span>
          ),
          children: empty ? (
            <Empty description="该步骤未产出结果（可能是 LLM 降级或输入不足）" />
          ) : (
            <SectionRenderer section={key} data={data as Record<string, any>} />
          ),
        };
      }),
    [result],
  );

  return (
    <div style={{ padding: 24, overflow: 'auto', background: '#fff', minHeight: '100%' }}>
      <Tabs
        activeKey={analysisMode}
        onChange={(k) => setAnalysisMode(k as 'collect' | 'batch' | 'single' | 'history')}
        style={{ maxWidth: 1080, margin: '0 auto' }}
        items={[
          {
            key: 'collect',
            label: <span><SearchOutlined /> 职位收集</span>,
            children: (
              <>
                <Card
                  title={
                    <Space>
                      <SearchOutlined />
                      <Text strong>职位收集</Text>
                      <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
                        设置筛选条件，收集并浏览真实职位（9 家免登录渠道）
                      </Text>
                    </Space>
                  }
                  extra={
                    <Button
                      size="small"
                      type="link"
                      icon={<BarChartOutlined />}
                      onClick={() => {
                        setAnalysisMode('batch');
                        handleBatchAnalyze(false);
                      }}
                    >
                      去批量分析 →
                    </Button>
                  }
                  style={{ marginBottom: 16 }}
                >
        <Row gutter={12} align="middle">
          <Col xs={24} sm={4}>
            <Select
              value={fetchCity}
              onChange={setFetchCity}
              style={{ width: '100%' }}
              options={CITY_OPTIONS.map((c) => ({ value: c, label: c === '不限' ? '工作地：不限' : `工作地：${c}` }))}
            />
          </Col>
          <Col xs={24} sm={9}>
            <Input
              placeholder="关键字，多个用空格分隔（如：Agent 大模型）"
              value={fetchKeyword}
              onChange={(e) => setFetchKeyword(e.target.value)}
              onPressEnter={handleFetch}
            />
          </Col>
          <Col xs={24} sm={5}>
            <Select
              value={fetchSalary}
              onChange={setFetchSalary}
              style={{ width: '100%' }}
              options={SALARY_OPTIONS.map((s) => ({ value: s, label: s === '不限' ? '薪资：不限' : `薪资：${s}` }))}
            />
          </Col>
          <Col xs={24} sm={6}>
              <Button type="primary" icon={<SearchOutlined />} loading={fetching} onClick={handleFetch} block>
                收集职位
              </Button>
          </Col>
        </Row>
        <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
          说明：工作地/薪资默认「不限」；关键字支持按空格拼接多个关键词（如「Agent 大模型」），空则用默认「Agent」。
        </Paragraph>
        <Space style={{ marginTop: 12 }} align="center">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".txt,.md,.markdown,.pdf,.docx"
            style={{ display: 'none' }}
            onChange={handleImportFiles}
          />
          <Button icon={<UploadOutlined />} loading={importing} onClick={handleImportClick}>
            批量上传职位文件
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            支持 .txt/.md/.docx/.pdf，每个文件一个职位（文件名作标题、正文作 JD），导入后补充到下方列表（不覆盖）
          </Text>
        </Space>
        {fetchedJobs.length > 0 && (
          <>
            <Space style={{ marginTop: 12, width: '100%', justifyContent: 'space-between' }}>
              <Space size={12}>
                <Text type="secondary" style={{ fontSize: 12 }}>共 {fetchedJobs.length} 条职位</Text>
                <Button size="small" type="link" onClick={handleToggleSelectAll}>
                  {allSelected ? `取消全选（已选 ${fetchedJobs.length} 条）` : `全选全部 ${fetchedJobs.length} 条`}
                </Button>
              </Space>
              <Button
                type="primary"
                size="small"
                icon={<BarChartOutlined />}
                disabled={selectedRowKeys.length < 1}
                loading={batchAnalyzing}
                onClick={() => {
                  const keys = selectedRowKeys.map(String);
                  const selected = fetchedJobs.filter((j) => keys.includes(j.job_id || `${j.title}-${j.company}`));
                  setAnalysisMode('batch');
                  handleBatchAnalyze(false, selected);
                }}
              >
                前往批量分析{selectedRowKeys.length > 0 ? `（${selectedRowKeys.length}）` : ''}
              </Button>
            </Space>
            <Table
              size="small"
              style={{ marginTop: 8 }}
              rowKey={(j) => j.job_id || `${j.title}-${j.company}`}
              dataSource={fetchedJobs.slice((tablePage - 1) * tablePageSize, tablePage * tablePageSize)}
              rowSelection={{
                selectedRowKeys,
                onChange: setSelectedRowKeys,
                // 跨页保留选中：否则翻页时 antd 会清掉不在当前页的选中项，
                // 导致「全选全部」或逐页勾选在翻页后丢失
                preserveSelectedRowKeys: true,
              }}
              pagination={false}
              columns={[
                {
                  title: '职位名',
                  dataIndex: 'title',
                  key: 'title',
                  render: (_, job) => (
                    <Space size={6}>
                      <JobTitle job={job} />
                      {job.salary && <Tag color="blue" style={{ fontSize: 11 }}>{job.salary}</Tag>}
                    </Space>
                  ),
                },
                {
                  title: '职位类型',
                  key: 'role',
                  width: 120,
                  render: (_, job) => <Tag style={{ fontSize: 11 }}>{classifyRole(job.title)}</Tag>,
                  filters: [...new Set(fetchedJobs.map((j) => classifyRole(j.title)))].map((r) => ({ text: r, value: r })),
                  onFilter: (value, job) => classifyRole(job.title) === value,
                },
                {
                  title: '数据来源',
                  dataIndex: 'source',
                  key: 'source',
                  width: 100,
                  render: (s) => <Tag style={{ fontSize: 11 }}>{s || '-'}</Tag>,
                  filters: [...new Set(fetchedJobs.map((j) => j.source || '未知'))].map((s) => ({ text: s, value: s })),
                  onFilter: (value, job) => (job.source || '未知') === value,
                },
                {
                  title: '公司',
                  dataIndex: 'company',
                  key: 'company',
                  render: (c) => <Text strong>{c || '-'}</Text>,
                  filters: [...new Set(fetchedJobs.map((j) => j.company || '未知'))].map((c) => ({ text: c, value: c })),
                  onFilter: (value, job) => (job.company || '未知') === value,
                  filterSearch: true,
                },
                {
                  title: '操作',
                  key: 'action',
                  width: 180,
                  render: (_, job) => (
                    <Space size={0}>
                      <Button size="small" type="link" onClick={() => handlePickJob(job)}>填入分析</Button>
                      <Button size="small" type="link" icon={<ReloadOutlined />} onClick={() => handleRefreshJob(job)}>刷新</Button>
                      <Button size="small" type="link" danger icon={<DeleteOutlined />} onClick={() => handleDeleteJob(job)}>删除</Button>
                    </Space>
                  ),
                },
              ]}
            />
            <div style={{ textAlign: 'right', marginTop: 12 }}>
              <Pagination
                current={tablePage}
                pageSize={tablePageSize}
                total={fetchedJobs.length}
                size="small"
                showSizeChanger
                pageSizeOptions={[10, 20, 50, 100]}
                showTotal={(t) => `共 ${t} 条`}
                onChange={(page, size) => {
                  setTablePage(page);
                  setTablePageSize(size);
                }}
              />
            </div>
          </>
        )}
                </Card>
              </>
            ),
          },
          {
            key: 'batch',
            label: <span><BarChartOutlined /> 批量分析</span>,
            children: (
              <>
                {/* 批量市场分析 */}
                <Card
                  title={
          <Space>
            <BarChartOutlined />
            <Text strong>批量职位分析</Text>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
              对「职位收集」里已收集的职位（多个）做整体市场分析
            </Text>
          </Space>
        }
        extra={
          <Space>
            {marketReport && (
              <>
                <Button size="small" icon={<ReloadOutlined />} loading={batchAnalyzing} onClick={() => handleBatchAnalyze(true)}>
                  重新分析
                </Button>
                <Button size="small" danger icon={<DeleteOutlined />} onClick={handleDeleteReport}>
                  删除报告
                </Button>
              </>
            )}
            <Button type="primary" size="small" icon={<BarChartOutlined />} loading={batchAnalyzing} onClick={() => handleBatchAnalyze(false)}>
              一键分析
            </Button>
          </Space>
        }
        style={{ maxWidth: 1080, margin: '0 auto 16px' }}
      >
        {batchAnalyzing && !marketReport ? (
          <Spin tip="正在采集职位并生成市场分析报告，约需 30~60 秒…">
            <div style={{ minHeight: 120 }} />
          </Spin>
        ) : marketReport ? (
          <div>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={`分析于 ${marketReport.analyzed_at?.replace('T', ' ').slice(0, 19) || ''} · 共 ${marketReport.job_count} 个职位 · 关键词「${marketReport.keyword}」· ${marketReport.city}`}
            />
            <Row gutter={16}>
              <Col xs={24} sm={8}>
                <Paragraph strong style={{ marginBottom: 4 }}>公司分布</Paragraph>
                <Space wrap size={[4, 4]}>
                  {marketReport.stats?.company_distribution?.map((c) => (
                    <Tag key={c.name} color="blue">{c.name} × {c.count}</Tag>
                  ))}
                </Space>
              </Col>
              <Col xs={24} sm={8}>
                <Paragraph strong style={{ marginBottom: 4 }}>职位方向</Paragraph>
                <Space wrap size={[4, 4]}>
                  {marketReport.stats?.role_distribution?.map((c) => (
                    <Tag key={c.name} color="geekblue">{c.name} × {c.count}</Tag>
                  ))}
                </Space>
              </Col>
              <Col xs={24} sm={8}>
                <Paragraph strong style={{ marginBottom: 4 }}>热点关键词</Paragraph>
                <Space wrap size={[4, 4]}>
                  {marketReport.stats?.hot_keywords?.map((c) => (
                    <Tag key={c.keyword} color="purple">{c.keyword} × {c.count}</Tag>
                  ))}
                </Space>
              </Col>
            </Row>

            {marketReport.market && !isEmptyValue(marketReport.market) && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <Paragraph strong style={{ marginBottom: 8 }}>市场行情</Paragraph>
                <MarketSection data={marketReport.market as Record<string, any>} />
              </>
            )}

            {marketReport.knowledge_iteration && !isEmptyValue(marketReport.knowledge_iteration) && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <Paragraph strong style={{ marginBottom: 8 }}>知识迭代</Paragraph>
                <KnowledgeSection data={marketReport.knowledge_iteration as Record<string, any>} />
              </>
            )}
          </div>
        ) : (
          <Empty description="点击「一键分析」生成市场分析报告" />
        )}
                </Card>
              </>
            ),
          },
          {
            key: 'single',
            label: <span><FileSearchOutlined /> 单职位分析</span>,
            children: (
              <>
                <Card
                  title={
                    <Space>
                      <FileSearchOutlined />
                      <Text strong>单职位分析</Text>
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
              粘贴职位 JD，自动产出岗位定位 / 知识点 / 面试题 / 差距 / 简历建议 / 求职策略
            </Text>
          </Space>
        }
        style={{ maxWidth: 1080, margin: '0 auto' }}
      >
        <TextArea
          value={jdText}
          onChange={(e) => setJdText(e.target.value)}
          rows={9}
          placeholder={'在此粘贴职位描述（JD）原文，例如：\n【岗位】AI 应用工程师\n【职责】负责 Agent 应用落地，设计多 Agent 编排……\n【要求】本科及以上，熟悉 LangGraph、RAG、Python/FastAPI……'}
          style={{ marginBottom: 12 }}
        />
        <Row gutter={12} style={{ marginBottom: 16 }}>
          <Col xs={12} sm={6}>
            <Input
              placeholder="公司（可选）"
              value={meta.company || ''}
              onChange={(e) => setMeta({ ...meta, company: e.target.value })}
              allowClear
            />
          </Col>
          <Col xs={12} sm={6}>
            <Input
              placeholder="职位名（可选）"
              value={meta.position || ''}
              onChange={(e) => setMeta({ ...meta, position: e.target.value })}
              allowClear
            />
          </Col>
          <Col xs={12} sm={6}>
            <Input
              placeholder="城市（可选）"
              value={meta.city || ''}
              onChange={(e) => setMeta({ ...meta, city: e.target.value })}
              allowClear
            />
          </Col>
          <Col xs={12} sm={6}>
            <Input
              placeholder="薪资（可选）"
              value={meta.salary || ''}
              onChange={(e) => setMeta({ ...meta, salary: e.target.value })}
              allowClear
            />
          </Col>
        </Row>
        <Space>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={analyzing}
            onClick={handleAnalyze}
          >
            开始分析
          </Button>
          <Button icon={<ClearOutlined />} onClick={handleClear} disabled={analyzing}>
            清空
          </Button>
        </Space>
      </Card>

      <Card
        style={{ maxWidth: 1080, margin: '16px auto 0' }}
        styles={{ body: { paddingTop: 12 } }}
      >
        {result === null ? (
          <Spin spinning={analyzing} tip="分析中，需依次调用多个 LLM 步骤，请稍候…">
            <div style={{ minHeight: 160 }}>
              <Empty description={analyzing ? '正在分析职位…' : '粘贴 JD 后点击「开始分析」'} />
            </div>
          </Spin>
        ) : (
          <>
            <Space style={{ marginBottom: 8 }} align="center">
              {getMatchScore(result) !== null && (
                <Tag color={matchScoreColor(getMatchScore(result) as number)} style={{ fontSize: 13, padding: '2px 12px' }}>
                  匹配度 {getMatchScore(result)} 分
                </Tag>
              )}
              <Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 0 }}>
                岗位定位：{result.job_analysis?.positioning || result.job_analysis?.position || '（未产出）'}
              </Paragraph>
              {(result as { cached?: boolean }).cached && (
                <Tag color="green" style={{ fontSize: 11 }}>14 天缓存</Tag>
              )}
            </Space>
            <Tabs
              activeKey={activeTab}
              onChange={(k) => setActiveTab(k as SectionKey)}
              items={tabItems}
            />
          </>
        )}
                </Card>
              </>
            ),
          },
          {
            key: 'history',
            label: <span><HistoryOutlined /> 历史报告</span>,
            children: (
              <>
                <Card
                  title={
                    <Space>
                      <HistoryOutlined />
                      <Text strong>历史报告</Text>
                      <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
                        批量分析 / 单职位分析的结果自动存档，可随时回顾
                      </Text>
                    </Space>
                  }
                  extra={<Button size="small" icon={<ReloadOutlined />} onClick={loadReports}>刷新</Button>}
                  style={{ marginBottom: 16 }}
                >
                  <Tabs
                    size="small"
                    activeKey={historyTab}
                    onChange={(k) => setHistoryTab(k as 'batch' | 'single')}
                    items={[
                      { key: 'batch', label: `批量分析（${batchReports.length}）` },
                      { key: 'single', label: `单职位分析（${singleReports.length}）` },
                    ]}
                    style={{ marginBottom: 8 }}
                  />
                  <Spin spinning={reportsLoading}>
                    {historyReports.length === 0 ? (
                      <Empty description="暂无存档报告，做一次批量/单职位分析后会自动存档" />
                    ) : (
                      <List
                        size="small"
                        dataSource={historyReports}
                        renderItem={(r) => (
                          <List.Item
                            key={r.id}
                            actions={[
                              <Button key="view" size="small" type="link" onClick={() => openReport(r.id)}>查看</Button>,
                              <Button key="del" size="small" type="link" danger onClick={() => handleDeleteReportItem(r.id)}>删除</Button>,
                            ]}
                          >
                            <List.Item.Meta
                              title={<Text strong>{r.title}</Text>}
                              description={<Text type="secondary" style={{ fontSize: 12 }}>{r.created_at?.replace('T', ' ').slice(0, 19)}</Text>}
                            />
                          </List.Item>
                        )}
                      />
                    )}
                  </Spin>
                </Card>
              </>
            ),
          },
        ]}
      />

      {/* 历史报告详情弹窗 */}
      <Modal
        title={reportDetail?.title || '报告详情'}
        open={!!reportDetail}
        onCancel={() => setReportDetail(null)}
        footer={
          reportDetail ? (
            <Button
              icon={<DownloadOutlined />}
              onClick={() => {
                const blob = new Blob([reportDetail.markdown], { type: 'text/markdown;charset=utf-8' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${reportDetail.title || '报告'}.md`;
                a.click();
                URL.revokeObjectURL(url);
              }}
            >
              下载 Markdown
            </Button>
          ) : null
        }
        width={860}
      >
        {reportDetail ? (
          <div style={{ maxHeight: '70vh', overflow: 'auto' }}>
            {reportDetail.report ? (
              reportDetail.type === 'batch' ? (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Row gutter={16}>
                    <Col xs={24} sm={8}>
                      <Paragraph strong style={{ marginBottom: 4 }}>公司分布</Paragraph>
                      <Space wrap size={[4, 4]}>
                        {(reportDetail.report.stats?.company_distribution || []).map((c: any) => (
                          <Tag key={c.name} color="blue">{c.name} × {c.count}</Tag>
                        ))}
                      </Space>
                    </Col>
                    <Col xs={24} sm={8}>
                      <Paragraph strong style={{ marginBottom: 4 }}>职位方向</Paragraph>
                      <Space wrap size={[4, 4]}>
                        {(reportDetail.report.stats?.role_distribution || []).map((c: any) => (
                          <Tag key={c.name} color="geekblue">{c.name} × {c.count}</Tag>
                        ))}
                      </Space>
                    </Col>
                    <Col xs={24} sm={8}>
                      <Paragraph strong style={{ marginBottom: 4 }}>热点关键词</Paragraph>
                      <Space wrap size={[4, 4]}>
                        {(reportDetail.report.stats?.hot_keywords || []).map((c: any) => (
                          <Tag key={c.keyword} color="purple">{c.keyword} × {c.count}</Tag>
                        ))}
                      </Space>
                    </Col>
                  </Row>
                  {reportDetail.report.market && !isEmptyValue(reportDetail.report.market) && (
                    <>
                      <Divider style={{ margin: 0 }} />
                      <Paragraph strong style={{ marginBottom: 0 }}>市场行情</Paragraph>
                      <MarketSection data={reportDetail.report.market as Record<string, any>} />
                    </>
                  )}
                  {reportDetail.report.knowledge_iteration && !isEmptyValue(reportDetail.report.knowledge_iteration) && (
                    <>
                      <Divider style={{ margin: 0 }} />
                      <Paragraph strong style={{ marginBottom: 0 }}>知识迭代</Paragraph>
                      <KnowledgeSection data={reportDetail.report.knowledge_iteration as Record<string, any>} />
                    </>
                  )}
                </Space>
              ) : (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  {SECTION_ORDER.map((key) => {
                    const data = reportDetail.report?.[key];
                    if (isEmptyValue(data)) return null;
                    return (
                      <div key={key}>
                        <Divider style={{ margin: '4px 0' }} />
                        <Paragraph strong style={{ marginBottom: 8 }}>{SECTION_LABELS[key]}</Paragraph>
                        <SectionRenderer section={key} data={data as Record<string, any>} />
                      </div>
                    );
                  })}
                </Space>
              )
            ) : (
              <ReactMarkdown>{reportDetail.markdown}</ReactMarkdown>
            )}
          </div>
        ) : null}
      </Modal>
    </div>
  );
};

export default Job;
