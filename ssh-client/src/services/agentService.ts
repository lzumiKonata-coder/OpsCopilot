import { API_BASE_URL, apiClient } from './api';
import type { CommandRiskNotice, PermissionMode, ReActEvent } from '../types';

interface StreamCallbacks {
  onEvent: (event: ReActEvent) => void;
  onError: (err: Error) => void;
  onDone: () => void;
}

/**
 * 统一消费 SSE 响应流（POST 发起与 GET 接续共用）
 */
async function consumeSSEStream(
  url: string,
  init: RequestInit,
  callbacks: StreamCallbacks,
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
          const event: ReActEvent = JSON.parse(dataStr);
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
   * 研判高危命令风险等级与影响说明（前端事前风险评估，供展示与提示，不强制阻断）
   */
  getCommandRiskNotice(cmd: string): CommandRiskNotice {
    const trimmed = (cmd || '').trim();
    if (!trimmed) {
      return { isDangerous: false, level: 'INFO', title: '常规指令', desc: '无已知高风险。' };
    }

    // 格式化或高危磁盘破坏
    if (/\b(?:mkfs|fdisk|parted|dd\s+if=.*of=\/dev\/[a-z]+)\b/i.test(trimmed)) {
      return {
        isDangerous: true,
        level: 'HIGH',
        title: '高危破坏性磁盘操作',
        desc: '检测到底层文件系统格式化或块设备直接写入命令，操作极具破坏性，可能导致整盘数据彻底丢失！',
      };
    }

    // 递归强制删除
    if (/\brm\s+-(?:r[fF]|[fF]r|R)\s+/i.test(trimmed)) {
      return {
        isDangerous: true,
        level: 'HIGH',
        title: '高危递归强制删除',
        desc: '检测到 `rm -rf` 递归强制删除操作，将直接永久移除指定目录下的全部内容。请审视目标路径是否绝对正确！',
      };
    }

    // 关机、重启
    if (/\b(?:reboot|shutdown|init\s+0|poweroff|halt)\b/i.test(trimmed)) {
      return {
        isDangerous: true,
        level: 'HIGH',
        title: '系统级关停/重启',
        desc: '该指令将立即中断主机操作系统，当前所有运行中的服务与终端连接都将强制断开。',
      };
    }

    // 强杀所有或系统 PID
    if (/\bkill\s+-9\s+(?:1|-1)\b|\bkillall\b/i.test(trimmed)) {
      return {
        isDangerous: true,
        level: 'HIGH',
        title: '危险进程批量强杀',
        desc: '可能引起操作系统核心服务瘫痪或系统崩溃。',
      };
    }

    // 常见变更操作 (中危)
    if (/\b(?:systemctl\s+(?:stop|restart)|docker\s+rm\s+-f|iptables\s+-F)\b/i.test(trimmed)) {
      return {
        isDangerous: true,
        level: 'MEDIUM',
        title: '关键服务变更或防火墙重置',
        desc: '该操作可能会造成当前业务网络瞬断或容器被强行销毁。',
      };
    }

    return {
      isDangerous: false,
      level: 'INFO',
      title: '常规运维操作',
      desc: '属于受管范围内的安全探查或普通操作。',
    };
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

  /**
   * 智能单步回显研判
   */
  async analyzeStep(
    hostId: string,
    params: { stepNumber: number; command: string; observation: string }
  ): Promise<{ step_number: number; command: string; analysis: string; is_normal: boolean }> {
    const res = await apiClient.post(
      `/api/v1/hosts/${encodeURIComponent(hostId)}/agent/step-analyze`,
      {
        step_number: params.stepNumber,
        command: params.command,
        observation: params.observation,
      }
    );
    return res.data;
  },
};
