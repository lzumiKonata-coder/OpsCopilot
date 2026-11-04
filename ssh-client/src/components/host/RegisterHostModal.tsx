import React, { useEffect, useRef, useState } from 'react';
import { X, Server, KeyRound, FileKey, Loader2, AlertCircle } from 'lucide-react';
import type { RegisterHostPayload } from '../../types';
import { hostService } from '../../services/hostService';

interface RegisterHostModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

const INITIAL_FORM: RegisterHostPayload = {
  name: '',
  ip: '',
  port: 22,
  username: 'root',
  auth_type: 'PASSWORD',
  password: '',
  private_key: '',
  passphrase: '',
  connection_timeout: 10,
};

export const RegisterHostModal: React.FC<RegisterHostModalProps> = ({ isOpen, onClose, onSuccess }) => {
  const [formData, setFormData] = useState<RegisterHostPayload>(INITIAL_FORM);

  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [keyFileName, setKeyFileName] = useState<string | null>(null);
  const keyInputRef = useRef<HTMLInputElement>(null);

  // 每次打开弹窗都重置表单，避免上次登记的内容残留
  useEffect(() => {
    if (!isOpen) return;
    setFormData(INITIAL_FORM);
    setSubmitting(false);
    setErrorMsg(null);
    setKeyFileName(null);
    if (keyInputRef.current) keyInputRef.current.value = '';
  }, [isOpen]);

  if (!isOpen) return null;

  const handleKeyFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const content = await file.text();
      setFormData((prev) => ({ ...prev, private_key: content }));
      setKeyFileName(file.name);
      setErrorMsg(null);
    } catch {
      setErrorMsg('私钥文件读取失败，请重新选择');
    }
  };

  const clearKeyFile = () => {
    setFormData((prev) => ({ ...prev, private_key: '' }));
    setKeyFileName(null);
    if (keyInputRef.current) keyInputRef.current.value = '';
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);

    if (formData.auth_type === 'PRIVATE_KEY') {
      const privateKey = formData.private_key || '';
      if (!privateKey.trim()) {
        setErrorMsg('请上传 SSH 私钥文件');
        return;
      }
      if (!privateKey.includes('PRIVATE KEY')) {
        setErrorMsg('私钥文件内容无效，请上传 OpenSSH 格式的私钥文件');
        return;
      }
    }

    setSubmitting(true);

    try {
      await hostService.registerHost(formData);
      onSuccess();
      onClose();
    } catch (err: any) {
      setErrorMsg(err.message || '登记主机资产失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Server size={18} color="#58a6ff" />
            <h3 style={{ fontSize: '16px', fontWeight: 600 }}>登记目标主机资产</h3>
          </div>
          <button className="btn btn-icon" onClick={onClose} style={{ background: 'transparent', border: 'none' }}>
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {errorMsg && (
              <div
                style={{
                  marginBottom: '16px',
                  padding: '10px 14px',
                  borderRadius: '6px',
                  backgroundColor: 'rgba(248, 81, 73, 0.15)',
                  border: '1px solid rgba(248, 81, 73, 0.3)',
                  color: '#f85149',
                  fontSize: '13px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                }}
              >
                <AlertCircle size={16} />
                <span>{errorMsg}</span>
              </div>
            )}

            <div className="form-group">
              <label>主机别名 / 描述名称 *</label>
              <input
                className="form-control"
                placeholder="如：hadoop100 或 k8s-master-01"
                required
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              />
            </div>

            <div style={{ display: 'flex', gap: '12px' }}>
              <div className="form-group" style={{ flex: 3 }}>
                <label>IP 地址 / 主机名 *</label>
                <input
                  className="form-control"
                  placeholder="如：192.168.132.100"
                  required
                  value={formData.ip}
                  onChange={(e) => setFormData({ ...formData, ip: e.target.value })}
                />
              </div>
              <div className="form-group" style={{ flex: 1.2 }}>
                <label>端口 *</label>
                <input
                  type="number"
                  className="form-control"
                  required
                  value={formData.port}
                  onChange={(e) => setFormData({ ...formData, port: parseInt(e.target.value) || 22 })}
                />
              </div>
            </div>

            <div className="form-group">
              <label>SSH 登录用户名 *</label>
              <input
                className="form-control"
                placeholder="如：root 或 ubuntu"
                required
                value={formData.username}
                onChange={(e) => setFormData({ ...formData, username: e.target.value })}
              />
            </div>

            <div className="form-group">
              <label>认证方式</label>
              <div style={{ display: 'flex', gap: '16px', marginTop: '6px' }}>
                <label
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    cursor: 'pointer',
                    fontSize: '13px',
                  }}
                >
                  <input
                    type="radio"
                    name="auth_type"
                    checked={formData.auth_type === 'PASSWORD'}
                    onChange={() => setFormData({ ...formData, auth_type: 'PASSWORD' })}
                  />
                  <KeyRound size={14} color="#e3b341" />
                  <span>密码认证</span>
                </label>

                <label
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    cursor: 'pointer',
                    fontSize: '13px',
                  }}
                >
                  <input
                    type="radio"
                    name="auth_type"
                    checked={formData.auth_type === 'PRIVATE_KEY'}
                    onChange={() => setFormData({ ...formData, auth_type: 'PRIVATE_KEY' })}
                  />
                  <FileKey size={14} color="#58a6ff" />
                  <span>私钥认证 (SSH Key)</span>
                </label>
              </div>
            </div>

            {formData.auth_type === 'PASSWORD' ? (
              <div className="form-group">
                <label>登录密码 *</label>
                <input
                  type="password"
                  className="form-control"
                  placeholder="请输入主机 SSH 密码"
                  required
                  value={formData.password}
                  onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                />
              </div>
            ) : (
              <>
                <div className="form-group">
                  <label>OpenSSH 私钥文件 *</label>
                  <input
                    ref={keyInputRef}
                    type="file"
                    style={{ display: 'none' }}
                    onChange={handleKeyFileChange}
                  />
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => keyInputRef.current?.click()}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') keyInputRef.current?.click();
                    }}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '10px',
                      padding: '12px 14px',
                      borderRadius: '6px',
                      border: `1px dashed ${keyFileName ? 'rgba(56, 139, 253, 0.6)' : '#444c56'}`,
                      backgroundColor: 'rgba(56, 139, 253, 0.06)',
                      cursor: 'pointer',
                      fontSize: '13px',
                    }}
                  >
                    <FileKey size={16} color="#58a6ff" />
                    <span
                      style={{
                        flex: 1,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        color: keyFileName ? 'var(--text-main)' : 'var(--text-muted)',
                      }}
                    >
                      {keyFileName || '点击选择私钥文件（如 id_rsa / id_ed25519）'}
                    </span>
                    {keyFileName && (
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          clearKeyFile();
                        }}
                      >
                        移除
                      </button>
                    )}
                  </div>
                  <p style={{ fontSize: '12px', color: 'var(--text-dim)', marginTop: '6px' }}>
                    支持 PEM / OpenSSH 格式私钥文件，内容仅在登记时上传
                  </p>
                </div>
                <div className="form-group">
                  <label>私钥解锁口令 Passphrase (可选)</label>
                  <input
                    type="password"
                    className="form-control"
                    placeholder="若私钥已加密请填写"
                    value={formData.passphrase}
                    onChange={(e) => setFormData({ ...formData, passphrase: e.target.value })}
                  />
                </div>
              </>
            )}

            <div className="form-group">
              <label>连接超时时间 (秒)</label>
              <input
                type="number"
                className="form-control"
                min={1}
                max={60}
                value={formData.connection_timeout}
                onChange={(e) => setFormData({ ...formData, connection_timeout: parseInt(e.target.value) || 10 })}
              />
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose} disabled={submitting}>
              取消
            </button>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? <Loader2 size={14} className="spin-animate" /> : null}
              <span>{submitting ? '登记中...' : '确认登记'}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
