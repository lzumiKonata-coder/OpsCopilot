import React from 'react';
import { RefreshCw, Trash2, Server, Sparkles } from 'lucide-react';
import type { Host } from '../../types';
import { Tooltip } from '../common/Tooltip';

interface TerminalHeaderProps {
  host: Host | null;
  status: 'connecting' | 'connected' | 'disconnected' | 'error';
  isAgentPaneOpen: boolean;
  onToggleAgentPane: () => void;
  onReconnect: () => void;
  onClear: () => void;
}

export const TerminalHeader: React.FC<TerminalHeaderProps> = ({
  host,
  status,
  isAgentPaneOpen,
  onToggleAgentPane,
  onReconnect,
  onClear,
}) => {
  return (
    <div
      style={{
        height: '44px',
        backgroundColor: '#121314',
        borderBottom: '1px solid #2b2b2b',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 14px',
        userSelect: 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <Server size={15} color="#8b949e" />
        <span style={{ fontSize: '13px', fontWeight: 600, color: '#f0f0f0' }}>
          {host?.name || '远程主机'}
        </span>
        <span
          style={{
            fontSize: '12px',
            color: '#aab4c0',
            fontFamily: 'var(--font-mono)',
          }}
        >
          {host ? `${host.username}@${host.ip}:${host.port}` : ''}
        </span>

        <div className={`status-pill ${status}`}>
          <span style={{ fontSize: '7px' }}>●</span>
          <span>{status.toUpperCase()}</span>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <button
          className="agent-top-btn"
          title="清空终端屏幕"
          onClick={onClear}
        >
          <Trash2 size={14} />
        </button>

        <button
          className="agent-top-btn"
          title="重新连接 SSH 终端"
          onClick={onReconnect}
        >
          <RefreshCw size={14} />
        </button>

        {/* 当探索版收起时，在终端顶栏提供一键展开按钮 */}
        {!isAgentPaneOpen && (
          <Tooltip content="展开 OpsCopilot 探索版" placement="bottom">
            <button
              className="btn btn-sm"
              onClick={onToggleAgentPane}
              style={{
                backgroundColor: 'rgba(56, 139, 253, 0.15)',
                borderColor: 'rgba(56, 139, 253, 0.35)',
                color: '#58a6ff',
                padding: '3px 10px',
                gap: '5px',
                fontSize: '12px',
              }}
            >
              <Sparkles size={13} color="#58a6ff" />
              <span>探索版</span>
            </button>
          </Tooltip>
        )}
      </div>
    </div>
  );
};
