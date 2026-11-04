import React, { useCallback, useState } from 'react';
import type { AgentSessionSummary, ChatMessageItem as MessageItemType } from '../types';
import { sessionService } from '../services/sessionService';
import { removeEmojis } from '../utils/textUtils';

const SESSION_STORAGE_PREFIX = 'opscopilot_current_session_';
const sessionStorageKey = (targetHostId: string) => `${SESSION_STORAGE_PREFIX}${targetHostId}`;
export const DEFAULT_WELCOME_TEXT =
  '您好！我是 **OpsCopilot**。\n\n我已联动左侧终端会话，能够实时洞察屏幕回显并执行自治推演。\n\n在下方输入框直接描述运维需求即可启动推演！';

/** 由流式 Hook 暴露给会话 Hook 的控制能力（通过 ref 注入，避免循环依赖） */
export interface StreamControl {
  resetStream: () => void;
  clearRunPrompt: () => void;
  resetApprovedCommands: () => void;
  attachRunningTask: (sessionId: string) => Promise<void>;
}

interface UseSessionsOptions {
  hostId: string;
  streamControlRef: React.MutableRefObject<StreamControl | null>;
  onSessionSelected?: () => void;
}

export interface SessionsApi {
  sessions: AgentSessionSummary[];
  currentSessionId: string;
  setCurrentSessionId: React.Dispatch<React.SetStateAction<string>>;
  sessionTitle: string;
  setSessionTitle: React.Dispatch<React.SetStateAction<string>>;
  messages: MessageItemType[];
  setMessages: React.Dispatch<React.SetStateAction<MessageItemType[]>>;
  fetchSessions: (targetHostId: string, restoreHistory?: boolean) => Promise<string | null>;
  handleSelectSession: (session: AgentSessionSummary) => Promise<void>;
  handleDeleteSession: (sessionId: string) => Promise<void>;
  handleCreateSession: () => Promise<void>;
}

