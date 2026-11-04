import React, { useState } from 'react';
import { X } from 'lucide-react';

interface CommandApprovalDialogProps {
  command: string;
  onApprove: (scope: 'once' | 'session') => void;
  onReject: () => void;
}

export const CommandApprovalDialog: React.FC<CommandApprovalDialogProps> = ({
  command,
  onApprove,
  onReject,
}) => {
  const [selectedScope, setSelectedScope] = useState<'once' | 'session'>('once');

  return (
    <div className="command-approval-container">
      {/* 头部标题与关闭按钮 */}
      <div className="command-approval-header">
        <div className="command-approval-title">AI 请求执行命令，是否允许？</div>
        <button
          type="button"
          className="command-approval-close-btn"
          onClick={onReject}
          title="关闭"
        >
          <X size={18} />
        </button>
      </div>

      {/* 待执行命令行展示区 */}
      <div className="command-approval-code-box">
        <code>{command}</code>
      </div>

      {/* 授权范围选项 */}
      <div className="command-approval-options">
        <div
          className={`command-approval-option-item ${selectedScope === 'once' ? 'selected' : ''}`}
          onClick={() => setSelectedScope('once')}
        >
          <div className="command-approval-badge">1</div>
          <div className="command-approval-option-content">
            <div className="command-approval-option-title">仅本次允许</div>
            <div className="command-approval-option-desc">
              仅执行当前这一次操作，后续仍会继续询问。
            </div>
          </div>
        </div>

        <div
          className={`command-approval-option-item ${selectedScope === 'session' ? 'selected' : ''}`}
          onClick={() => setSelectedScope('session')}
        >
          <div className="command-approval-badge">2</div>
          <div className="command-approval-option-content">
            <div className="command-approval-option-title">本会话允许类似命令</div>
            <div className="command-approval-option-desc">
              当前会话内，对相同资源的同类操作将自动放行；刷新或切换会话后失效。
            </div>
          </div>
        </div>
      </div>

      {/* 底部按钮组 */}
      <div className="command-approval-actions">
        <button
          type="button"
          className="command-approval-btn-cancel"
          onClick={onReject}
        >
          不执行
        </button>
        <button
          type="button"
          className="command-approval-btn-confirm"
          onClick={() => onApprove(selectedScope)}
        >
          确认执行
        </button>
      </div>
    </div>
  );
};
