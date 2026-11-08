import { apiClient } from './api';
import type { AgentSessionSummary } from '../types';

export const sessionService = {
  /**
   * 获取指定主机的 OpsAgent 对话会话列表
   */
  async getSessions(hostId: string): Promise<AgentSessionSummary[]> {
    const res = await apiClient.get<AgentSessionSummary[]>(
      `/api/v1/hosts/${encodeURIComponent(hostId)}/agent/sessions`
    );
    return res.data;
  },

  /**
   * 为指定主机新建对话会话
   */
  async createSession(hostId: string, title?: string): Promise<AgentSessionSummary> {
    const res = await apiClient.post<AgentSessionSummary>(
      `/api/v1/hosts/${encodeURIComponent(hostId)}/agent/sessions`,
      { title: title || undefined }
    );
    return res.data;
  },

  /**
   * 获取会话全量历史详情（含消息记录）
   */
  async getSessionDetail(sessionId: string): Promise<any> {
    const res = await apiClient.get(`/api/v1/agent/sessions/${encodeURIComponent(sessionId)}`);
    return res.data;
  },

  /**
   * 删除会话
   */
  async deleteSession(sessionId: string): Promise<{ deleted: boolean; session_id: string }> {
    const res = await apiClient.delete(`/api/v1/agent/sessions/${encodeURIComponent(sessionId)}`);
    return res.data;
  },
};
