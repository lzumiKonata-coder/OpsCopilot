import axios from 'axios';

// 优先采用环境变量配置，默认直连 8000 端口（支持跨域 CORS 和反向代理）
export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const errorMsg =
      error.response?.data?.message ||
      error.response?.data?.detail ||
      error.message ||
      '网络请求失败';
    console.error('[API Error]:', errorMsg);
    return Promise.reject(new Error(errorMsg));
  }
);

/**
 * 获取 WebSocket 终端连接 URL
 */
export function getWebSocketTerminalUrl(
  hostId: string,
  cols: number = 100,
  rows: number = 30
): string {
  const customWsBase = import.meta.env.VITE_WS_BASE_URL;
  if (customWsBase) {
    return `${customWsBase}/ws/hosts/${encodeURIComponent(hostId)}/terminal?cols=${cols}&rows=${rows}`;
  }

  // 根据当前协议与目标自动构造
  const isHttps = window.location.protocol === 'https:';
  const proto = isHttps ? 'wss:' : 'ws:';
  
  if (API_BASE_URL.startsWith('http')) {
    const parsed = new URL(API_BASE_URL);
    const wsProto = parsed.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProto}//${parsed.host}/ws/hosts/${encodeURIComponent(hostId)}/terminal?cols=${cols}&rows=${rows}`;
  }

  return `${proto}//${window.location.host}/ws/hosts/${encodeURIComponent(hostId)}/terminal?cols=${cols}&rows=${rows}`;
}
