import { API_BASE_URL, apiClient } from './api';
import type { PermissionMode, ReActEvent } from '../types';

interface StreamCallbacks {
  onEvent: (event: ReActEvent) => void;
  onError: (err: Error) => void;
  onDone: () => void;
}

/**
 * 统一消费 SSE 响应流（POST 发起与 GET 接续共用）
 */
async function consumeSSEStream<T = ReActEvent>(
  url: string,
  init: RequestInit,
  callbacks: {
    onEvent: (event: T) => void;
    onError: (err: Error) => void;
    onDone: () => void;
  },
  abortSignal?: AbortSignal
): Promise<void> {
  try {
    const response = await fetch(url, { ...init, signal: abortSignal });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`ReAct 请求失败 (${response.status}): ${errorText}`);
    }

    if (!response.body) {
      throw new Error('未获取到响应数据流 (response.body is null)');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data:')) continue;

        const dataStr = trimmed.slice(5).trim();
        if (dataStr === '[DONE]') {
          callbacks.onDone();
          return;
        }

        try {
          const event = JSON.parse(dataStr) as T;
          callbacks.onEvent(event);
        } catch (parseErr) {
          console.warn('[SSE Parse Warn]:', parseErr, dataStr);
        }
      }
    }

    callbacks.onDone();
  } catch (err: any) {
    if (err.name === 'AbortError') {
      console.log('[ReAct Stream Aborted]');
      return;
    }
    callbacks.onError(err);
  }
}

export const agentService = {
  /**
   * 普通对话 SSE 流式接口（单次大模型调用，不调用工具）
   */
  async streamChat(
    hostId: string,
    params: {
      prompt: string;
      terminalBuffer?: string;
      sessionId?: string;
      permissionMode?: PermissionMode;
    },
    callbacks: {
      onContent: (chunk: string) => void;
      onError: (err: Error) => void;
      onDone: () => void;
    },
    abortSignal?: AbortSignal
  ): Promise<void> {
    const url = `${API_BASE_URL}/api/v1/hosts/${encodeURIComponent(hostId)}/agent/chat`;

    return consumeSSEStream<{ content?: string; error?: string }>(
      url,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify({
          prompt: params.prompt,
          terminal_buffer: params.terminalBuffer || '',
          session_id: params.sessionId,
          permission_mode: params.permissionMode || 'request_approval',
        }),
      },
      {
        onEvent: (event) => {
          if (event.error) {
            callbacks.onError(new Error(event.error));
          } else if (event.content) {
            callbacks.onContent(event.content);
          }
        },
        onError: callbacks.onError,
        onDone: callbacks.onDone,
      },
      abortSignal
    );
  },

  /**
   * 启动智能自治 ReAct 闭环排障 SSE 流式任务
   */
  async streamReAct(
    hostId: string,
    params: {
      prompt: string;
      terminalBuffer?: string;
      sessionId?: string;
      permissionMode?: PermissionMode;
      approvedCommands?: string[];
      maxSteps?: number;
      branchFromSessionId?: string;
      branchStepIdx?: number;
      branchTitle?: string;
      images?: string[];
    },
    callbacks: {
      onEvent: (event: ReActEvent) => void;
      onError: (err: Error) => void;
      onDone: () => void;
    },
    abortSignal?: AbortSignal
  ): Promise<void> {
    const url = `${API_BASE_URL}/api/v1/hosts/${encodeURIComponent(hostId)}/agent/react`;

    return consumeSSEStream(
      url,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify({
          prompt: params.prompt,
          terminal_buffer: params.terminalBuffer || '',
          session_id: params.sessionId,
          permission_mode: params.permissionMode || 'request_approval',
          approved_commands: params.approvedCommands || [],
          max_steps: params.maxSteps || 10,
          branch_from_session_id: params.branchFromSessionId,
          branch_step_idx: params.branchStepIdx,
          branch_title: params.branchTitle,
          images: params.images && params.images.length > 0 ? params.images : undefined,
        }),
      },
      callbacks,
      abortSignal
    );
  },

  /**
   * 接续服务端仍在后台运行的排障任务（浏览器刷新/断线后恢复实时进度）
   */
  async attachReAct(
    hostId: string,
    params: { sessionId?: string; after?: number },
    callbacks: StreamCallbacks,
    abortSignal?: AbortSignal
  ): Promise<void> {
    const query = new URLSearchParams();
    if (params.sessionId) query.set('session_id', params.sessionId);
    if (params.after !== undefined) query.set('after', String(params.after));
    const url = `${API_BASE_URL}/api/v1/hosts/${encodeURIComponent(hostId)}/agent/react/stream?${query.toString()}`;

    return consumeSSEStream(
      url,
      {
        method: 'GET',
        headers: { Accept: 'text/event-stream' },
      },
      callbacks,
      abortSignal
    );
  },

  /**
   * 查询指定会话是否仍有排障任务在服务端后台运行
   */
  async getReActStatus(
    hostId: string,
    sessionId?: string
  ): Promise<{ running: boolean; task_key: string }> {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    const res = await apiClient.get(
      `/api/v1/hosts/${encodeURIComponent(hostId)}/agent/react/status${query}`
    );
    return res.data;
  },

  /**
   * 主动取消服务端后台运行的排障任务
   */
  async cancelReAct(hostId: string, sessionId?: string): Promise<{ cancelled: boolean }> {
    const res = await apiClient.post(
      `/api/v1/hosts/${encodeURIComponent(hostId)}/agent/react/cancel`,
      sessionId ? { session_id: sessionId } : {}
    );
    return res.data;
  },

  /**
   * 提交高危命令的人机协同审批决定
   */
  async submitApproval(
    hostId: string,
    payload: {
      approvalId: string;
      approved: boolean;
      scope?: 'once' | 'session';
    }
  ): Promise<{ status: string; approved: boolean }> {
    const res = await apiClient.post(
      `/api/v1/hosts/${encodeURIComponent(hostId)}/agent/react/approve`,
      {
        approval_id: payload.approvalId,
        approved: payload.approved,
        scope: payload.scope || 'once',
      }
    );
    return res.data;
  },
};
