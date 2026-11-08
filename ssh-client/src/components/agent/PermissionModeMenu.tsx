import React from 'react';
import { AlertTriangle, Hand, Info, Check } from 'lucide-react';
import type { PermissionMode } from '../../types';

interface PermissionModeMenuProps {
  isOpen: boolean;
  permissionMode: PermissionMode;
  menuRef: React.RefObject<HTMLDivElement | null>;
  onChange: (mode: PermissionMode) => void;
}

/** 工具权限模式弹出菜单 */
export const PermissionModeMenu: React.FC<PermissionModeMenuProps> = ({
  isOpen,
  permissionMode,
  menuRef,
  onChange,
}) => {
  if (!isOpen) return null;

  return (
    <div ref={menuRef} className="permission-popup-menu permission-mode-popup">
      <div className="permission-popup-title">工具权限模式</div>

      {/* 选项 1: 请求批准 */}
      <div
        className={`permission-option-item ${permissionMode === 'request_approval' ? 'active' : ''}`}
        onClick={() => onChange('request_approval')}
      >
        <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
          <Hand size={14} color="#f0f0f0" style={{ marginTop: '2px', flexShrink: 0 }} />
          <div>
            <div className="permission-option-title">
              <span>请求批准</span>
              <Info size={11} color="#8c8c8c" />
            </div>
            <div className="permission-option-desc">只读放行，写入和执行需你确认</div>
          </div>
        </div>
        {permissionMode === 'request_approval' && (
          <Check size={14} color="#ffffff" style={{ flexShrink: 0 }} />
        )}
      </div>

      {/* 选项 2: 完全访问权限 */}
      <div
        className={`permission-option-item ${permissionMode === 'full_access' ? 'active' : ''}`}
        onClick={() => onChange('full_access')}
      >
        <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
          <AlertTriangle size={14} color="#ff9900" style={{ marginTop: '2px', flexShrink: 0 }} />
          <div>
            <div className="permission-option-title" style={{ color: '#ff9900' }}>
              <span>完全访问权限</span>
              <Info size={11} color="#ff9900" />
            </div>
            <div className="permission-option-desc">
              仅平台硬性规则拦截
            </div>
          </div>
        </div>
        {permissionMode === 'full_access' && (
          <Check size={14} color="#ffffff" style={{ flexShrink: 0 }} />
        )}
      </div>
    </div>
  );
};
