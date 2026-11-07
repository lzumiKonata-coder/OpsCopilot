/**
 * 文本处理工具函数
 */

// 过滤常见 Unicode Emoji 表情符号的正则
const EMOJI_REGEX =
  /[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}\u{1F900}-\u{1F9FF}\u{1FA70}-\u{1FAFF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{FE00}-\u{FE0F}]/gu;

/**
 * 移除文本中包含的任何 Emoji 表情符号，返回纯净文本
 */
export const removeEmojis = (text: string): string => {
  if (!text) return text;
  return text.replace(EMOJI_REGEX, '').trim();
};

// 已闭合的思考块，以及流式输出中尚未闭合的思考块
const THINK_BLOCK_REGEX = /<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi;
const OPEN_THINK_REGEX = /<think(?:ing)?>[\s\S]*$/i;

/**
 * 移除模型输出中 <think>...</think> 推理片段，避免在对话气泡中暴露思考过程
 * 流式场景下未闭合的思考块同样隐藏，直到正文开始输出
 */
export const stripThinkBlocks = (text: string): string => {
  if (!text) return text;
  return text.replace(THINK_BLOCK_REGEX, '').replace(OPEN_THINK_REGEX, '').trim();
};
