import React, { useEffect, useRef, useState } from 'react';
import {
  ChevronRight,
  ChevronDown,
  X,
} from 'lucide-react';
import type { ReActStepData } from '../../types';
import { FileDiffViewer } from './FileDiffViewer';
import { CodeBlock, MemoizedMarkdown } from '../common/MemoizedMarkdown';

interface ReActStepCardProps {
  stepData: ReActStepData;
  onApprovalDecision?: (
    approvalId: string,
    approved: boolean,
    scope: 'once' | 'session'
  ) => void;
  onForkBranch?: (step: number) => void;
}

const ReActStepCardComponent: React.FC<ReActStepCardProps> = ({
  stepData,
}) => {
  const isRunning = stepData.status === 'running';
  const headerTitle = isRunning ? '深度思考...' : '深度思考';
  const scrollRef = useRef<HTMLDivElement>(null);

  const isPlanConfirmation =
    stepData.title === '方案确认' ||
    Boolean(stepData.plan) ||
    (!stepData.command && stepData.status === 'approval_required');

  const planContent =
    stepData.plan ||
    (isPlanConfirmation ? stepData.description || stepData.thought : undefined);

  const hasSeparateThought = Boolean(
    stepData.thought &&
      stepData.thought.trim() &&
      stepData.thought.trim() !== (planContent || '').trim()
  );

  // 运行中的步骤默认展开，已完成的步骤默认折叠
  const [isCollapsed, setIsCollapsed] = useState(!isRunning);
  const prevIsRunningRef = useRef(isRunning);

  // 当思考由运行中转为完成时，自动折叠
  useEffect(() => {
    if (prevIsRunningRef.current && !isRunning) {
      setIsCollapsed(true);
    }
    prevIsRunningRef.current = isRunning;
  }, [isRunning]);

  const isExpanded = !isCollapsed;

  const actionStatusLabel =
    stepData.status === 'approval_required' && !stepData.approved
      ? '待执行'
      : stepData.observation
      ? '已执行'
      : '执行';

  useEffect(() => {
    if (isRunning && scrollRef.current && isExpanded) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [stepData.thought, isRunning, isExpanded]);

  const toggleExpand = () => {
    setIsCollapsed((prev) => !prev);
  };

  return (
    <div
      className="react-step-card"
      style={{
        margin: '10px 0',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
      }}
    >
      {/* 1. 深度思考 (Thought) - 仅在存在独立推演思考过程时展示 */}
      {hasSeparateThought && (
        <div>
          <div
            onClick={toggleExpand}
            role="button"
            tabIndex={0}
            className="thinking-header-toggle"
            title={isExpanded ? '点击折叠思考过程' : '点击展开查看思考过程'}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              fontSize: '13.5px',
              fontWeight: 500,
              cursor: 'pointer',
              userSelect: 'none',
              marginBottom: isExpanded ? '6px' : '0px',
              color: '#8b949e',
            }}
          >
            {isExpanded ? (
              <ChevronDown size={14} style={{ flexShrink: 0 }} />
            ) : (
              <ChevronRight size={14} style={{ flexShrink: 0 }} />
            )}
            <span className={isRunning ? 'thinking-shimmer-text' : 'thinking-static-text'}>
              {headerTitle}
            </span>
          </div>

          <div
            ref={scrollRef}
            className="thinking-scroll-box"
            style={{
              display: isExpanded ? 'block' : 'none',
              maxHeight: '220px',
              overflowY: 'auto',
              padding: '10px 14px',
              backgroundColor: 'rgba(255, 255, 255, 0.02)',
              border: '1px solid var(--border-subtle, #2a2a2a)',
              borderRadius: '6px',
              fontSize: '13.5px',
              lineHeight: '1.6',
              color: '#c9d1d9',
            }}
          >
            <MemoizedMarkdown content={stepData.thought || ''} />
          </div>
        </div>
      )}

      {/* 步骤标题/说明 */}
      {stepData.title &&
        !stepData.title.startsWith('步骤 ') &&
        stepData.title !== '运维探测指令' &&
        stepData.title !== '排障完成' &&
        stepData.title !== '任务完成' &&
        stepData.title !== '任务已完成' && (
          <div
            style={{
              fontSize: '14.5px',
              fontWeight: 600,
              color: '#e6edf3',
              lineHeight: '1.6',
              marginTop: '1px',
              marginBottom: '2px',
            }}
          >
            {stepData.title}
          </div>
        )}

      {/* 方案确认正文内容 (Plan) - 保持醒目清晰的 Markdown 呈现，不折叠 */}
      {planContent && (
        <div
          className="plan-confirmation-content markdown-content"
          style={{
            fontSize: '14px',
            lineHeight: '1.7',
            color: '#c9d1d9',
            marginTop: '2px',
            marginBottom: '4px',
          }}
        >
          <MemoizedMarkdown content={planContent} />
        </div>
      )}

      {/* 2. 执行动作命令行 (Action) - 与查询结果详情一致的代码块展示 */}
      {stepData.command && (
        <CodeBlock
          language="text"
          title={actionStatusLabel}
          value={stepData.command}
        />
      )}

      {/* 3. 审批与用户交互状态文字 */}
      {(stepData.status === 'approval_required' || stepData.approvalId) && !stepData.approved && (
        <div style={{ padding: '2px 0' }}>
          {stepData.rejected ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#8b949e', fontSize: '13.5px' }}>
              <X size={14} />
              <span>{stepData.command ? '已取消执行该指令' : '已取消执行'}</span>
            </div>
          ) : (
            <div style={{ color: '#8b949e', fontSize: '13.5px' }}>
              {stepData.command ? '等待执行命令确认' : '等待用户输入'}
            </div>
          )}
        </div>
      )}

      {/* 3.5. 文件修改 Diff 视图 */}
      {stepData.fileDiff && <FileDiffViewer diffData={stepData.fileDiff} />}
    </div>
  );
};

export const ReActStepCard = React.memo(ReActStepCardComponent);
