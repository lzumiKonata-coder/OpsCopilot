import React, { useRef, useState } from 'react';
import { ArrowRight } from 'lucide-react';

interface PendingApprovalDialogProps {
  command?: string;
  riskLevel?: string;
  reason?: string;
  onExecute: () => void;
  onCancel: () => void;
  onAdjustDirection: (supplementText: string) => void;
}

export const PendingApprovalDialog: React.FC<PendingApprovalDialogProps> = ({
  onExecute,
  onCancel,
  onAdjustDirection,
}) => {
  const [supplement, setSupplement] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const handleAdjustClick = () => {
    if (supplement.trim()) {
      onAdjustDirection(supplement.trim());
    } else {
      inputRef.current?.focus();
    }
  };

  const handleSend = () => {
    if (!supplement.trim()) return;
    onAdjustDirection(supplement.trim());
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="bottom-approval-container">
      {/* 标题 */}
      <div className="bottom-approval-title">AI 等待你的回复</div>

      {/* 选项按钮列表 */}
      <div className="approval-options-list">
        <button
          type="button"
          className="approval-action-btn"
          onClick={onExecute}
        >
          <span>开始执行</span>
          <ArrowRight size={15} className="approval-action-arrow" />
        </button>

        <button
          type="button"
          className="approval-action-btn"
          onClick={handleAdjustClick}
        >
          <span>调整思路</span>
          <ArrowRight size={15} className="approval-action-arrow" />
        </button>

        <button
          type="button"
          className="approval-action-btn"
          onClick={onCancel}
        >
          <span>取消</span>
          <ArrowRight size={15} className="approval-action-arrow" />
        </button>
      </div>

      {/* 底部输入补充内容行 */}
      <div className="approval-input-row">
        <input
          ref={inputRef}
          type="text"
          className="approval-text-input"
          placeholder="输入补充内容，Enter 发送"
          value={supplement}
          onChange={(e) => setSupplement(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          type="button"
          className="approval-send-btn"
          onClick={handleSend}
          disabled={!supplement.trim()}
        >
          发送
        </button>
      </div>
    </div>
  );
};
