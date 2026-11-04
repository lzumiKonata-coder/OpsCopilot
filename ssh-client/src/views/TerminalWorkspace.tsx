import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Plus, Server, Loader2, X } from 'lucide-react';
import type { Host } from '../types';
import { hostService } from '../services/hostService';
import { TerminalHostWorkspace } from './TerminalHostWorkspace';

interface WorkspaceTab {
  id: string;
  hostId: string;
  hostName: string;
}

/**
 * 终端工作区外壳：多主机标签页
 * 每个标签是一个独立的 TerminalHostWorkspace（终端 + Agent 面板状态互不干扰）
 */
export const TerminalWorkspace: React.FC = () => {
  const { hostId } = useParams<{ hostId: string }>();
  const navigate = useNavigate();

  const [tabs, setTabs] = useState<WorkspaceTab[]>(() =>
    hostId ? [{ id: hostId, hostId, hostName: hostId }] : []
  );
  const [activeId, setActiveId] = useState<string>(hostId || '');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [hosts, setHosts] = useState<Host[]>([]);
  const [loadingHosts, setLoadingHosts] = useState(false);

  // 路由变更时确保对应主机标签存在
  useEffect(() => {
    if (!hostId) return;
    setTabs((prev) =>
      prev.some((tab) => tab.id === hostId)
        ? prev
        : [...prev, { id: hostId, hostId, hostName: hostId }]
    );
    setActiveId(hostId);
  }, [hostId]);

  // 补全标签显示的主机别名
  useEffect(() => {
    const missing = tabs.filter((tab) => tab.hostName === tab.hostId);
    if (missing.length === 0) return;
    let cancelled = false;
    (async () => {
      const resolved: Record<string, string> = {};
      for (const tab of missing) {
        try {
          const host = await hostService.getHost(tab.hostId);
          if (host?.name) resolved[tab.id] = host.name;
        } catch {
          // 保留 hostId 作为名称
        }
      }
      if (!cancelled && Object.keys(resolved).length > 0) {
        setTabs((prev) =>
          prev.map((tab) =>
            resolved[tab.id] ? { ...tab, hostName: resolved[tab.id] } : tab
          )
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tabs]);

  const openHostPicker = async () => {
    setPickerOpen(true);
    if (hosts.length > 0) return;
    setLoadingHosts(true);
    try {
      setHosts(await hostService.getHosts());
    } catch (err) {
      console.error('Failed to fetch hosts:', err);
    } finally {
      setLoadingHosts(false);
    }
  };

  const addHostTab = (host: Host) => {
    const targetId = host.host_id || host.ip;
    if (!tabs.some((tab) => tab.id === targetId)) {
      setTabs((prev) => [...prev, { id: targetId, hostId: targetId, hostName: host.name }]);
    }
    setActiveId(targetId);
    setPickerOpen(false);
  };

  const closeTab = (id: string) => {
    const remaining = tabs.filter((tab) => tab.id !== id);
    if (remaining.length === 0) {
      setTabs([]);
      navigate('/');
      return;
    }
    if (activeId === id) {
      setActiveId(remaining[remaining.length - 1].id);
    }
    setTabs(remaining);
  };

  const availableHosts = hosts.filter(
    (host) => !tabs.some((tab) => tab.hostId === (host.host_id || host.ip))
  );

  return (
    <div className="terminal-tabs-shell">
      <div className="terminal-tabs-bar">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            className={`terminal-tab ${tab.id === activeId ? 'active' : ''}`}
            onClick={() => setActiveId(tab.id)}
            title={tab.hostName}
          >
            <Server size={13} />
            <span className="terminal-tab-label">{tab.hostName}</span>
            <button
              type="button"
              className="terminal-tab-close"
              title="关闭标签页"
              onClick={(e) => {
                e.stopPropagation();
                closeTab(tab.id);
              }}
            >
              <X size={12} />
            </button>
          </div>
        ))}
        <button
          type="button"
          className="terminal-tab-add"
          title="打开其他主机终端"
          onClick={openHostPicker}
        >
          <Plus size={15} />
        </button>
      </div>

      <div className="terminal-tabs-body">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            className="terminal-tab-panel"
            style={{
              visibility: tab.id === activeId ? 'visible' : 'hidden',
              pointerEvents: tab.id === activeId ? 'auto' : 'none',
              zIndex: tab.id === activeId ? 1 : 0,
            }}
          >
            <TerminalHostWorkspace hostId={tab.hostId} isActive={tab.id === activeId} />
          </div>
        ))}
      </div>

      {pickerOpen && (
        <div className="modal-backdrop" onClick={() => setPickerOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Server size={18} color="#58a6ff" />
                <h3 style={{ fontSize: '16px', fontWeight: 600 }}>打开主机终端</h3>
              </div>
              <button
                className="btn btn-icon"
                onClick={() => setPickerOpen(false)}
                style={{ background: 'transparent', border: 'none' }}
              >
                <X size={18} />
              </button>
            </div>
            <div className="modal-body">
              {loadingHosts ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)' }}>
                  <Loader2 size={16} className="spin-animate" />
                  <span>正在加载主机列表...</span>
                </div>
              ) : availableHosts.length > 0 ? (
                <div className="host-picker-list">
                  {availableHosts.map((host) => {
                    const targetId = host.host_id || host.ip;
                    return (
                      <button
                        key={targetId}
                        type="button"
                        className="host-picker-item"
                        onClick={() => addHostTab(host)}
                      >
                        <Server size={15} color="#58a6ff" />
                        <span className="host-picker-name">{host.name}</span>
                        <span className="host-picker-target">
                          {host.username}@{host.ip}:{host.port}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
                  已打开全部主机，或暂无可用的主机资产。
                </p>
              )}
            </div>
            <div className="modal-footer">
              <button type="button" className="btn" onClick={() => setPickerOpen(false)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
