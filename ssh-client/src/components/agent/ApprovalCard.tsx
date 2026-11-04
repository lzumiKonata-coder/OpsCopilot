import React, { useState } from 'react';
import { AlertTriangle, Check, X, ShieldAlert, Copy, CheckCheck } from 'lucide-react';
import { agentService } from '../../services/agentService';

interface ApprovalCardProps {
  command: string;
  reason?: string;
  riskLevel?: string;
  approvalId?: string;
  onDecision: (approved: boolean, scope: 'once' | 'session') => void;
  resolvedStatus?: 'approved' | 'rejected' | null;
}

export const ApprovalCard: React.FC<ApprovalCardProps> = ({
  command,
  reason,
  riskLevel = 'HIGH',
  onDecision,
  resolvedStatus,
}) => {
  const [copied, setCopied] = useState(false);

  // 前端事前风险评估提示
  const riskNotice = agentService.getCommandRiskNotice(command);

  const handleCopy = () => {
    navigator.clipboard.writeText(command);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      style={{
        margin: '12px 0',
        backgroundColor: '#1b1414',
        border: '1px solid #da3633',
        borderRadius: '8px',
        overflow: 'hidden',
        boxShadow: '0 4px 16px rgba(218, 54, 51, 0.15)',
      }}
    >
      {/* 头部标题条 */}
      <div
        style={{
          padding: '10px 14px',
          backgroundColor: 'rgba(218, 54, 51, 0.2)',
          borderBottom: '1px solid rgba(218, 54, 51, 0.4)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#ff7b72' }}>
          <ShieldAlert size={16} />
          <span style={{ fontWeight: 600, fontSize: '13px' }}>
            关键指令人工审批 (高危安全门禁)
          </span>
        </div>
        <span
          style={{
            fontSize: '11px',
            padding: '2px 8px',
            borderRadius: '10px',
            backgroundColor: '#da3633',
            color: '#fff',
            fontWeight: 600,
          }}
        >
          {riskLevel || 'HIGH RISK'}
        </span>
      </div>

      <div style={{ padding: '14px' }}>
        {/* 醒目的风险研判提示条 */}
        <div
          style={{
            padding: '10px 12px',
            borderRadius: '6px',
            backgroundColor: 'rgba(210, 153, 34, 0.15)',
            border: '1px solid rgba(210, 153, 34, 0.35)',
            color: '#e3b341',
            fontSize: '12px',
            lineHeight: '1.5',
            marginBottom: '12px',
            display: 'flex',
            gap: '8px',
          }}
        >
          <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: '2px' }} />
          <div>
            <div style={{ fontWeight: 600, marginBottom: '2px' }}>
              【风险提示】{riskNotice.title}
            </div>
            <div>{reason || riskNotice.desc}</div>
          </div>
        </div>

        {/* 待执行命令行展示区 */}
        <div style={{ marginBottom: '14px' }}>
          <div
            style={{
              fontSize: '11px',
              color: 'var(--text-muted)',
              marginBottom: '6px',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <span>待执行的高危命令：</span>
            <button
              onClick={handleCopy}
              className="btn btn-sm"
              style={{ padding: '2px 8px', fontSize: '11px' }}
            >
              {copied ? <CheckCheck size={12} color="#3fb950" /> : <Copy size={12} />}
              <span>{copied ? '已复制' : '复制命令'}</span>
            </button>
          </div>
          <div
            style={{
              padding: '10px 12px',
              backgroundColor: '#090d13',
              border: '1px solid var(--border)',
              borderRadius: '6px',
              fontFamily: 'var(--font-mono)',
              fontSize: '13px',
              color: '#f0f6fc',
              wordBreak: 'break-all',
              whiteSpace: 'pre-wrap',
            }}
          >
            <span style={{ color: '#ff7b72', marginRight: '6px' }}>$</span>
            <code>{command}</code>
          </div>
        </div>

        {/* 审批决断状态或按钮区 */}
        {resolvedStatus ? (
          <div
            style={{
              padding: '10px 14px',
              borderRadius: '6px',
              fontSize: '13px',
              fontWeight: 500,
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              backgroundColor:
                resolvedStatus === 'approved'
                  ? 'rgba(46, 160, 67, 0.15)'
                  : 'rgba(248, 81, 73, 0.15)',
              color: resolvedStatus === 'approved' ? '#3fb950' : '#f85149',
              border: `1px solid ${
                resolvedStatus === 'approved'
                  ? 'rgba(46, 160, 67, 0.3)'
                  : 'rgba(248, 81, 73, 0.3)'
              }`,
            }}
          >
            {resolvedStatus === 'approved' ? <Check size={16} /> : <X size={16} />}
            <span>
              {resolvedStatus === 'approved'
                ? '您已批准授权该命令，系统已下发到终端执行。'
                : '您已取消执行该高危命令，排障流程已中止。'}
            </span>
          </div>
        ) : (
          <div>
            <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
              <button
                type="button"
                className="btn"
                style={{
                  padding: '8px 16px',
                  backgroundColor: 'var(--bg-card)',
                  borderColor: 'var(--border)',
                  color: 'var(--text-main)',
                }}
                onClick={() => onDecision(false, 'once')}
              >
                <X size={14} />
                <span>取消执行</span>
              </button>

              <button
                type="button"
                className="btn btn-danger"
                style={{
                  padding: '8px 18px',
                  fontWeight: 600,
                  backgroundColor: '#da3633',
                  color: '#fff',
                  border: '1px solid #f85149',
                }}
                onClick={() => onDecision(true, 'once')}
              >
                <AlertTriangle size={14} />
                <span>仍然执行</span>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
