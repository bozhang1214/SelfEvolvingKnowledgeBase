import React from 'react';
import { Card, Row, Col, Typography, Space, Button, Steps, Tag, Divider } from 'antd';
import {
  MessageOutlined,
  FileOutlined,
  DatabaseOutlined,
  ReadOutlined,
  SolutionOutlined,
  SettingOutlined,
  ArrowRightOutlined,
  RocketOutlined,
  BookOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';

const { Title, Text, Paragraph } = Typography;

/** 功能卡片数据 */
const FEATURES = [
  {
    key: '/',
    icon: <MessageOutlined />,
    title: 'AI 对话',
    desc: '与 AI 助手多轮对话，具备短期/长期记忆、工具调用、RAG 检索增强，自动沉淀知识到长期知识库。',
    color: '#1677ff',
    tags: ['多轮记忆', '工具调用', 'RAG'],
  },
  {
    key: '/files',
    icon: <FileOutlined />,
    title: '文件管理',
    desc: '上传文档/图片，自动解析、智能分块、去重入库，自动三级分类与系列识别，支持历史查看。',
    color: '#52c41a',
    tags: ['自动分块', '去重', '分类'],
  },
  {
    key: '/knowledge',
    icon: <DatabaseOutlined />,
    title: '知识库',
    desc: '三级分类目录 + 向量检索，条目可手动重分类、搜索，是 RAG 问答的知识底座。',
    color: '#722ed1',
    tags: ['三级分类', '向量检索', '重分类'],
  },
  {
    key: '/news',
    icon: <ReadOutlined />,
    title: '科技资讯',
    desc: '每日 AI 资讯日报（多 RSS 源聚合 + LLM 结构化），支持周报/月报、关键词检索、深读。',
    color: '#fa8c16',
    tags: ['AI 日报', '周报月报', '检索'],
  },
  {
    key: '/job',
    icon: <SolutionOutlined />,
    title: '招聘分析',
    desc: '多源采集真实职位（9 家免登录渠道），批量市场分析 + 单职位深度分析（岗位/面试/简历/学习计划）。',
    color: '#eb2f96',
    tags: ['多源采集', '批量分析', '深度分析'],
  },
  {
    key: '/settings',
    icon: <SettingOutlined />,
    title: '设置',
    desc: '账号信息、登录态（90 天自动续租）、模型与系统配置。',
    color: '#8c8c8c',
    tags: ['账号', '配置'],
  },
];

const GUIDE_STEPS = [
  { title: '登录 / 注册', desc: '首次使用注册一个账号；登录态有效期 90 天，到期自动续租，接近免登录。' },
  { title: '上传资料', desc: '在「文件管理」上传你的文档/笔记，系统自动分块、去重并分类入库。' },
  { title: '开始提问', desc: '在「AI 对话」里提问，助手会检索你的知识库（RAG）给出有依据的回答。' },
  { title: '采集职位', desc: '在「招聘分析」一键采集职位并批量分析，定位目标岗位与差距。' },
  { title: '跟进资讯', desc: '每天看「科技资讯」的 AI 日报，保持对行业动态的感知。' },
];

const Home: React.FC = () => {
  const navigate = useNavigate();

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto', padding: '24px 16px' }}>
      {/* 欢迎区 */}
      <div style={{ textAlign: 'center', padding: '24px 0 8px' }}>
        <Title level={2} style={{ marginBottom: 8 }}>
          自迭代个人知识库 <Tag color="blue">SEKB</Tag>
        </Title>
        <Paragraph type="secondary" style={{ fontSize: 15, maxWidth: 720, margin: '0 auto' }}>
          一个会「越用越聪明」的个人知识库 + 多智能体（PDCA）AI 助手：上传资料自动入库分类，
          对话时检索增强，还能采集职位做招聘分析、自动生成 AI 资讯日报。
        </Paragraph>
      </div>

      {/* 功能说明 */}
      <Divider orientation="left">
        <Space><BookOutlined /> 功能说明</Space>
      </Divider>
      <Row gutter={[16, 16]}>
        {FEATURES.map((f) => (
          <Col xs={24} sm={12} lg={8} key={f.key}>
            <Card
              hoverable
              onClick={() => navigate(f.key)}
              style={{ height: '100%' }}
              styles={{ body: { display: 'flex', flexDirection: 'column', height: '100%' } }}
            >
              <Space align="start" style={{ marginBottom: 8 }}>
                <span style={{ fontSize: 28, color: f.color }}>{f.icon}</span>
                <Title level={5} style={{ margin: 0 }}>{f.title}</Title>
              </Space>
              <Paragraph type="secondary" style={{ flex: 1, minHeight: 66, marginBottom: 12 }}>
                {f.desc}
              </Paragraph>
              <Space wrap size={[4, 4]} style={{ marginBottom: 12 }}>
                {f.tags.map((t) => <Tag key={t} style={{ fontSize: 11 }}>{t}</Tag>)}
              </Space>
              <Button type="primary" ghost size="small" onClick={(e) => { e.stopPropagation(); navigate(f.key); }}>
                进入 <ArrowRightOutlined />
              </Button>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 快速上手引导 */}
      <Divider orientation="left">
        <Space><RocketOutlined /> 快速上手</Space>
      </Divider>
      <Card>
        <Steps
          direction="vertical"
          current={-1}
          items={GUIDE_STEPS.map((s) => ({
            title: <Text strong>{s.title}</Text>,
            description: <Text type="secondary">{s.desc}</Text>,
          }))}
        />
      </Card>
    </div>
  );
};

export default Home;
