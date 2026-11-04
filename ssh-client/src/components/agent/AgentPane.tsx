import React from 'react';
import {
  PanelLeft,
  MessageSquarePlus,
  Settings,
  X,
  ChevronsDown,
  Sparkles,
} from 'lucide-react';
import type {
  AgentSessionSummary,
  ChatMessageItem as MessageItemType,
  Host,
  PermissionMode,
} from '../../types';
import { ChatMessageItem } from './ChatMessageItem';
import { ChatInput } from './ChatInput';
import { PendingApprovalDialog } from './PendingApprovalDialog';
import { CommandApprovalDialog } from './CommandApprovalDialog';
import { SessionSidebar } from './SessionSidebar';
import { Tooltip } from '../common/Tooltip';

export interface PendingApprovalInfo {
  approvalId: string;
  command: string;
  reason?: string;
  riskLevel?: string;
  step?: number;
}

interface AgentPaneProps {
  isOpen: boolean;
  width: number;
  host: Host | null;
  sessions: AgentSessionSummary[];
  currentSessionId: string;
  sessionTitle: string;
  messages: MessageItemType[];
  isAgentRunning: boolean;
  isWaitingFirstStep: boolean;
  pendingApproval: PendingApprovalInfo | null;
  showScrollBottom: boolean;
  isSessionSidebarOpen: boolean;
  permissionMode: PermissionMode;
  chatScrollRef: React.RefObject<HTMLDivElement | null>;
  onToggleSessionSidebar: () => void;
  onCreateSession: () => void;
  onSelectSession: (session: AgentSessionSummary) => void;
  onDeleteSession: (sessionId: string) => void;
  onOpenSettings: () => void;
  onClosePane: () => void;
  onReopenPane: () => void;
  onScroll: () => void;
  onScrollToBottom: () => void;
  onApprovalDecision: (approvalId: string, approved: boolean, scope: 'once' | 'session') => void;
  onOpenForkModal: (stepIdx?: number) => void;
  onSend: (prompt: string, images?: string[]) => void;
  onStop: () => void;
  onPermissionModeChange: (mode: PermissionMode) => void;
  onAttachTerminalBuffer: () => void;
  onExecutePlan: (approvalId: string) => void;
  onAdjustDirection: (approvalId: string, supplementText: string) => void;
}