/** 会话列表、会话历史与消息状态管理 */
export function useSessions({ hostId, streamControlRef, onSessionSelected }: UseSessionsOptions): SessionsApi {
  // 会话列表抽屉展开状态由外层工作区控制，本 Hook 仅负责数据与会话切换
  const [sessions, setSessions] = useState<AgentSessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string>('');
  const [sessionTitle, setSessionTitle] = useState<string>('智能运维会话');
  const [messages, setMessages] = useState<MessageItemType[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content: DEFAULT_WELCOME_TEXT,
      timestamp: new Date().toISOString(),
    },
  ]);

  const persistCurrentSession = useCallback((targetHostId: string, sessionId: string) => {
    try {
      localStorage.setItem(sessionStorageKey(targetHostId), sessionId);
    } catch {
      // ignore
    }
  }, []);

  // 从后端加载指定会话的历史消息，刷新页面后可恢复对话
  const loadSessionMessages = useCallback(async (sessionId: string, emptyText: string) => {
    try {
      const detail = await sessionService.getSessionDetail(sessionId);
      if (detail && detail.messages && detail.messages.length > 0) {
        const loaded: MessageItemType[] = detail.messages.map((m: any) => ({
          id: m.id || `${m.role}-${Date.now()}`,
          role: m.role,
          type: m.role === 'assistant' ? 'final_answer' : 'text',
          content: removeEmojis(m.content || ''),
          timestamp: m.created_at,
        }));
        setMessages(loaded);
        return;
      }
    } catch (err) {
      console.error('Failed to load session details:', err);
    }
    setMessages([
      {
        id: `welcome-${sessionId}`,
        role: 'assistant',
        content: emptyText,
        timestamp: new Date().toISOString(),
      },
    ]);
  }, [setMessages]);

  const fetchSessions = useCallback(async (
    targetHostId: string,
    restoreHistory = false
  ): Promise<string | null> => {
    try {
      const sessionList = await sessionService.getSessions(targetHostId);
      setSessions(sessionList);

      if (sessionList.length === 0) {
        const newSession = await sessionService.createSession(targetHostId);
        setSessions([newSession]);
        setCurrentSessionId(newSession.session_id);
        setSessionTitle(newSession.title || '智能运维会话');
        persistCurrentSession(targetHostId, newSession.session_id);
        if (restoreHistory) {
          setMessages([
            {
              id: `welcome-${newSession.session_id}`,
              role: 'assistant',
              content: DEFAULT_WELCOME_TEXT,
              timestamp: new Date().toISOString(),
            },
          ]);
        }
        return newSession.session_id;
      }

      let savedSessionId: string | null = null;
      try {
        savedSessionId = localStorage.getItem(sessionStorageKey(targetHostId));
      } catch {
        savedSessionId = null;
      }
      const chosen =
        sessionList.find((item) => item.session_id === savedSessionId) || sessionList[0];

      if (restoreHistory || !currentSessionId) {
        setCurrentSessionId(chosen.session_id);
        setSessionTitle(chosen.title || '智能运维会话');
        persistCurrentSession(targetHostId, chosen.session_id);
        if (restoreHistory) {
          await loadSessionMessages(chosen.session_id, DEFAULT_WELCOME_TEXT);
        }
      }
      return chosen.session_id;
    } catch (err) {
      console.error('Failed to fetch sessions:', err);
      return null;
    }
  }, [currentSessionId, loadSessionMessages, persistCurrentSession]);

  // 切换会话
  const handleSelectSession = async (session: AgentSessionSummary) => {
    // 中止本地实时流；服务端任务继续后台运行，切回该会话时自动接续
    streamControlRef.current?.resetStream();

    setCurrentSessionId(session.session_id);
    setSessionTitle(session.title || '智能运维会话');
    if (hostId) persistCurrentSession(hostId, session.session_id);
    onSessionSelected?.();

    await loadSessionMessages(
      session.session_id,
      `已切换至会话：**${session.title}**。\n请输入您的运维需求。`
    );
    await streamControlRef.current?.attachRunningTask(session.session_id);
  };

  // 删除会话：当前会话被删除后自动跳转到新会话
  const handleDeleteSession = async (sessionId: string) => {
    const isCurrent = currentSessionId === sessionId;
    try {
      await sessionService.deleteSession(sessionId);
    } catch (err) {
      console.error('Failed to delete session:', err);
      return;
    }

    // 本地立即移除，避免列表刷新滞后造成“删除无效”的观感
    setSessions((prev) => prev.filter((item) => item.session_id !== sessionId));

    if (!isCurrent) {
      if (hostId) await fetchSessions(hostId);
      return;
    }

    try {
      if (hostId) localStorage.removeItem(sessionStorageKey(hostId));
    } catch {
      // ignore
    }
    setCurrentSessionId('');
    await handleCreateSession();
  };

  // 创建新会话
  const handleCreateSession = async () => {
    if (!hostId) return;
    try {
      const newSession = await sessionService.createSession(hostId);
      setSessions((prev) => [newSession, ...prev]);
      setCurrentSessionId(newSession.session_id);
      setSessionTitle(newSession.title);
      persistCurrentSession(hostId, newSession.session_id);
      onSessionSelected?.();
      streamControlRef.current?.clearRunPrompt();
      streamControlRef.current?.resetApprovedCommands();
      setMessages([
        {
          id: `welcome-${newSession.session_id}`,
          role: 'assistant',
          content: `已为您开启全新智能运维会话：**${newSession.title}**。\n请输入您的运维需求。`,
          timestamp: new Date().toISOString(),
        },
      ]);
    } catch (err) {
      console.error('Failed to create session:', err);
    }
  };

  return {
    sessions,
    currentSessionId,
    setCurrentSessionId,
    sessionTitle,
    setSessionTitle,
    messages,
    setMessages,
    fetchSessions,
    handleSelectSession,
    handleDeleteSession,
    handleCreateSession,
  };
}
