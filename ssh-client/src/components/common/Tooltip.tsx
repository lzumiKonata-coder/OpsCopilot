import React, { useState } from 'react';

interface TooltipProps {
  content: string;
  placement?: 'right' | 'bottom' | 'top' | 'left';
  children: React.ReactNode;
}

export const Tooltip: React.FC<TooltipProps> = ({
  content,
  placement = 'bottom',
  children,
}) => {
  const [visible, setVisible] = useState(false);

  return (
    <div
      style={{ position: 'relative', display: 'inline-flex' }}
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
    >
      {children}
      {visible && (
        <div
          className={`custom-tooltip tooltip-${placement}`}
          style={{
            position: 'absolute',
            zIndex: 9999,
            backgroundColor: '#1e1e1e',
            color: '#f0f0f0',
            border: '1px solid #333333',
            borderRadius: '4px',
            padding: '5px 9px',
            fontSize: '12px',
            lineHeight: '1.2',
            whiteSpace: 'nowrap',
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.6)',
            pointerEvents: 'none',
            ...(placement === 'right' && {
              left: 'calc(100% + 8px)',
              top: '50%',
              transform: 'translateY(-50%)',
            }),
            ...(placement === 'bottom' && {
              top: 'calc(100% + 8px)',
              left: '50%',
              transform: 'translateX(-50%)',
            }),
            ...(placement === 'top' && {
              bottom: 'calc(100% + 8px)',
              left: '50%',
              transform: 'translateX(-50%)',
            }),
            ...(placement === 'left' && {
              right: 'calc(100% + 8px)',
              top: '50%',
              transform: 'translateY(-50%)',
            }),
          }}
        >
          {/* 尖角箭头 */}
          <div
            style={{
              position: 'absolute',
              width: 0,
              height: 0,
              border: '5px solid transparent',
              ...(placement === 'right' && {
                right: '100%',
                top: '50%',
                transform: 'translateY(-50%)',
                borderRightColor: '#1e1e1e',
              }),
              ...(placement === 'bottom' && {
                bottom: '100%',
                left: '50%',
                transform: 'translateX(-50%)',
                borderBottomColor: '#1e1e1e',
              }),
              ...(placement === 'top' && {
                top: '100%',
                left: '50%',
                transform: 'translateX(-50%)',
                borderTopColor: '#1e1e1e',
              }),
              ...(placement === 'left' && {
                left: '100%',
                top: '50%',
                transform: 'translateY(-50%)',
                borderLeftColor: '#1e1e1e',
              }),
            }}
          />
          {content}
        </div>
      )}
    </div>
  );
};
