import React, { useState } from 'react';
import { Copy, Check, RotateCw, ThumbsUp, ThumbsDown, GitFork } from 'lucide-react';
import type { ChatMessageItem as MessageItemType } from '../../types';
import { ReActStepCard } from './ReActStepCard';
import { MemoizedMarkdown } from '../common/MemoizedMarkdown';

interface ChatMessageItemProps {
  message: MessageItemType;
  onApprovalDecision?: (approvalId: string, approved: boolean, scope: 'once' | 'session') => void;
  onForkBranch?: (step?: number) => void;
  onRetry?: () => void;
}

const ChatMessageItemComponent: React.FC<ChatMessageItemProps> = ({
  message,
  onApprovalDecision,
  onForkBranch,
  onRetry,
}) => {
  const [copied, setCopied] = useState(false);
  const [liked, setLiked] = useState<boolean | null>(null);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // 1. 用户提问消息 (气泡居右，深灰圆角卡片)
  if (message.role === 'user') {
    return (
      <div className="user-msg-row">
        <div className="user-msg-bubble">
          {message.images && message.images.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '8px' }}>
              {message.images.map((imgUrl, i) => (
                <img
                  key={i}
                  src={imgUrl}
                  alt={`图片附件 ${i + 1}`}
                  style={{
                    maxWidth: '220px',
                    maxHeight: '160px',
                    borderRadius: '8px',
                    objectFit: 'cover',
                    border: '1px solid rgba(255, 255, 255, 0.15)',
                    cursor: 'pointer',
                  }}
                  onClick={() => window.open(imgUrl, '_blank')}
                  title="点击在新窗口查看原图"
                />
              ))}
            </div>
          )}
          {message.content}
        </div>
      </div>
    );
  }

  // 2. 聚合步骤时间线卡片 (ReActStepCard)
  if (message.type === 'react_step') {
    return (
      <div className="assistant-msg-row">
        <ReActStepCard
          stepData={message.stepData}
          onApprovalDecision={onApprovalDecision}
          onForkBranch={onForkBranch}
        />
      </div>
    );
  }

  // 3. 最终结论 / 综合回答 (Final Answer / Assistant)
  if (message.role === 'assistant') {
    return (
      <div className="assistant-msg-row">
        <div style={{ fontSize: '14px', lineHeight: '1.65', color: '#e5e5e5' }}>
          <MemoizedMarkdown content={message.content} />
        </div>

        {/* 底部小工具条（复制、重试、赞、踩、更多） */}
        <div className="assistant-actions-row">
          <button
            type="button"
            className="assistant-action-btn"
            title="复制内容"
            onClick={() => handleCopy(message.content)}
          >
            {copied ? <Check size={14} color="#3fb950" /> : <Copy size={14} />}
          </button>

          <button
            type="button"
            className="assistant-action-btn"
            title="重新生成"
            onClick={() => onRetry && onRetry()}
          >
            <RotateCw size={14} />
          </button>

          <button
            type="button"
            className="assistant-action-btn"
            title="好评"
            onClick={() => setLiked(liked === true ? null : true)}
            style={{ color: liked === true ? '#58a6ff' : undefined }}
          >
            <ThumbsUp size={14} />
          </button>

          <button
            type="button"
            className="assistant-action-btn"
            title="差评"
            onClick={() => setLiked(liked === false ? null : false)}
            style={{ color: liked === false ? '#da3633' : undefined }}
          >
            <ThumbsDown size={14} />
          </button>

          {onForkBranch && (
            <button
              type="button"
              className="assistant-action-btn"
              title="基于此结果切出新排障分支 (分叉)"
              onClick={() => onForkBranch()}
            >
              <GitFork size={14} />
            </button>
          )}
        </div>
      </div>
    );
  }

  // 4. 错误消息
  if (message.type === 'error') {
    return (
      <div
        className="assistant-msg-row"
        style={{
          padding: '10px 14px',
          borderRadius: '8px',
          backgroundColor: 'rgba(218, 54, 51, 0.15)',
          border: '1px solid rgba(218, 54, 51, 0.3)',
          color: '#ff7b72',
          fontSize: '13px',
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: '2px' }}>执行异常</div>
        <div>{message.content}</div>
      </div>
    );
  }

  return null;
};

export const ChatMessageItem = React.memo(ChatMessageItemComponent);
