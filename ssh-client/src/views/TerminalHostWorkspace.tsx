import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, Copy, Check, X } from 'lucide-react';
import type { Host, PermissionMode } from '../types';
import { hostService } from '../services/hostService';
import { TerminalHeader } from '../components/terminal/TerminalHeader';
import { XTerminal, type XTerminalRef } from '../components/terminal/XTerminal';
import { AgentPane, type PendingApprovalInfo } from '../components/agent/AgentPane';
import { BranchForkModal } from '../components/agent/BranchForkModal';
import { useSessions, type StreamControl } from '../hooks/useSessions';
import { useReActStream } from '../hooks/useReActStream';

interface TerminalHostWorkspaceProps {
  hostId: string;
  isActive?: boolean;
}

const DEFAULT_AGENT_WIDTH = 480;
const MIN_AGENT_WIDTH = 340;

/** 单主机终端工作区：左侧 WebSSH 终端 + 右侧 OpsCopilot Agent 面板 */
export const TerminalHostWorkspace: React.FC<TerminalHostWorkspaceProps> = ({
  hostId,
  isActive = true,
}) => {
  const [host, setHost] = useState<Host | null>(null);
  const [terminalStatus, setTerminalStatus] = useState<
    'connecting' | 'connected' | 'disconnected' | 'error'
  >('connecting');
  const [permissionMode, setPermissionMode] = useState<PermissionMode>('request_approval');

  // 探索版展开/收起状态 (关闭探索版即收起右侧 Agent 界面，保持会话与消息完全保留)
  const [isAgentPaneOpen, setIsAgentPaneOpen] = useState(true);

  // Agent 面板宽度与拖拽分屏状态
  const [agentWidth, setAgentWidth] = useState<number>(() => {
    try {
      const saved = localStorage.getItem('smartops_agent_pane_width');
      if (saved) {
        const parsed = parseInt(saved, 10);
        if (!isNaN(parsed) && parsed >= 320 && parsed <= 1200) {
          return parsed;
        }
      }
    } catch {
      // ignore
    }
    return DEFAULT_AGENT_WIDTH;
  });
  const [isDragging, setIsDragging] = useState(false);
  const dragStartXRef = useRef<number>(0);
  const dragStartWidthRef = useRef<number>(DEFAULT_AGENT_WIDTH);

  // 会话列表抽屉展开状态
  const [isSessionSidebarOpen, setIsSessionSidebarOpen] = useState(false);

  // 滚动条与设置弹窗
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [copiedSetting, setCopiedSetting] = useState(false);

  // 分支分叉模态弹窗状态
  const [forkModalState, setForkModalState] = useState<{
    isOpen: boolean;
    stepIdx: number;
    initialPrompt?: string;
  }>({
    isOpen: false,
    stepIdx: 1,
    initialPrompt: '',
  });

  const terminalRef = useRef<XTerminalRef>(null);
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const streamControlRef = useRef<StreamControl | null>(null);

  // 标签页切换时重新适配终端尺寸
  useEffect(() => {
    if (!isActive) return;
    const timer = window.setTimeout(() => terminalRef.current?.fit(), 80);
    return () => window.clearTimeout(timer);
  }, [isActive]);

  const sessionsApi = useSessions({
    hostId,
    streamControlRef,
    onSessionSelected: () => setIsSessionSidebarOpen(false),
  });

  const {
    sessions,
    currentSessionId,
    sessionTitle,
    messages,
    setMessages,
    fetchSessions,
    handleSelectSession,
    handleDeleteSession,
    handleCreateSession,
  } = sessionsApi;

  const reactApi = useReActStream({
    hostId,
    currentSessionId,
    permissionMode,
    setMessages,
    setCurrentSessionId: sessionsApi.setCurrentSessionId,
    setSessionTitle: sessionsApi.setSessionTitle,
    fetchSessions,
    getTerminalBuffer: () => terminalRef.current?.getBuffer() || '',
  });

  const {
    isAgentRunning,
    handleApprovalDecision,
    handleSendPrompt,
    handleStopAgent,
    attachRunningTask,
    resetStream,
    clearRunPrompt,
    resetApprovedCommands,
  } = reactApi;

  streamControlRef.current = {
    resetStream,
    clearRunPrompt,
    resetApprovedCommands,
    attachRunningTask,
  };

  // 滚动监听
  const handleScroll = () => {
    if (!chatScrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = chatScrollRef.current;
    const isUp = scrollHeight - scrollTop - clientHeight > 120;
    setShowScrollBottom(isUp);
  };

  const scrollToBottom = useCallback(() => {
    if (chatScrollRef.current) {
      chatScrollRef.current.scrollTo({
        top: chatScrollRef.current.scrollHeight,
        behavior: 'smooth',
      });
    }
  }, []);

  // 计算当前是否存在等待用户决断的高危审批任务 (AI 等待你的回复)
  const pendingApproval: PendingApprovalInfo | null = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.type === 'react_step') {
        if (
          msg.stepData.status === 'approval_required' &&
          !msg.stepData.approved &&
          !msg.stepData.rejected &&
          msg.stepData.approvalId
        ) {
          return {
            approvalId: msg.stepData.approvalId,
            command: msg.stepData.command || '',
            reason: msg.stepData.reason,
            riskLevel: msg.stepData.riskLevel,
            step: msg.stepData.step,
          };
        }
      }
    }
    return null;
  }, [messages]);

  useEffect(() => {
    if (pendingApproval) {
      scrollToBottom();
    }
  }, [pendingApproval, scrollToBottom]);

  useEffect(() => {
    if (chatScrollRef.current && !showScrollBottom) {
      chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
    }
  }, [messages, isAgentRunning, showScrollBottom]);

  // 判断当前轮次是否正在等待首个步骤卡片生成
  const isWaitingFirstStep = useMemo(() => {
    if (!isAgentRunning) return false;
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.role === 'user') break;
      if (msg.type === 'react_step') return false;
    }
    return true;
  }, [isAgentRunning, messages]);

  // 初始化加载主机信息与会话列表，并接续可能仍在后台运行的排障任务
  useEffect(() => {
    if (!hostId) return;

    const initData = async () => {
      try {
        const hostData = await hostService.getHost(hostId);
        setHost(hostData);
        const sessionId = await fetchSessions(hostId, true);
        if (sessionId) await attachRunningTask(sessionId);
      } catch (err) {
        console.error('Failed to init workspace data:', err);
      }
    };

    initData();
  }, [hostId]);

  // 打开从某步分叉弹窗
  const handleOpenForkModal = useCallback(
    (stepIdx?: number) => {
      let targetStep = stepIdx;
      if (!targetStep) {
        for (let i = messages.length - 1; i >= 0; i--) {
          const candidate = messages[i];
          if (candidate.type === 'react_step' && candidate.stepData.step) {
            targetStep = candidate.stepData.step;
            break;
          }
        }
      }
      targetStep = targetStep || 1;

      let targetStepData;
      for (let i = messages.length - 1; i >= 0; i--) {
        const candidate = messages[i];
        if (candidate.type === 'react_step' && candidate.stepData.step === targetStep) {
          targetStepData = candidate.stepData;
          break;
        }
      }
      const initialPrompt = targetStepData?.thought || targetStepData?.command || '';
      setForkModalState({
        isOpen: true,
        stepIdx: targetStep,
        initialPrompt,
      });
    },
    [messages]
  );

  // 提交切出新排障分支
  const handleForkSubmit = async (branchTitle: string, newPrompt: string) => {
    const parentSid = currentSessionId;
    const branchPointStep = forkModalState.stepIdx;
    setForkModalState({ isOpen: false, stepIdx: 1 });

    const newBranchSessionId = `branch-${Date.now()}`;
    await handleSendPrompt(newPrompt, {
      branchFromSessionId: parentSid,
      branchStepIdx: branchPointStep,
      branchTitle,
      newSessionId: newBranchSessionId,
    });
    if (hostId) await fetchSessions(hostId);
  };

  // 审批面板「开始执行」：追加用户确认消息并放行
  const handleExecutePlan = (approvalId: string) => {
    setMessages((prev) => [
      ...prev,
      {
        id: `user-${Date.now()}`,
        role: 'user',
        content: '开始执行',
        timestamp: new Date().toISOString(),
      },
    ]);
    handleApprovalDecision(approvalId, true, 'once');
  };

  // 审批面板「调整方向」：驳回后按补充说明继续推演
  const handleAdjustDirection = (approvalId: string, supplementText: string) => {
    handleApprovalDecision(approvalId, false, 'once');
    if (supplementText && supplementText.trim()) {
      handleSendPrompt(supplementText.trim());
    }
  };

  // 拖拽分屏调节
  const handleSplitterMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
    dragStartXRef.current = e.clientX;
    dragStartWidthRef.current = agentWidth;

    const handleMouseMove = (moveEvent: MouseEvent) => {
      const delta = dragStartXRef.current - moveEvent.clientX;
      const maxAllowedWidth = Math.floor(window.innerWidth * 0.75);
      const newWidth = Math.max(
        MIN_AGENT_WIDTH,
        Math.min(maxAllowedWidth, dragStartWidthRef.current + delta)
      );
      setAgentWidth(newWidth);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      terminalRef.current?.fit();
      setAgentWidth((w) => {
        try {
          localStorage.setItem('smartops_agent_pane_width', String(w));
        } catch {
          // ignore
        }
        return w;
      });
    };

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  };

  const handleSplitterDoubleClick = () => {
    setAgentWidth(DEFAULT_AGENT_WIDTH);
    try {
      localStorage.setItem('smartops_agent_pane_width', String(DEFAULT_AGENT_WIDTH));
    } catch {
      // ignore
    }
    setTimeout(() => {
      terminalRef.current?.fit();
    }, 50);
  };

  if (!hostId) {
    return (
      <div style={{ padding: '40px', textAlign: 'center' }}>
        <AlertCircle size={32} color="#f85149" />
        <p>未指定目标主机 ID</p>
        <Link to="/" className="btn btn-primary" style={{ marginTop: '16px' }}>
          返回主机列表
        </Link>
      </div>
    );
  }

  return (
    <div className={`workspace-container ${isDragging ? 'is-dragging' : ''}`} style={{ height: '100%' }}>
      {/* 左侧：WebSSH 终端面板 */}
      <div className="terminal-pane" style={{ flex: 1 }}>
        <TerminalHeader
          host={host}
          status={terminalStatus}
          isAgentPaneOpen={isAgentPaneOpen}
          onToggleAgentPane={() => {
            setIsAgentPaneOpen(!isAgentPaneOpen);
            setTimeout(() => terminalRef.current?.fit(), 150);
          }}
          onReconnect={() => terminalRef.current?.reconnect()}
          onClear={() => terminalRef.current?.clear()}
        />

        <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
          <XTerminal ref={terminalRef} hostId={hostId} onStatusChange={setTerminalStatus} />
        </div>
      </div>

      {/* 垂直拖拽分屏调节手柄 */}
      {isAgentPaneOpen && (
        <div
          className={`workspace-splitter ${isDragging ? 'dragging' : ''}`}
          onMouseDown={handleSplitterMouseDown}
          onDoubleClick={handleSplitterDoubleClick}
          title="按住拖拽调节宽度，双击恢复默认"
        >
          <div className="workspace-splitter-handle" />
        </div>
      )}

      {/* 右侧：OpsAgent 智能协同排障面板 */}
      <AgentPane
        isOpen={isAgentPaneOpen}
        width={agentWidth}
        host={host}
        sessions={sessions}
        currentSessionId={currentSessionId}
        sessionTitle={sessionTitle}
        messages={messages}
        isAgentRunning={isAgentRunning}
        isWaitingFirstStep={isWaitingFirstStep}
        pendingApproval={pendingApproval}
        showScrollBottom={showScrollBottom}
        isSessionSidebarOpen={isSessionSidebarOpen}
        permissionMode={permissionMode}
        chatScrollRef={chatScrollRef}
        onToggleSessionSidebar={() => setIsSessionSidebarOpen(!isSessionSidebarOpen)}
        onCreateSession={handleCreateSession}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
        onOpenSettings={() => setShowSettingsModal(true)}
        onClosePane={() => setIsAgentPaneOpen(false)}
        onReopenPane={() => {
          setIsAgentPaneOpen(true);
          setTimeout(() => terminalRef.current?.fit(), 150);
        }}
        onScroll={handleScroll}
        onScrollToBottom={scrollToBottom}
        onApprovalDecision={handleApprovalDecision}
        onOpenForkModal={handleOpenForkModal}
        onSend={(p, imgs) => handleSendPrompt(p, undefined, imgs)}
        onStop={handleStopAgent}
        onPermissionModeChange={setPermissionMode}
        onAttachTerminalBuffer={() => {
          const buf = terminalRef.current?.getBuffer() || '';
          console.log('Attached terminal buffer length:', buf.length);
        }}
        onExecutePlan={handleExecutePlan}
        onAdjustDirection={handleAdjustDirection}
      />

      {/* 偏好设置模态框 */}
      {showSettingsModal && (
        <div className="modal-backdrop" onClick={() => setShowSettingsModal(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ fontSize: '15px', fontWeight: 600 }}>环境与连接信息</h3>
              <button className="agent-top-btn" onClick={() => setShowSettingsModal(false)}>
                <X size={16} />
              </button>
            </div>
            <div className="modal-body" style={{ fontSize: '13px', lineHeight: '1.8' }}>
              <div><strong>目标主机：</strong> {host?.name}</div>
              <div><strong>网络地址：</strong> <code>{host?.username}@{host?.ip}:{host?.port}</code></div>
              <div><strong>终端状态：</strong> <span style={{ color: terminalStatus === 'connected' ? '#3fb950' : '#f85149' }}>● {terminalStatus.toUpperCase()}</span></div>
              <div><strong>当前会话 ID：</strong> <code>{currentSessionId}</code></div>
              <div><strong>工具权限模式：</strong> {permissionMode === 'request_approval' ? '请求批准 (需确认)' : '完全访问权限'}</div>
            </div>
            <div className="modal-footer">
              <button
                className="btn btn-sm"
                onClick={() => {
                  navigator.clipboard.writeText(
                    `主机: ${host?.name} (${host?.ip})\n会话ID: ${currentSessionId}`
                  );
                  setCopiedSetting(true);
                  setTimeout(() => setCopiedSetting(false), 2000);
                }}
              >
                {copiedSetting ? <Check size={13} color="#3fb950" /> : <Copy size={13} />}
                <span>{copiedSetting ? '已复制' : '复制环境信息'}</span>
              </button>
              <button className="btn btn-sm btn-primary" onClick={() => setShowSettingsModal(false)}>
                完成
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 步骤分叉切出分支模态框 */}
      <BranchForkModal
        isOpen={forkModalState.isOpen}
        stepIdx={forkModalState.stepIdx}
        initialPrompt={forkModalState.initialPrompt}
        onClose={() => setForkModalState({ isOpen: false, stepIdx: 1 })}
        onSubmit={handleForkSubmit}
      />
    </div>
  );
};
