import React, { useCallback, useRef, useState } from 'react';
import type {
  ChatMessageItem as MessageItemType,
  PermissionMode,
  ReActEvent,
  ReActStepEvent,
} from '../types';
import { agentService } from '../services/agentService';
import { removeEmojis } from '../utils/textUtils';
import {
  applyReActStepEvent,
  resolveSearchStartIndex,
  settleRunningSteps,
} from '../utils/reactEventReducer';

export interface BranchOptions {
  branchFromSessionId: string;
  branchStepIdx: number;
  branchTitle: string;
  newSessionId?: string;
}

interface UseReActStreamOptions {
  hostId: string;
  currentSessionId: string;
  permissionMode: PermissionMode;
  setMessages: React.Dispatch<React.SetStateAction<MessageItemType[]>>;
  setCurrentSessionId: React.Dispatch<React.SetStateAction<string>>;
  setSessionTitle: React.Dispatch<React.SetStateAction<string>>;
  fetchSessions: (targetHostId: string, restoreHistory?: boolean) => Promise<string | null>;
  getTerminalBuffer: () => string;
}

export interface ReActStreamApi {
  isAgentRunning: boolean;
  approvedCommands: string[];
  handleApprovalDecision: (
    approvalId: string,
    approved: boolean,
    scope: 'once' | 'session'
  ) => Promise<void>;
  handleSendPrompt: (
    prompt: string,
    branchOptions?: BranchOptions,
    images?: string[]
  ) => Promise<void>;
  handleSendChatPrompt: (prompt: string, images?: string[]) => Promise<void>;
  handleStopAgent: () => void;
  attachRunningTask: (sessionId: string) => Promise<void>;
  resetStream: () => void;
  clearRunPrompt: () => void;
  resetApprovedCommands: () => void;
}

