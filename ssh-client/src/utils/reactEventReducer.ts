import type { ChatMessageItem as MessageItemType, ReActStepData, ReActStepEvent } from '../types';
import { removeEmojis } from './textUtils';

/** 计算本轮用户消息之后的消息搜索起点 */
export const resolveSearchStartIndex = (
  messages: MessageItemType[],
  currentRunPromptId: string | null
): number => {
  const promptIndex = currentRunPromptId
    ? messages.findIndex((m) => m.id === currentRunPromptId)
    : -1;
  return promptIndex >= 0 ? promptIndex + 1 : 0;
};

/** 将搜索起点之后仍处于 running 的步骤卡片统一置为指定终态 */
export const settleRunningSteps = (
  messages: MessageItemType[],
  searchStartIndex: number,
  status: 'completed' | 'error'
): MessageItemType[] =>
  messages.map((msg, idx) => {
    if (idx >= searchStartIndex && msg.type === 'react_step' && msg.stepData.status === 'running') {
      return {
        ...msg,
        stepData: {
          ...msg.stepData,
          status,
        },
      };
    }
    return msg;
  });

/**
 * 应用单步类 ReAct 事件（thought / token_delta / action / observation /
 * approval_required / plan_confirmed / reflection 等），返回更新后的消息列表。
 */
export const applyReActStepEvent = (
  prev: MessageItemType[],
  event: ReActStepEvent,
  currentRunPromptId: string | null
): MessageItemType[] => {
  const searchStartIndex = resolveSearchStartIndex(prev, currentRunPromptId);

  let stepNum = event.step;
  if (stepNum === undefined || stepNum === null) {
    for (let i = prev.length - 1; i >= searchStartIndex; i--) {
      const candidate = prev[i];
      if (candidate.type === 'react_step') {
        stepNum = candidate.stepData.step;
        break;
      }
    }
    stepNum = stepNum ?? 1;
  }

  let targetIndex = -1;
  for (let i = searchStartIndex; i < prev.length; i++) {
    const candidate = prev[i];
    if (candidate.type === 'react_step' && candidate.stepData.step === stepNum) {
      targetIndex = i;
      break;
    }
  }

  let currentStepData: ReActStepData;
  if (targetIndex >= 0) {
    const target = prev[targetIndex];
    currentStepData = { ...(target.type === 'react_step' ? target.stepData : { step: stepNum, status: 'running' }) };
  } else {
    currentStepData = {
      step: stepNum,
      status: 'running',
    };
  }

  if (event.type === 'token_delta') {
    const delta = event.delta || '';
    currentStepData.thought = (currentStepData.thought || '') + removeEmojis(delta);
    if (currentStepData.status !== 'error' && currentStepData.status !== 'approval_required') {
      currentStepData.status = 'running';
    }
  } else if (event.type === 'thought') {
    const thoughtText = event.thought ?? event.content ?? '';
    if (thoughtText) {
      currentStepData.thought = removeEmojis(thoughtText);
    }
    if (event.title) {
      currentStepData.title = removeEmojis(event.title);
    }
    if (event.plan) {
      currentStepData.plan = removeEmojis(event.plan);
    }
  } else if (event.type === 'approval_required') {
    currentStepData.status = 'approval_required';
    currentStepData.command = event.command;
    currentStepData.approvalId = event.approval_id;
    currentStepData.riskLevel = event.risk_level;
    currentStepData.reason = event.reason ? removeEmojis(event.reason) : undefined;
    currentStepData.description = event.description
      ? removeEmojis(event.description)
      : undefined;
    if (event.plan) {
      currentStepData.plan = removeEmojis(event.plan);
    }
  } else if (event.type === 'plan_confirmed') {
    currentStepData.approved = true;
    currentStepData.status = 'completed';
  } else if (event.type === 'action') {
    currentStepData.command = event.command;
    if (event.description) {
      currentStepData.description = removeEmojis(event.description);
    }
    if (event.title) {
      currentStepData.title = removeEmojis(event.title);
    }
    if (
      event.file_path ||
      event.diff ||
      event.tool_name === 'edit_remote_file' ||
      event.tool_name === 'write_remote_file'
    ) {
      currentStepData.fileDiff = {
        path: event.file_path || '',
        toolName: event.tool_name,
        target: event.file_target,
        replacement: event.file_replacement,
        content: event.file_content,
        diff: event.diff,
      };
    }
    if (currentStepData.status !== 'approval_required') {
      currentStepData.status = 'running';
    }
  } else if (event.type === 'observation') {
    const obsText = event.output ?? event.stdout ?? event.content ?? '';
    currentStepData.observation = removeEmojis(obsText) || '(无回显输出)';
    if (currentStepData.status === 'running') {
      currentStepData.status = 'completed';
    }
  } else if (event.type === 'reflection') {
    const refText = event.analysis ?? event.content ?? event.reflection;
    if (typeof refText === 'string') {
      currentStepData.reflection = removeEmojis(refText);
    }
    currentStepData.isNormal = event.is_normal;
    if (event.is_normal === false) {
      currentStepData.status = 'error';
    } else if (currentStepData.status !== 'error') {
      currentStepData.status = 'completed';
    }
  }

  if (targetIndex >= 0) {
    const target = prev[targetIndex];
    if (target.type === 'react_step') {
      const updated = [...prev];
      updated[targetIndex] = {
        ...target,
        stepData: currentStepData,
      };
      return updated;
    }
  }

  const updated = settleRunningSteps(prev, searchStartIndex, 'completed');

  const newMessage: MessageItemType = {
    id: `step-${stepNum}-${Date.now()}`,
    role: 'assistant',
    type: 'react_step',
    step: stepNum,
    content: '',
    stepData: currentStepData,
  };

  return [...updated, newMessage];
};
