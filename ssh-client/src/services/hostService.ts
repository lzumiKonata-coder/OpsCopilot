import { apiClient } from './api';
import type { Host, RegisterHostPayload } from '../types';

export const hostService = {
  /**
   * 获取所有注册的主机资产
   */
  async getHosts(): Promise<Host[]> {
    const res = await apiClient.get<Host[]>('/api/v1/hosts');
    return res.data;
  },

  /**
   * 根据 ID、别名或 IP 获取特定主机详情
   */
  async getHost(identifier: string): Promise<Host> {
    const res = await apiClient.get<Host>(`/api/v1/hosts/${encodeURIComponent(identifier)}`);
    return res.data;
  },

  /**
   * 注册登记新目标主机
   */
  async registerHost(payload: RegisterHostPayload): Promise<Host> {
    const res = await apiClient.post<Host>('/api/v1/hosts', {
      name: payload.name,
      connection_name: payload.name,
      ip: payload.ip,
      host: payload.ip,
      port: payload.port,
      username: payload.username,
      auth_type: payload.auth_type === 'PASSWORD' ? 1 : 2,
      password: payload.password || undefined,
      private_key: payload.private_key || undefined,
      passphrase: payload.passphrase || undefined,
      connection_timeout: payload.connection_timeout || 10,
    });
    return res.data;
  },

  /**
   * 删除指定目标主机（后端为软删除）
   */
  async deleteHost(identifier: string): Promise<void> {
    await apiClient.delete(`/api/v1/hosts/${encodeURIComponent(identifier)}`);
  },

  /**
   * 测试目标主机的 SSH 凭据与网络连通性
   */
  async testHost(identifier: string): Promise<{ success: boolean; message: string }> {
    try {
      const res = await apiClient.post(`/api/v1/hosts/${encodeURIComponent(identifier)}/test`);
      return { success: true, message: res.data?.message || 'SSH 连通性测试成功' };
    } catch (err: any) {
      return { success: false, message: err.message || 'SSH 连接测试失败' };
    }
  },
};
