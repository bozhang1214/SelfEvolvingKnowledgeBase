import React, { useMemo, useState } from 'react';
import { Alert, Empty, Input, List, Modal, Popover, Space, Tag, Typography } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import type { FetchedJob, JobAnalyzeResult } from '@/services/job';

const { Text, Paragraph } = Typography;

export type SectionKey =
  | 'job_analysis'
  | 'knowledge_priority'
  | 'interview_qa'
  | 'gap_analysis'
  | 'resume_advice'
  | 'project_iteration'
  | 'job_strategy';

export const SECTION_LABELS: Record<SectionKey, string> = {
  job_analysis: '岗位分析',
  knowledge_priority: '知识点优先级',
  interview_qa: '面试 Q&A',
  gap_analysis: '差距分析',
  resume_advice: '简历建议',
  project_iteration: '项目迭代',
  job_strategy: '求职策略',
};

export const SECTION_ORDER: SectionKey[] = [
  'job_analysis',
  'knowledge_priority',
  'interview_qa',
  'gap_analysis',
  'resume_advice',
  'project_iteration',
  'job_strategy',
];

// 职位采集筛选选项
export const CITY_OPTIONS = ['不限', '北京', '上海', '深圳', '杭州', '广州', '成都', '全国'];
export const SALARY_OPTIONS = ['不限', '20K+', '30K+', '40K+', '50K+', '60K+', '80K+', '100K+'];

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

export function classifyRole(title: string): string {
  const t = (title || '').toLowerCase();
  for (const [role, kws] of ROLE_RULES) {
    if (kws.some((k) => t.includes(k.toLowerCase()))) return role;
  }
  return '其他';
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

export function isEmptyValue(v: unknown): boolean {
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
export const MarketSection: React.FC<{
  data: Record<string, any>;
  /** 传入后，「招聘 N 个」标签可点击，回传赛道名供上层弹出对应职位列表 */
  onShowJobs?: (trackName?: string) => void;
}> = ({ data, onShowJobs }) => {
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
                {t.job_count != null && (
                  <Tag
                    color="blue"
                    style={{ marginLeft: 8, cursor: onShowJobs ? 'pointer' : undefined }}
                    onClick={onShowJobs ? () => onShowJobs(t.track) : undefined}
                  >
                    招聘 {t.job_count} 个{onShowJobs ? ' ›' : ''}
                  </Tag>
                )}
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
export const KnowledgeSection: React.FC<{ data: Record<string, any> }> = ({ data }) => {
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
export const SectionRenderer: React.FC<{ section: SectionKey; data: Record<string, any> }> = ({ section, data }) => {
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
export const JobTitle: React.FC<{ job: FetchedJob }> = ({ job }) => (
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

/** 从分析结果里提取匹配度评分（0~100），无则返回 null。 */export function getMatchScore(result: JobAnalyzeResult | null): number | null {
  const ranking = (result as { job_strategy?: { match_ranking?: Array<{ match_score?: number }> } })?.job_strategy?.match_ranking;
  if (Array.isArray(ranking) && ranking.length > 0 && typeof ranking[0]?.match_score === 'number') {
    return ranking[0].match_score;
  }
  return null;
}

export function matchScoreColor(score: number): string {
  if (score >= 80) return 'green';
  if (score >= 60) return 'blue';
  return 'orange';
}

/**
 * 按赛道名粗筛职位（best-effort）：从赛道名提取关键词，匹配职位名 / 公司。
 * 无匹配时**返回全部**，避免「点了赛道却弹出空列表」的体验落差。
 */
export function filterJobsByTrack(jobs: FetchedJob[], trackName?: string): FetchedJob[] {
  if (!trackName) return jobs;
  const keywords = trackName
    .split(/[/、／\s|｜]+/)
    .map((k) => k.trim())
    .filter((k) => k.length >= 2);
  if (keywords.length === 0) return jobs;
  const matched = jobs.filter((j) => {
    const hay = `${j.title || ''} ${j.company || ''}`;
    return keywords.some((k) => hay.includes(k));
  });
  return matched.length > 0 ? matched : jobs;
}

/**
 * 职位列表弹框（可复用）：批量分析 / 历史报告 / 赛道热力共用。
 * 职位名复用 `JobTitle`——点击跳原文、悬浮看 JD 详情；顶部支持关键词过滤。
 */
export const JobListModal: React.FC<{
  open: boolean;
  onClose: () => void;
  jobs: FetchedJob[];
  /** 弹框标题（默认「职位列表（N）」） */
  title?: string;
}> = ({ open, onClose, jobs, title }) => {
  const [q, setQ] = useState('');
  const shown = useMemo(() => {
    const kw = q.trim().toLowerCase();
    if (!kw) return jobs;
    return jobs.filter((j) =>
      `${j.title || ''} ${j.company || ''} ${j.city || ''} ${j.salary || ''}`
        .toLowerCase()
        .includes(kw),
    );
  }, [jobs, q]);

  return (
    <Modal
      title={title || `职位列表（${jobs.length}）`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={920}
    >
      <Input
        allowClear
        prefix={<SearchOutlined />}
        placeholder="搜索职位名 / 公司 / 城市"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ marginBottom: 12 }}
      />
      {shown.length === 0 ? (
        <Empty description="没有匹配的职位" />
      ) : (
        <div style={{ maxHeight: '60vh', overflow: 'auto' }}>
          <List
            size="small"
            dataSource={shown}
            renderItem={(j, idx) => (
              <List.Item key={j.job_id || j.job_url || idx}>
                <Space wrap size={8}>
                  <JobTitle job={j} />
                  {j.company && <Text type="secondary">{j.company}</Text>}
                  {j.salary && <Tag color="gold">{j.salary}</Tag>}
                  {j.city && <Tag>{j.city}</Tag>}
                </Space>
              </List.Item>
            )}
          />
        </div>
      )}
    </Modal>
  );
};
