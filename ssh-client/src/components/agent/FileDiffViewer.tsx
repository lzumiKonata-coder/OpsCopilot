import React, { useState } from 'react';
import { Copy, CheckCheck, ChevronDown, ChevronRight, FileEdit, Plus, Minus } from 'lucide-react';
import type { FileDiffData } from '../../types';

interface FileDiffViewerProps {
  diffData: FileDiffData;
}

export const FileDiffViewer: React.FC<FileDiffViewerProps> = ({ diffData }) => {
  const [isExpanded, setIsExpanded] = useState(true);
  const [copied, setCopied] = useState(false);

  const getCopyContent = (): string => {
    if (diffData.diff) return diffData.diff;
    if (diffData.replacement) return diffData.replacement;
    if (diffData.content) return diffData.content;
    return diffData.target || '';
  };

  const handleCopy = () => {
    const text = getCopyContent();
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const isEdit = diffData.toolName === 'edit_remote_file' || (diffData.target && diffData.replacement);
  const isWrite = diffData.toolName === 'write_remote_file' || (diffData.content && !diffData.target);
  const isUnifiedDiff = Boolean(diffData.diff);

  const badgeText = isEdit
    ? '局部修改'
    : isWrite
    ? '文件写入'
    : isUnifiedDiff
    ? '文件对比'
    : '文件变更';

  const badgeColor = isEdit ? '#58a6ff' : isWrite ? '#3fb950' : '#bc8cff';
  const badgeBg = isEdit
    ? 'rgba(56, 139, 253, 0.15)'
    : isWrite
    ? 'rgba(63, 185, 80, 0.15)'
    : 'rgba(188, 140, 255, 0.15)';

  return (
    <div
      style={{
        margin: '8px 0',
        backgroundColor: '#090d13',
        border: '1px solid #30363d',
        borderRadius: '6px',
        overflow: 'hidden',
        fontFamily: 'var(--font-mono)',
        fontSize: '12px',
      }}
    >
      {/* 头部文件路径栏 */}
      <div
        onClick={() => setIsExpanded(!isExpanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '6px 10px',
          backgroundColor: '#161b22',
          borderBottom: isExpanded ? '1px solid #21262d' : 'none',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', overflow: 'hidden' }}>
          <FileEdit size={13} color={badgeColor} />
          <span
            style={{
              padding: '1px 6px',
              fontSize: '10.5px',
              borderRadius: '3px',
              color: badgeColor,
              backgroundColor: badgeBg,
              fontWeight: 600,
            }}
          >
            {badgeText}
          </span>
          <span
            style={{
              color: '#e6edf3',
              fontWeight: 600,
              fontSize: '11.5px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={diffData.path}
          >
            {diffData.path || '目标文件'}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <button
            type="button"
            className="btn btn-sm"
            onClick={(e) => {
              e.stopPropagation();
              handleCopy();
            }}
            style={{ padding: '1px 6px', height: '22px', fontSize: '11px', gap: '3px' }}
            title="复制文件变更内容"
          >
            {copied ? <CheckCheck size={11} color="#3fb950" /> : <Copy size={11} />}
            <span>{copied ? '已复制' : '复制'}</span>
          </button>
          <span style={{ color: '#8b949e' }}>
            {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </span>
        </div>
      </div>

      {/* 展开的 Diff 区域 */}
      {isExpanded && (
        <div style={{ maxHeight: '320px', overflowY: 'auto', backgroundColor: '#090d13' }}>
          {/* 1. 局部精确查找替换视图 (Target vs Replacement) */}
          {isEdit && (
            <div>
              {diffData.target && (
                <div style={{ borderBottom: '1px solid #21262d' }}>
                  <div
                    style={{
                      padding: '3px 8px',
                      backgroundColor: 'rgba(248, 81, 73, 0.1)',
                      color: '#f85149',
                      fontSize: '11px',
                      fontWeight: 600,
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                    }}
                  >
                    <Minus size={11} />
                    <span>原匹配内容 (已移除):</span>
                  </div>
                  <pre
                    style={{
                      margin: 0,
                      padding: '6px 10px',
                      color: '#ff7b72',
                      backgroundColor: 'rgba(248, 81, 73, 0.05)',
                      fontFamily: 'inherit',
                      fontSize: '11.5px',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-all',
                    }}
                  >
                    {diffData.target}
                  </pre>
                </div>
              )}

              {diffData.replacement && (
                <div>
                  <div
                    style={{
                      padding: '3px 8px',
                      backgroundColor: 'rgba(63, 185, 80, 0.1)',
                      color: '#3fb950',
                      fontSize: '11px',
                      fontWeight: 600,
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                    }}
                  >
                    <Plus size={11} />
                    <span>新替换内容 (已写入):</span>
                  </div>
                  <pre
                    style={{
                      margin: 0,
                      padding: '6px 10px',
                      color: '#56d364',
                      backgroundColor: 'rgba(63, 185, 80, 0.05)',
                      fontFamily: 'inherit',
                      fontSize: '11.5px',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-all',
                    }}
                  >
                    {diffData.replacement}
                  </pre>
                </div>
              )}
            </div>
          )}

          {/* 2. 整文件写入视图 (Content) */}
          {isWrite && diffData.content && (
            <div>
              <div
                style={{
                  padding: '3px 8px',
                  backgroundColor: 'rgba(63, 185, 80, 0.1)',
                  color: '#3fb950',
                  fontSize: '11px',
                  fontWeight: 600,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}
              >
                <Plus size={11} />
                <span>写入内容 ({diffData.content.length} 字节):</span>
              </div>
              <pre
                style={{
                  margin: 0,
                  padding: '6px 10px',
                  color: '#e6edf3',
                  backgroundColor: 'rgba(255, 255, 255, 0.02)',
                  fontFamily: 'inherit',
                  fontSize: '11.5px',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}
              >
                {diffData.content}
              </pre>
            </div>
          )}

          {/* 3. Unified Diff 逐行渲染 */}
          {isUnifiedDiff && diffData.diff && (
            <div style={{ padding: '4px 0' }}>
              {diffData.diff.split('\n').map((line, idx) => {
                let lineBg = 'transparent';
                let textColor = '#c9d1d9';
                let gutterPrefix = ' ';

                if (line.startsWith('+') && !line.startsWith('+++')) {
                  lineBg = 'rgba(63, 185, 80, 0.12)';
                  textColor = '#56d364';
                  gutterPrefix = '+';
                } else if (line.startsWith('-') && !line.startsWith('---')) {
                  lineBg = 'rgba(248, 81, 73, 0.12)';
                  textColor = '#ff7b72';
                  gutterPrefix = '-';
                } else if (line.startsWith('@@')) {
                  lineBg = 'rgba(56, 139, 253, 0.12)';
                  textColor = '#58a6ff';
                  gutterPrefix = '@';
                }

                return (
                  <div
                    key={idx}
                    style={{
                      display: 'flex',
                      backgroundColor: lineBg,
                      color: textColor,
                      padding: '1px 8px',
                      lineHeight: '1.4',
                      fontSize: '11.5px',
                    }}
                  >
                    <span
                      style={{
                        width: '32px',
                        color: '#6e7681',
                        userSelect: 'none',
                        textAlign: 'right',
                        paddingRight: '8px',
                      }}
                    >
                      {idx + 1}
                    </span>
                    <span style={{ width: '14px', userSelect: 'none', fontWeight: 600 }}>
                      {gutterPrefix}
                    </span>
                    <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', flex: 1 }}>
                      {line.startsWith('+') || line.startsWith('-') ? line.slice(1) : line}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
