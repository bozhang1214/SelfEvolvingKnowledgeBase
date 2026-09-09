import React, { useState } from 'react';
import { Button } from 'antd';
import { CheckOutlined, CopyOutlined } from '@ant-design/icons';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';
import type { Message } from '@/types/chat';

/** 代码块组件：带语言标签 + 复制按钮 */
export const CodeBlock: React.FC<{ language: string; code: string }> = ({ language, code }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div style={{ position: 'relative', maxWidth: '100%', overflowX: 'auto' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '4px 12px',
          background: '#f0f0f0',
          borderRadius: '4px 4px 0 0',
          fontSize: 12,
          color: '#666',
        }}
      >
        <span>{language}</span>
        <Button
          type="text"
          size="small"
          icon={copied ? <CheckOutlined style={{ color: '#52c41a' }} /> : <CopyOutlined />}
          onClick={handleCopy}
        >
          {copied ? '已复制' : '复制'}
        </Button>
      </div>
      <SyntaxHighlighter
        style={oneLight}
        language={language}
        PreTag="div"
        customStyle={{ margin: 0, borderRadius: '0 0 4px 4px' }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
};

/** 将多条消息拼接为 Markdown 文本（用于导出下载）。 */
export function buildConversationMarkdown(title: string, msgs: Message[]): string {
  const lines: string[] = [];
  lines.push(`# ${title || '对话'}`);
  lines.push('');
  lines.push(`> 导出自 SEKB 知识库对话 · ${new Date().toLocaleString('zh-CN')}`);
  lines.push('');
  msgs.forEach((m) => {
    const role = m.role === 'user' ? '🧑 用户' : '🤖 助手';
    const time = m.created_at ? new Date(m.created_at).toLocaleString('zh-CN') : '';
    lines.push(`## ${role}${time ? ` · ${time}` : ''}`);
    lines.push('');
    lines.push(m.content);
    lines.push('');
  });
  return lines.join('\n');
}

/** 拼接多条选中消息为纯文本（按 角色: 内容 格式）。 */
export function joinSelectedMessages(msgs: Message[]): string {
  return msgs
    .map((m) => `${m.role === 'user' ? '用户' : '助手'}: ${m.content}`)
    .join('\n\n');
}
