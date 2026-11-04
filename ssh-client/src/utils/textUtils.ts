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
