import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Copy, CheckCheck, Terminal } from 'lucide-react';

const REMARK_PLUGINS = [remarkGfm];

interface CodeBlockProps {
  language?: string;
  value: string;
  title?: string;
}

export const CodeBlock: React.FC<CodeBlockProps> = ({ language, value, title }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const displayLang = title || (language || 'text').toUpperCase();

  return (
    <div
      style={{
        margin: '8px 0',
        backgroundColor: '#090d13',
        border: '1px solid #30363d',
        borderRadius: '6px',
        overflow: 'hidden',
        fontFamily: 'var(--font-mono)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '4px 10px',
          backgroundColor: '#161b22',
          borderBottom: '1px solid #21262d',
          fontSize: '11px',
          color: '#8b949e',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Terminal size={12} color="#58a6ff" />
          <span style={{ fontWeight: 600, letterSpacing: '0.5px' }}>{displayLang}</span>
        </div>
        <button
          type="button"
          onClick={handleCopy}
          className="btn btn-sm"
          style={{
            padding: '1px 6px',
            height: '20px',
            fontSize: '10.5px',
            gap: '3px',
            backgroundColor: 'transparent',
            border: 'none',
            color: '#8b949e',
          }}
          title="复制代码"
        >
          {copied ? <CheckCheck size={11} color="#3fb950" /> : <Copy size={11} />}
          <span>{copied ? '已复制' : '复制'}</span>
        </button>
      </div>
      <pre
        style={{
          margin: 0,
          padding: '10px 12px',
          overflowX: 'auto',
          fontSize: '12px',
          lineHeight: '1.5',
          color: '#f0f6fc',
          fontFamily: 'inherit',
          backgroundColor: '#090d13',
        }}
      >
        <code>{value}</code>
      </pre>
    </div>
  );
};

interface MemoizedMarkdownProps {
  content: string;
  className?: string;
}

export const MemoizedMarkdown: React.FC<MemoizedMarkdownProps> = React.memo(
  ({ content, className }) => {
    return (
      <div className={`markdown-content ${className || ''}`.trim()}>
        <ReactMarkdown
          remarkPlugins={REMARK_PLUGINS}
          components={{
            code(props) {
              const { children, className, ...rest } = props;
              const match = /language-(\w+)/.exec(className || '');
              const isInline = !match && !String(children).includes('\n');
              if (isInline) {
                return (
                  <code
                    style={{
                      padding: '2px 5px',
                      backgroundColor: 'rgba(255, 255, 255, 0.08)',
                      borderRadius: '4px',
                      fontFamily: 'var(--font-mono)',
                      fontSize: '0.9em',
                      color: '#f0f6fc',
                    }}
                    {...rest}
                  >
                    {children}
                  </code>
                );
              }
              const codeVal = String(children).replace(/\n$/, '');
              return <CodeBlock language={match ? match[1] : undefined} value={codeVal} />;
            },
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    );
  },
  (prev, next) => prev.content === next.content && prev.className === next.className
);