/** 右侧 OpsCopilot Agent 面板（会话侧栏、消息流、审批与输入区） */
export const AgentPane: React.FC<AgentPaneProps> = ({
  isOpen,
  width,
  host,
  sessions,
  currentSessionId,
  sessionTitle,
  messages,
  isAgentRunning,
  isWaitingFirstStep,
  pendingApproval,
  showScrollBottom,
  isSessionSidebarOpen,
  permissionMode,
  chatScrollRef,
  onToggleSessionSidebar,
  onCreateSession,
  onSelectSession,
  onDeleteSession,
  onOpenSettings,
  onClosePane,
  onReopenPane,
  onScroll,
  onScrollToBottom,
  onApprovalDecision,
  onOpenForkModal,
  onSend,
  onStop,
  onPermissionModeChange,
  onAttachTerminalBuffer,
  onExecutePlan,
  onAdjustDirection,
}) => {
  // 当探索版收起时，在终端右侧边缘悬浮展示唤出标签
  if (!isOpen) {
    return (
      <button
        className="floating-agent-reopen-tab"
        onClick={onReopenPane}
        title="展开 OpsCopilot Agent 探索版"
      >
        <Sparkles size={14} color="#388bfd" />
        <span>展开探索版</span>
      </button>
    );
  }

  return (
    <div className="agent-pane" style={{ width: `${width}px` }}>
      {/* 会话侧边抽屉 (展开会话列表时显示，左上角标明 OpsCopilot Agent) */}
      {isSessionSidebarOpen && (
        <SessionSidebar
          sessions={sessions}
          currentSessionId={currentSessionId}
          onSelectSession={onSelectSession}
          onCreateSession={onCreateSession}
          onDeleteSession={onDeleteSession}
          onClose={onToggleSessionSidebar}
        />
      )}

      {/* 顶部工具栏 (展开会话列表图标 | 新会话图标 | 会话标题 | 设置 | 关闭探索版) */}
      <div className="agent-top-bar">
        <div className="agent-top-left">
          {/* 展开会话列表图标 (Tooltip: 展开会话列表) */}
          <Tooltip
            content={isSessionSidebarOpen ? '收起会话列表' : '展开会话列表'}
            placement="right"
          >
            <button className="agent-top-btn" onClick={onToggleSessionSidebar}>
              <PanelLeft size={17} />
            </button>
          </Tooltip>

          {/* 新会话图标 (Tooltip: 新会话) */}
          <Tooltip content="新会话" placement="right">
            <button className="agent-top-btn" onClick={onCreateSession}>
              <MessageSquarePlus size={17} />
            </button>
          </Tooltip>

          <span style={{ color: '#3a3a3a', margin: '0 2px' }}>|</span>

          {/* 会话标题 */}
          <span className="agent-session-title" title={sessionTitle}>
            {sessionTitle}
          </span>
        </div>

        <div className="agent-top-right">
          {/* 设置齿轮 */}
          <button className="agent-top-btn" title="主机与环境配置" onClick={onOpenSettings}>
            <Settings size={16} />
          </button>

          {/* 关闭探索版图标 (Tooltip: 关闭探索版 - 仅收起右侧界面，保留会话历史) */}
          <Tooltip content="关闭探索版" placement="bottom">
            <button className="agent-top-btn" onClick={onClosePane}>
              <X size={16} />
            </button>
          </Tooltip>
        </div>
      </div>

      {/* 消息滚动流 */}
      <div ref={chatScrollRef} className="chat-scroll-area" onScroll={onScroll}>
        {messages.map((msg, idx) => (
          <ChatMessageItem
            key={msg.id || `${msg.role}-${msg.type}-${idx}`}
            message={msg}
            onApprovalDecision={onApprovalDecision}
            onForkBranch={onOpenForkModal}
            onRetry={() => {
              const lastUser = [...messages].reverse().find((m) => m.role === 'user');
              if (lastUser) onSend(lastUser.content);
            }}
          />
        ))}

        {isWaitingFirstStep && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '6px 12px',
              borderRadius: '8px',
              fontSize: '13px',
              margin: '4px 0 16px 0',
            }}
          >
            <span className="thinking-shimmer-text">深度思考...</span>
          </div>
        )}
      </div>

      {/* 浮动滚到底部圆形按钮 (双下箭头) */}
      {showScrollBottom && (
        <button className="scroll-bottom-btn" onClick={onScrollToBottom} title="滚动到底部">
          <ChevronsDown size={16} />
        </button>
      )}

      {/* 拟态浮动输入卡片 或 底部 AI 审批面板 */}
      {pendingApproval ? (
        pendingApproval.command ? (
          <CommandApprovalDialog
            command={pendingApproval.command}
            onApprove={(scope) =>
              onApprovalDecision(pendingApproval.approvalId, true, scope)
            }
            onReject={() => onApprovalDecision(pendingApproval.approvalId, false, 'once')}
          />
        ) : (
          <PendingApprovalDialog
            command={pendingApproval.command}
            riskLevel={pendingApproval.riskLevel}
            reason={pendingApproval.reason}
            onExecute={() => onExecutePlan(pendingApproval.approvalId)}
            onCancel={() => onApprovalDecision(pendingApproval.approvalId, false, 'once')}
            onAdjustDirection={(supplementText) =>
              onAdjustDirection(pendingApproval.approvalId, supplementText)
            }
          />
        )
      ) : (
        <ChatInput
          loading={isAgentRunning}
          targetScopeName={host?.name ? `${host.name} (${host.ip})` : '默认'}
          permissionMode={permissionMode}
          onPermissionModeChange={onPermissionModeChange}
          onSend={onSend}
          onStop={onStop}
          onAttachTerminalBuffer={onAttachTerminalBuffer}
        />
      )}
    </div>
  );
};
