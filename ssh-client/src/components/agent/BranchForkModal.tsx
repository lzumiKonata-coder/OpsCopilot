import React, { useState } from 'react';
import { GitFork, X, Sparkles } from 'lucide-react';

interface BranchForkModalProps {
  isOpen: boolean;
  stepIdx: number;
  initialPrompt?: string;
  onClose: () => void;
  onSubmit: (branchTitle: string, newPrompt: string) => void;
}

export const BranchForkModal: React.FC<BranchForkModalProps> = ({
  isOpen,
  stepIdx,
  initialPrompt = '',
  onClose,
  onSubmit,
}) => {
  const [branchTitle, setBranchTitle] = useState(`步骤 ${stepIdx} 分支探索`);
  const [prompt, setPrompt] = useState(initialPrompt);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim()) return;
    onSubmit(branchTitle.trim() || `步骤 ${stepIdx} 分支`, prompt.trim());
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(4px)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '20px',
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: '100%',
          maxWidth: '480px',
          backgroundColor: '#161b22',
          border: '1px solid #30363d',
          borderRadius: '10px',
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.6)',
          overflow: 'hidden',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div
          style={{
            padding: '14px 18px',
            backgroundColor: '#1b2129',
            borderBottom: '1px solid #30363d',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#58a6ff' }}>
            <GitFork size={18} />
            <span style={{ fontWeight: 600, fontSize: '14px', color: '#f0f6fc' }}>
              切出独立排障分支 (切入第 {stepIdx} 步)
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: '#8b949e',
              cursor: 'pointer',
              padding: '4px',
            }}
          >
            <X size={16} />
          </button>
        </div>

        {/* 表单内容 */}
        <form onSubmit={handleSubmit} style={{ padding: '16px 18px' }}>
          <p style={{ fontSize: '12px', color: '#8b949e', margin: '0 0 14px 0', lineHeight: '1.5' }}>
            分支将继承第 1 ~ {stepIdx} 步的环境上下文与排查结论，并在全新的隔离会话中探索替代策略。
          </p>

          <div style={{ marginBottom: '14px' }}>
            <label
              style={{
                display: 'block',
                fontSize: '12px',
                fontWeight: 600,
                color: '#c9d1d9',
                marginBottom: '6px',
              }}
            >
              分支标题
            </label>
            <input
              type="text"
              value={branchTitle}
              onChange={(e) => setBranchTitle(e.target.value)}
              className="form-control"
              style={{
                width: '100%',
                height: '34px',
                backgroundColor: '#090d13',
                border: '1px solid #30363d',
                borderRadius: '6px',
                color: '#f0f6fc',
                fontSize: '13px',
                padding: '0 10px',
              }}
              placeholder={`步骤 ${stepIdx} 分支探索`}
            />
          </div>

          <div style={{ marginBottom: '18px' }}>
            <label
              style={{
                display: 'block',
                fontSize: '12px',
                fontWeight: 600,
                color: '#c9d1d9',
                marginBottom: '6px',
              }}
            >
              新分支排障目标或替代策略 <span style={{ color: '#f85149' }}>*</span>
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              required
              rows={4}
              style={{
                width: '100%',
                backgroundColor: '#090d13',
                border: '1px solid #30363d',
                borderRadius: '6px',
                color: '#f0f6fc',
                fontSize: '13px',
                padding: '8px 10px',
                resize: 'vertical',
                lineHeight: '1.4',
                fontFamily: 'inherit',
              }}
              placeholder="输入在此分支尝试的新排障方案，例如：尝试回滚配置并检查端口占用情况..."
            />
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
            <button
              type="button"
              onClick={onClose}
              className="btn"
              style={{
                padding: '6px 14px',
                backgroundColor: '#21262d',
                borderColor: '#30363d',
                color: '#c9d1d9',
                fontSize: '12.5px',
              }}
            >
              取消
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              style={{
                padding: '6px 16px',
                gap: '6px',
                fontSize: '12.5px',
                fontWeight: 600,
              }}
            >
              <Sparkles size={13} />
              <span>启动分支推演</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
