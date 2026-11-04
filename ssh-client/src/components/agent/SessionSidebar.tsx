import React, { useState } from 'react';
import {
  MessageSquarePlus,
  Search,
  PanelLeft,
  Plus,
  Trash2,
  GitFork,
} from 'lucide-react';
import type { AgentSessionSummary } from '../../types';
import { Tooltip } from '../common/Tooltip';

interface SessionSidebarProps {
  sessions: AgentSessionSummary[];
  currentSessionId: string;
  onSelectSession: (session: AgentSessionSummary) => void;
  onCreateSession: () => void;
  onDeleteSession: (sessionId: string) => void;
  onClose: () => void;
}

function formatRelativeTime(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    if (diffMs < 0) return '刚刚';
    const diffMin = Math.floor(diffMs / (1000 * 60));
    const diffHour = Math.floor(diffMs / (1000 * 60 * 60));
    const diffDay = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    const diffMonth = Math.floor(diffDay / 30);

    if (diffMin < 2) return '刚刚';
    if (diffMin < 60) return `${diffMin} 分钟前`;
    if (diffHour < 24) return `${diffHour} 小时前`;
    if (diffDay < 30) return `${diffDay} 天前`;
    return `${diffMonth} 个月前`;
  } catch {
    return '刚刚';
  }
}

export const SessionSidebar: React.FC<SessionSidebarProps> = ({
  sessions,
  currentSessionId,
  onSelectSession,
  onCreateSession,
  onDeleteSession,
  onClose,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [showSearchInput, setShowSearchInput] = useState(false);
  const [hoveredSessionId, setHoveredSessionId] = useState<string | null>(null);

  const filteredSessions = sessions.filter((s) =>
    (s.title || '').toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="session-sidebar-container">
      {/* 顶部标题栏：左侧 AI 名称替换为 OpsCopilot Agent，右侧搜索与折叠 */}
      <div className="session-sidebar-header">
        <span className="session-sidebar-brand">OpsCopilot Agent</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <button
            className="agent-top-btn"
            title="搜索历史会话"
            onClick={() => setShowSearchInput(!showSearchInput)}
          >
            <Search size={15} />
          </button>
          <Tooltip content="收起会话列表" placement="right">
            <button className="agent-top-btn" onClick={onClose}>
              <PanelLeft size={16} />
            </button>
          </Tooltip>
        </div>
      </div>

      {/* 搜索框 (点击搜索图标展示) */}
      {showSearchInput && (
        <div style={{ padding: '0 12px 8px 12px' }}>
          <input
            className="form-control form-control-sm"
            placeholder="搜索会话标题..."
            autoFocus
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            style={{
              height: '28px',
              fontSize: '12px',
              backgroundColor: '#1f1f1f',
              border: '1px solid #333',
            }}
          />
        </div>
      )}

      {/* 新会话按钮 */}
      <div style={{ padding: '4px 12px 10px 12px' }}>
        <button
          className="new-session-full-btn"
          onClick={onCreateSession}
        >
          <MessageSquarePlus size={16} color="#d4d4d4" />
          <span>新会话</span>
        </button>
      </div>

      {/* 工作空间分区 */}
      <div className="session-sidebar-section">
        <div className="section-title-row">
          <span>工作空间</span>
          <button
            className="agent-top-btn"
            title="创建新工作空间"
            style={{ padding: '2px' }}
          >
            <Plus size={14} />
          </button>
        </div>
        <div className="empty-workspace-text">暂无工作空间</div>
      </div>

      {/* 会话分区 */}
      <div className="session-sidebar-section" style={{ flex: 1, overflowY: 'auto' }}>
        <div className="section-title-row">
          <span>会话</span>
        </div>

        <div className="session-list-items">
          {filteredSessions.length > 0 ? (
            filteredSessions.map((s) => {
              const isActive = s.session_id === currentSessionId;
              const isHovered = hoveredSessionId === s.session_id;

              return (
                <div
                  key={s.session_id}
                  className={`session-item-row ${isActive ? 'active' : ''}`}
                  onClick={() => onSelectSession(s)}
                  onMouseEnter={() => setHoveredSessionId(s.session_id)}
                  onMouseLeave={() => setHoveredSessionId(null)}
                >
                  <span
                    className="session-item-title"
                    title={s.title}
                    style={{ display: 'flex', alignItems: 'center', gap: '4px' }}
                  >
                    {(s.is_branch || s.parent_session_id || (s.title && s.title.includes('分支'))) && (
                      <GitFork size={12} color="#58a6ff" style={{ flexShrink: 0 }} />
                    )}
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {s.title}
                    </span>
                  </span>

                  {isHovered || isActive ? (
                    <button
                      className="session-delete-btn"
                      title="删除该会话"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteSession(s.session_id);
                      }}
                    >
                      <Trash2 size={13} color="#8c8c8c" />
                    </button>
                  ) : (
                    <span className="session-item-time">
                      {formatRelativeTime(s.updated_at || s.created_at)}
                    </span>
                  )}
                </div>
              );
            })
          ) : (
            <div style={{ padding: '16px 8px', fontSize: '12px', color: '#666', textAlign: 'center' }}>
              {searchTerm ? '无匹配会话' : '暂无历史会话'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
