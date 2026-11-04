import React from 'react';
import { Sparkles, X } from 'lucide-react';

interface QuickPromptMenuProps {
  isOpen: boolean;
  prompts: string[];
  onSelect: (prompt: string) => void;
  onClose: () => void;
}

/** 常用运维快捷指令推荐菜单 */
export const QuickPromptMenu: React.FC<QuickPromptMenuProps> = ({
  isOpen,
  prompts,
  onSelect,
  onClose,
}) => {
  if (!isOpen) return null;

  return (
    <div
      style={{
        position: 'absolute',
        bottom: '100%',
        left: '16px',
        right: '16px',
        marginBottom: '8px',
        backgroundColor: '#222222',
        border: '1px solid #333333',
        borderRadius: '12px',
        padding: '8px',
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
        boxShadow: '0 8px 24px rgba(0, 0, 0, 0.5)',
        zIndex: 40,
      }}
    >
      <div
        style={{
          fontSize: '11px',
          color: 'var(--text-muted)',
          padding: '4px 8px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <span>常用运维快捷指令推荐</span>
        <span
          style={{ cursor: 'pointer', color: '#ccc', display: 'flex', alignItems: 'center' }}
          onClick={onClose}
        >
          <X size={12} />
        </span>
      </div>
      {prompts.map((qp, idx) => (
        <button
          key={idx}
          type="button"
          className="btn btn-sm"
          style={{
            justifyContent: 'flex-start',
            backgroundColor: 'transparent',
            border: 'none',
            color: '#d4d4d4',
            textAlign: 'left',
            padding: '6px 10px',
            borderRadius: '6px',
          }}
          onClick={() => onSelect(qp)}
        >
          <Sparkles size={12} color="#58a6ff" />
          <span>{qp}</span>
        </button>
      ))}
    </div>
  );
};
