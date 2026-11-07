export type AuthType = 'PASSWORD' | 'PRIVATE_KEY' | 1 | 2;

export interface Host {
  host_id: string;
  connection_id: string;
  name: string;
  connection_name?: string;
  ip: string;
  host?: string;
  port: number;
  username: string;
  auth_type: AuthType;
  connection_timeout: number;
  status?: number;
  user_id?: string;
}

export interface RegisterHostPayload {
  name: string;
  ip: string;
  port: number;
  username: string;
  auth_type: 'PASSWORD' | 'PRIVATE_KEY';
  password?: string;
  private_key?: string;
  passphrase?: string;
  connection_timeout?: number;
  keepalive_interval?: number;
}

export interface AgentSessionSummary {
  session_id: string;
  id?: string;
  host_id: string;
  agent_id?: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  parent_session_id?: string;
  branch_step?: number;
  is_branch?: boolean;
}

export interface FileDiffData {
  path: string;
  toolName?: 'edit_remote_file' | 'write_remote_file' | string;
  target?: string;
  replacement?: string;
  content?: string;
  diff?: string;
}

export interface ReActStepData {
  step: number;
  title?: string;
  thought?: string;
  command?: string;
  description?: string;
  plan?: string;
  observation?: string;
  reflection?: string;
  isNormal?: boolean;
  status: 'running' | 'completed' | 'error' | 'approval_required';
  approvalId?: string;
  riskLevel?: string;
  reason?: string;
  approved?: boolean;
  rejected?: boolean;
  durationMs?: number;
  fileDiff?: FileDiffData;
}

export interface MessageBase {
  id?: number | string;
  content: string;
  timestamp?: string;
}

export interface UserMessage extends MessageBase {
  role: 'user';
  type?: undefined;
  images?: string[];
}

export interface AssistantTextMessage extends MessageBase {
  role: 'assistant';
  type?: 'text' | 'final_answer';
}

export interface ReActStepMessage extends MessageBase {
  role: 'assistant';
  type: 'react_step';
  step: number;
  stepData: ReActStepData;
}

export interface SystemMessage extends MessageBase {
  role: 'system';
  type?: 'text';
}

export interface ErrorMessage extends MessageBase {
  role: 'system';
  type: 'error';
}

/** 消息判别联合：不同消息类型只暴露各自合法字段 */
export type ChatMessageItem =
  | UserMessage
  | AssistantTextMessage
  | ReActStepMessage
  | SystemMessage
  | ErrorMessage;

export interface ReActStartEvent {
  type: 'start';
  session_id?: string;
  host_id?: string;
  host_name?: string;
  user_prompt?: string;
}

export interface ReActBranchCreatedEvent {
  type: 'branch_created';
  branch_session_id?: string;
  parent_session_id?: string;
  branch_step?: number;
  title?: string;
}

export interface ReActGreetingEvent {
  type: 'greeting';
  content?: string;
}

export interface ReActFinalEvent {
  type: 'final_answer' | 'finish';
  conclusion?: string;
  content?: string;
  step?: number;
}

export interface ReActErrorEvent {
  type: 'error';
  error?: string;
  content?: string;
  step?: number;
}

export interface ReActTokenDeltaEvent {
  type: 'token_delta';
  step?: number;
  delta?: string;
}

export interface ReActThoughtEvent {
  type: 'thought';
  step?: number;
  thought?: string;
  content?: string;
  title?: string;
  plan?: string;
}

export interface ReActApprovalRequiredEvent {
  type: 'approval_required';
  step?: number;
  command?: string;
  approval_id?: string;
  risk_level?: string;
  reason?: string;
  description?: string;
  plan?: string;
}

export interface ReActPlanConfirmedEvent {
  type: 'plan_confirmed';
  step?: number;
}

export interface ReActActionEvent {
  type: 'action';
  step?: number;
  command?: string;
  description?: string;
  title?: string;
  file_path?: string;
  diff?: string;
  tool_name?: string;
  file_target?: string;
  file_replacement?: string;
  file_content?: string;
}

export interface ReActObservationEvent {
  type: 'observation';
  step?: number;
  output?: string;
  stdout?: string;
  content?: string;
}

export interface ReActReflectionEvent {
  type: 'reflection';
  step?: number;
  analysis?: string;
  content?: string;
  reflection?: string;
  is_normal?: boolean;
}

export interface ReActMiscEvent {
  type: 'plan' | 'audit';
  step?: number;
  title?: string;
  content?: string;
  plan?: string;
  plan_steps?: Array<{ step: number; action: string; purpose: string }>;
  status?: string;
}

/** ReAct 事件判别联合：按 type 精确约束字段集合 */
export type ReActEvent =
  | ReActStartEvent
  | ReActBranchCreatedEvent
  | ReActGreetingEvent
  | ReActFinalEvent
  | ReActErrorEvent
  | ReActTokenDeltaEvent
  | ReActThoughtEvent
  | ReActApprovalRequiredEvent
  | ReActPlanConfirmedEvent
  | ReActActionEvent
  | ReActObservationEvent
  | ReActReflectionEvent
  | ReActMiscEvent;

/** 单步推演事件：归约器仅接收该子集 */
export type ReActStepEvent =
  | ReActTokenDeltaEvent
  | ReActThoughtEvent
  | ReActApprovalRequiredEvent
  | ReActPlanConfirmedEvent
  | ReActActionEvent
  | ReActObservationEvent
  | ReActReflectionEvent
  | ReActMiscEvent;

export interface ChatMilestone {
  id?: number;
  session_id: string;
  type: string;
  content: string;
  created_at: string;
}

export type PermissionMode = 'request_approval' | 'full_access';

/** 对话模式：chat 走普通对话接口，agent 走 ReAct 闭环接口 */
export type AgentMode = 'chat' | 'agent';