/** ReAct 流式通信：SSE 事件应用、发送/停止与服务端任务接续 */
export function useReActStream({
  hostId,
  currentSessionId,
  permissionMode,
  setMessages,
  setCurrentSessionId,
  setSessionTitle,
  fetchSessions,
  getTerminalBuffer,
}: UseReActStreamOptions): ReActStreamApi {
  const [isAgentRunning, setIsAgentRunning] = useState(false);
  const [approvedCommands, setApprovedCommands] = useState<string[]>([]);

  const abortControllerRef = useRef<AbortController | null>(null);
  const currentRunPromptIdRef = useRef<string | null>(null);
  const attachSeqRef = useRef(0);

  // 统一处理 ReAct 事件（首次发起与刷新/断线后接续共用）
  const applyReActEvent = useCallback(
    (event: ReActEvent) => {
      if (event.type === 'start') {
        // 接续场景下本地尚无本条用户输入：依据 start 事件恢复用户消息并锚定本轮步骤
        if (!currentRunPromptIdRef.current && event.user_prompt) {
          const restoredId = `user-restored-${Date.now()}`;
          currentRunPromptIdRef.current = restoredId;
          setMessages((prev) => [
            ...prev,
            {
              id: restoredId,
              role: 'user',
              content: removeEmojis(event.user_prompt || ''),
              timestamp: new Date().toISOString(),
            },
          ]);
        }
        return;
      }

      if (event.type === 'branch_created') {
        if (event.branch_session_id) {
          setCurrentSessionId(event.branch_session_id);
        }
        if (hostId) {
          fetchSessions(hostId);
        }
        return;
      }

      if (event.type === 'greeting') {
        setMessages((prev) => [
          ...prev,
          {
            id: `greeting-${Date.now()}`,
            role: 'assistant',
            type: 'final_answer',
            content: removeEmojis(event.content || ''),
          },
        ]);
        return;
      }

      if (event.type === 'final_answer' || event.type === 'finish') {
        const conclusion = event.conclusion ?? event.content ?? '排障流程已达成目标。';
        setMessages((prev) => {
          const searchStartIndex = resolveSearchStartIndex(prev, currentRunPromptIdRef.current);

          const existingFinalIndex = prev.findIndex(
            (m, idx) => idx >= searchStartIndex && m.type === 'final_answer'
          );
          if (existingFinalIndex >= 0) {
            const updated = [...prev];
            updated[existingFinalIndex] = {
              ...updated[existingFinalIndex],
              content: removeEmojis(conclusion),
            };
            return updated;
          }

          const updated = settleRunningSteps(prev, searchStartIndex, 'completed');
          return [
            ...updated,
            {
              id: `final-${Date.now()}`,
              role: 'assistant',
              type: 'final_answer',
              content: removeEmojis(conclusion),
            },
          ];
        });
        return;
      }

      if (event.type === 'error') {
        const errText = event.error || event.content || '执行遇到错误';
        setMessages((prev) => {
          const searchStartIndex = resolveSearchStartIndex(prev, currentRunPromptIdRef.current);
          const updated = settleRunningSteps(prev, searchStartIndex, 'error');
          return [
            ...updated,
            {
              id: `err-${Date.now()}`,
              role: 'system',
              type: 'error',
              content: removeEmojis(errText),
            },
          ];
        });
        return;
      }

      // 步骤聚合: thought, token_delta, action, observation, approval_required, reflection
      // 前面的 start/branch/greeting/final/error 分支均已 return，此处事件必为单步事件
      setMessages((prev) =>
        applyReActStepEvent(prev, event as ReActStepEvent, currentRunPromptIdRef.current)
      );
    },
    [hostId, fetchSessions, setCurrentSessionId, setMessages]
  );

  // 流式通信异常处理
  const handleStreamError = useCallback(
    (err: Error) => {
      setMessages((prev) => {
        const searchStartIndex = resolveSearchStartIndex(prev, currentRunPromptIdRef.current);
        const updated = settleRunningSteps(prev, searchStartIndex, 'error');
        return [
          ...updated,
          {
            id: `err-${Date.now()}`,
            role: 'system',
            type: 'error',
            content: err.message || '通信中断',
          },
        ];
      });
      setIsAgentRunning(false);
    },
    [setMessages]
  );

  // 流式通信完成处理
  const handleStreamDone = useCallback(() => {
    setMessages((prev) => {
      const searchStartIndex = resolveSearchStartIndex(prev, currentRunPromptIdRef.current);
      return settleRunningSteps(prev, searchStartIndex, 'completed');
    });
    setIsAgentRunning(false);
    if (hostId) {
      fetchSessions(hostId);
    }
  }, [hostId, fetchSessions, setMessages]);

  // 刷新/切换会话后接续仍在服务端后台运行的排障任务
  const attachRunningTask = useCallback(
    async (sessionId: string) => {
      if (!hostId || !sessionId) return;
      const seq = ++attachSeqRef.current;
      try {
        const status = await agentService.getReActStatus(hostId, sessionId);
        if (seq !== attachSeqRef.current || !status.running) return;

        currentRunPromptIdRef.current = null;
        setIsAgentRunning(true);
        const controller = new AbortController();
        abortControllerRef.current = controller;

        await agentService.attachReAct(
          hostId,
          { sessionId, after: 0 },
          {
            onEvent: applyReActEvent,
            onError: handleStreamError,
            onDone: handleStreamDone,
          },
          controller.signal
        );
      } catch (err) {
        console.error('Failed to attach running task:', err);
        if (seq === attachSeqRef.current) {
          setIsAgentRunning(false);
        }
      }
    },
    [hostId, applyReActEvent, handleStreamError, handleStreamDone]
  );

  // 立即丢弃本地实时流（服务端任务继续后台运行）
  const resetStream = useCallback(() => {
    attachSeqRef.current += 1;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsAgentRunning(false);
    currentRunPromptIdRef.current = null;
  }, []);

  const clearRunPrompt = useCallback(() => {
    currentRunPromptIdRef.current = null;
  }, []);

  const resetApprovedCommands = useCallback(() => {
    setApprovedCommands([]);
  }, []);

  // 提交高危审批决定
  const handleApprovalDecision = useCallback(
    async (approvalId: string, approved: boolean, scope: 'once' | 'session') => {
      if (!hostId) return;

      setMessages((prev) =>
        prev.map((msg) => {
          if (msg.type === 'react_step' && msg.stepData.approvalId === approvalId) {
            const nextStatus: 'running' | 'completed' = approved ? 'running' : 'completed';
            return {
              ...msg,
              stepData: {
                ...msg.stepData,
                approved,
                rejected: !approved,
                status: nextStatus,
              },
            };
          }
          return msg;
        })
      );

      try {
        await agentService.submitApproval(hostId, {
          approvalId,
          approved,
          scope,
        });

        if (approved && scope === 'session') {
          setMessages((prevMsgs) => {
            const targetStepMsg = prevMsgs.find(
              (m) => m.type === 'react_step' && m.stepData.approvalId === approvalId
            );
            const cmd =
              targetStepMsg?.type === 'react_step' ? targetStepMsg.stepData.command : undefined;
            if (cmd) {
              setApprovedCommands((prevCmds) => [...prevCmds, cmd]);
            }
            return prevMsgs;
          });
        }
      } catch (err: any) {
        console.error('Submit approval failed:', err);
        setMessages((prev) => [
          ...prev,
          {
            id: `err-${Date.now()}`,
            role: 'system',
            type: 'error',
            content: `提交审批决定失败: ${err.message}`,
          },
        ]);
      }
    },
    [hostId, setMessages]
  );

  // 发送用户排障目标 (支持普通会话与分支分叉执行)
  const handleSendPrompt = async (
    prompt: string,
    branchOptions?: BranchOptions,
    images?: string[]
  ) => {
    if (!hostId || isAgentRunning) return;

    const cleanPrompt = removeEmojis(prompt);
    if (!cleanPrompt) return;

    const targetSessionId = branchOptions?.newSessionId || currentSessionId;
    if (branchOptions?.newSessionId) {
      setCurrentSessionId(branchOptions.newSessionId);
    }

    const cleanTitle =
      branchOptions?.branchTitle ||
      (cleanPrompt.length > 20 ? `${cleanPrompt.slice(0, 18)}...` : cleanPrompt);
    setSessionTitle(cleanTitle);

    const userMsgId = `user-${Date.now()}`;
    currentRunPromptIdRef.current = userMsgId;
    const userMsgContent = branchOptions
      ? `[分叉自步骤 ${branchOptions.branchStepIdx}] ${cleanPrompt}`
      : cleanPrompt;

    setMessages((prev) => [
      ...prev,
      {
        id: userMsgId,
        role: 'user',
        content: userMsgContent,
        images: images && images.length > 0 ? images : undefined,
        timestamp: new Date().toISOString(),
      },
    ]);

    setIsAgentRunning(true);
    abortControllerRef.current = new AbortController();
    attachSeqRef.current += 1;

    const terminalBuffer = getTerminalBuffer();

    try {
      await agentService.streamReAct(
        hostId,
        {
          prompt: cleanPrompt,
          terminalBuffer,
          sessionId: targetSessionId,
          permissionMode,
          approvedCommands,
          maxSteps: 12,
          branchFromSessionId: branchOptions?.branchFromSessionId,
          branchStepIdx: branchOptions?.branchStepIdx,
          branchTitle: branchOptions?.branchTitle,
          images,
        },
        {
          onEvent: applyReActEvent,
          onError: handleStreamError,
          onDone: handleStreamDone,
        },
        abortControllerRef.current.signal
      );
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          role: 'system',
          type: 'error',
          content: err.message,
        },
      ]);
      setIsAgentRunning(false);
    }
  };

  // 发送普通对话消息（Chat 模式：走 /agent/chat 单次问答接口）
  const handleSendChatPrompt = async (prompt: string, images?: string[]) => {
    if (!hostId || isAgentRunning) return;

    const cleanPrompt = removeEmojis(prompt);
    if (!cleanPrompt) return;

    setSessionTitle(
      cleanPrompt.length > 20 ? `${cleanPrompt.slice(0, 18)}...` : cleanPrompt
    );

    const userMsgId = `user-${Date.now()}`;
    currentRunPromptIdRef.current = userMsgId;
    setMessages((prev) => [
      ...prev,
      {
        id: userMsgId,
        role: 'user',
        content: cleanPrompt,
        images: images && images.length > 0 ? images : undefined,
        timestamp: new Date().toISOString(),
      },
    ]);

    setIsAgentRunning(true);
    abortControllerRef.current = new AbortController();
    attachSeqRef.current += 1;

    const assistantMsgId = `assistant-${Date.now()}`;
    let hasContent = false;
    let failed = false;

    try {
      await agentService.streamChat(
        hostId,
        {
          prompt: cleanPrompt,
          terminalBuffer: getTerminalBuffer(),
          sessionId: currentSessionId,
          permissionMode,
        },
        {
          onContent: (chunk) => {
            if (!chunk) return;
            hasContent = true;
            setMessages((prev) => {
              const idx = prev.findIndex((m) => m.id === assistantMsgId);
              if (idx < 0) {
                return [
                  ...prev,
                  {
                    id: assistantMsgId,
                    role: 'assistant',
                    type: 'final_answer',
                    content: chunk,
                    timestamp: new Date().toISOString(),
                  },
                ];
              }
              const updated = [...prev];
              updated[idx] = { ...updated[idx], content: updated[idx].content + chunk };
              return updated;
            });
          },
          onError: (err) => {
            failed = true;
            handleStreamError(err);
          },
          onDone: () => {
            if (failed) return;
            if (!hasContent) {
              setMessages((prev) => [
                ...prev,
                {
                  id: `err-${Date.now()}`,
                  role: 'system',
                  type: 'error',
                  content: '对话接口未返回任何内容',
                },
              ]);
            }
            setIsAgentRunning(false);
            if (hostId) {
              fetchSessions(hostId);
            }
          },
        },
        abortControllerRef.current.signal
      );
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          role: 'system',
          type: 'error',
          content: err.message,
        },
      ]);
      setIsAgentRunning(false);
    }
  };

  const handleStopAgent = () => {
    attachSeqRef.current += 1;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    // 同步取消服务端后台任务，避免只断开本地流而任务继续空转
    if (hostId && currentSessionId) {
      agentService.cancelReAct(hostId, currentSessionId).catch((err) => {
        console.error('Failed to cancel ReAct task:', err);
      });
    }
    setMessages((prev) => {
      const searchStartIndex = resolveSearchStartIndex(prev, currentRunPromptIdRef.current);
      return settleRunningSteps(prev, searchStartIndex, 'completed');
    });
    setIsAgentRunning(false);
  };

  return {
    isAgentRunning,
    approvedCommands,
    handleApprovalDecision,
    handleSendPrompt,
    handleSendChatPrompt,
    handleStopAgent,
    attachRunningTask,
    resetStream,
    clearRunPrompt,
    resetApprovedCommands,
  };
}
