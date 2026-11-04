import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { Server, Play, Zap, CheckCircle2, XCircle, Loader2, KeyRound, FileKey, Trash2, TriangleAlert, X } from 'lucide-react';
import type { Host } from '../../types';
import { hostService } from '../../services/hostService';

interface HostCardProps {
  host: Host;
  onRefresh?: () => void;
}

export const HostCard: React.FC<HostCardProps> = ({ host, onRefresh }) => {
  const [testing, setTesting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await hostService.testHost(host.host_id || host.ip);
      setTestResult(res);
    } catch (err: any) {
      setTestResult({ success: false, message: err.message || '测试失败' });
    } finally {
      setTesting(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    setTestResult(null);
    try {
      await hostService.deleteHost(host.host_id || host.ip);
      onRefresh?.();
    } catch (err: any) {
      setTestResult({ success: false, message: err.message || '删除失败' });
    } finally {
      setDeleting(false);
      setConfirmOpen(false);
    }
  };

  const closeConfirm = () => {
    if (!deleting) setConfirmOpen(false);
  };

  const isPassword =
    host.auth_type === 'PASSWORD' || host.auth_type === 1;

  return (
    <div
      style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderRadius: '10px',
        padding: '20px',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        gap: '16px',
        transition: 'all 0.2s ease',
      }}
    >
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Server size={18} color="#58a6ff" />
              <h3 style={{ fontSize: '16px', fontWeight: 600, color: '#58a6ff' }}>{host.name}</h3>
            </div>
            <div
              style={{
                fontSize: '13px',
                color: 'var(--text-muted)',
                fontFamily: 'var(--font-mono)',
                marginTop: '6px',
              }}
            >
              {host.username}@{host.ip}:{host.port}
            </div>
          </div>

          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              fontSize: '11px',
              padding: '3px 8px',
              borderRadius: '12px',
              backgroundColor: 'rgba(110, 118, 129, 0.2)',
              color: 'var(--text-muted)',
            }}
          >
            {isPassword ? <KeyRound size={12} /> : <FileKey size={12} />}
            {isPassword ? '密码认证' : '私钥认证'}
          </span>
        </div>

        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '12px' }}>
          超时: {host.connection_timeout}s
        </div>

        {testResult && (
          <div
            style={{
              marginTop: '12px',
              padding: '8px 12px',
              borderRadius: '6px',
              fontSize: '12px',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              backgroundColor: testResult.success ? 'rgba(46, 160, 67, 0.15)' : 'rgba(248, 81, 73, 0.15)',
              color: testResult.success ? '#3fb950' : '#f85149',
              border: `1px solid ${testResult.success ? 'rgba(46, 160, 67, 0.3)' : 'rgba(248, 81, 73, 0.3)'}`,
            }}
          >
            {testResult.success ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
            <span>{testResult.message}</span>
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: '10px', marginTop: 'auto' }}>
        <button
          className="btn"
          style={{ flex: 1, padding: '8px' }}
          onClick={handleTest}
          disabled={testing}
        >
          {testing ? <Loader2 size={14} className="spin-animate" /> : <Zap size={14} />}
          <span>{testing ? '检测中...' : '测试连通'}</span>
        </button>

        <Link
          to={`/terminal/${encodeURIComponent(host.host_id || host.ip)}`}
          className="btn btn-primary"
          style={{ flex: 1.2, padding: '8px', textDecoration: 'none' }}
        >
          <Play size={14} />
          <span>打开终端</span>
        </Link>

        <button
          className="btn btn-danger"
          style={{ padding: '8px 10px' }}
          onClick={() => setConfirmOpen(true)}
          disabled={deleting || testing}
          title="删除主机"
        >
          {deleting ? <Loader2 size={14} className="spin-animate" /> : <Trash2 size={14} />}
        </button>
      </div>

      {/* 删除确认弹窗 */}
      {confirmOpen && (
        <div className="modal-backdrop" onClick={closeConfirm}>
          <div
            className="modal-card"
            style={{ maxWidth: '420px' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <TriangleAlert size={18} color="#f85149" />
                <h3 style={{ fontSize: '16px', fontWeight: 600 }}>删除主机确认</h3>
              </div>
              <button
                className="btn btn-icon"
                onClick={closeConfirm}
                disabled={deleting}
                style={{ background: 'transparent', border: 'none' }}
              >
                <X size={18} />
              </button>
            </div>
            <div className="modal-body">
              <p style={{ fontSize: '14px', color: 'var(--text-main)', lineHeight: 1.6 }}>
                确定删除主机「{host.name}」吗？
              </p>
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '8px' }}>
                目标：{host.username}@{host.ip}:{host.port}
              </p>
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '8px' }}>
                删除后该主机将不再显示在资产列表中。
              </p>
            </div>
            <div className="modal-footer">
              <button type="button" className="btn" onClick={closeConfirm} disabled={deleting}>
                取消
              </button>
              <button type="button" className="btn btn-danger" onClick={handleDelete} disabled={deleting}>
                {deleting ? <Loader2 size={14} className="spin-animate" /> : <Trash2 size={14} />}
                <span>{deleting ? '删除中...' : '确认删除'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
