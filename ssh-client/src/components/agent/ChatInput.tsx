import React, { useState, useRef, useEffect } from 'react';
import {
  Send,
  Square,
  AtSign,
  Image as ImageIcon,
  Sparkles,
  Hand,
  AlertTriangle,
  ChevronDown,
  X,
} from 'lucide-react';
import type { PermissionMode } from '../../types';
import {
  MAX_IMAGE_DATA_URL_CHARS,
  MAX_IMAGE_FILE_BYTES,
  prepareImageDataUrl,
} from '../../utils/image';
import { ImageAttachmentList, type UploadedImage } from './ImageAttachmentList';
import { PermissionModeMenu } from './PermissionModeMenu';
import { QuickPromptMenu } from './QuickPromptMenu';

interface ChatInputProps {
  loading: boolean;
  targetScopeName?: string;
  permissionMode: PermissionMode;
  onPermissionModeChange: (mode: PermissionMode) => void;
  onSend: (prompt: string, images?: string[]) => void;
  onStop: () => void;
  onAttachTerminalBuffer?: () => void;
}

const MAX_IMAGES = 4;

const QUICK_PROMPTS = [
  '检查当前目录下的文件并按时间排序',
  '查看系统负载与内存使用情况',
  '检查磁盘空间占用与根分区容量',
  '排查占用 CPU 最高的 5 个进程',
  '检查 Docker 容器运行状态',
];

export const ChatInput: React.FC<ChatInputProps> = ({
  loading,
  targetScopeName = '默认',
  permissionMode,
  onPermissionModeChange,
  onSend,
  onStop,
  onAttachTerminalBuffer,
}) => {
  const [prompt, setPrompt] = useState('');
  const [images, setImages] = useState<UploadedImage[]>([]);
  const [imageError, setImageError] = useState('');
  const [showQuickPrompts, setShowQuickPrompts] = useState(false);
  const [showPermissionMenu, setShowPermissionMenu] = useState(false);
  const [modelRiskLevel, setModelRiskLevel] = useState<'High' | 'Auto'>('High');

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const permissionMenuRef = useRef<HTMLDivElement>(null);

  // 自动调整输入框高度
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 140)}px`;
    }
  }, [prompt]);

  // 添加图片：校验体积、压缩尺寸与 data URL 长度，并限制最多 4 张
  const addImageFile = async (file: File, fileName?: string): Promise<boolean> => {
    if (!file.type.startsWith('image/')) return false;
    if (file.size > MAX_IMAGE_FILE_BYTES) {
      setImageError(
        `图片「${fileName || file.name}」超过 ${MAX_IMAGE_FILE_BYTES / 1024 / 1024}MB 上限，请压缩后重试`
      );
      return false;
    }
    try {
      const url = await prepareImageDataUrl(file);
      if (url.length > MAX_IMAGE_DATA_URL_CHARS) {
        setImageError(`图片「${fileName || file.name}」压缩后仍超过体积上限，请换一张更小的图片`);
        return false;
      }
      setImageError('');
      setImages((prev) => [
        ...prev,
        { id: `${Date.now()}-${Math.random()}`, name: fileName || file.name || 'image.png', url },
      ]);
      return true;
    } catch {
      setImageError(`图片「${fileName || file.name}」处理失败，请重试`);
      return false;
    }
  };

  // 处理选择图片上传
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    const selected = Array.from(files);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }

    let available = MAX_IMAGES - images.length;
    for (const file of selected) {
      if (!file.type.startsWith('image/')) continue;
      if (available <= 0) {
        setImageError(`最多只能上传 ${MAX_IMAGES} 张图片`);
        break;
      }
      const added = await addImageFile(file);
      if (added) available -= 1;
    }
  };

  // 处理输入框内剪贴板直接粘贴图片
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith('image/')) {
        const file = items[i].getAsFile();
        if (file) {
          e.preventDefault();
          if (images.length >= MAX_IMAGES) {
            setImageError(`最多只能上传 ${MAX_IMAGES} 张图片`);
            return;
          }
          const fileName =
            file.name && file.name !== 'image.png'
              ? file.name
              : `screenshot_${new Date().toLocaleTimeString().replace(/:/g, '')}.png`;
          addImageFile(file, fileName);
        }
      }
    }
  };

  // 点击外部关闭权限菜单
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        permissionMenuRef.current &&
        !permissionMenuRef.current.contains(e.target as Node)
      ) {
        setShowPermissionMenu(false);
      }
    };
    if (showPermissionMenu) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [showPermissionMenu]);

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if ((!prompt.trim() && images.length === 0) || loading) return;
    const finalPrompt = prompt.trim() || '请分析上传的截图并排查异常';
    onSend(finalPrompt, images.length > 0 ? images.map((img) => img.url) : undefined);
    setPrompt('');
    setImages([]);
    setImageError('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleAttachContext = () => {
    if (onAttachTerminalBuffer) {
      onAttachTerminalBuffer();
    }
    setPrompt((prev) => (prev ? `${prev} @terminal` : '@terminal 结合终端回显排查 '));
    textareaRef.current?.focus();
  };

  return (
    <div className="floating-input-wrapper">
      {/* 快捷推荐菜单 */}
      <QuickPromptMenu
        isOpen={showQuickPrompts}
        prompts={QUICK_PROMPTS}
        onClose={() => setShowQuickPrompts(false)}
        onSelect={(qp) => {
          setPrompt(qp);
          setShowQuickPrompts(false);
          textareaRef.current?.focus();
        }}
      />

      {/* 浮动拟态输入卡片 */}
      <div className="floating-input-card">
        {/* 顶部执行范围 */}
        <div className="input-scope-row">
          <span>执行范围:</span>
          <span className="input-scope-val">{targetScopeName}</span>
        </div>

        {/* 图片上传错误提示 */}
        {imageError && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 14px 0 14px',
              fontSize: '12px',
              color: '#f85149',
            }}
          >
            <AlertTriangle size={12} />
            <span>{imageError}</span>
          </div>
        )}

        {/* 图片文件胶囊标签展示 */}
        <ImageAttachmentList
          images={images}
          onRemove={(idx) => setImages((prev) => prev.filter((_, i) => i !== idx))}
        />

        {/* 隐藏的文件选择 input */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          onChange={handleFileChange}
          style={{ display: 'none' }}
        />

        {/* 核心输入框 */}
        <textarea
          ref={textareaRef}
          rows={1}
          className="floating-textarea"
          placeholder="描述你的运维需求，可粘贴/上传截图排查异常..."
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
        />

        {/* 底部工具栏与操作按钮 */}
        <div className="input-toolbar-row">
          <div className="input-toolbar-left" style={{ position: 'relative' }}>
            {/* @ 添加上下文 */}
            <button
              type="button"
              className="input-tool-icon-btn"
              title="添加终端最新屏幕回显上下文 (@terminal)"
              onClick={handleAttachContext}
            >
              <AtSign size={16} />
            </button>

            {/* 附加图片 / 截图 */}
            <button
              type="button"
              className="input-tool-icon-btn"
              title="选择上传图片或截图 (支持直接粘贴截图)"
              onClick={() => fileInputRef.current?.click()}
            >
              <ImageIcon size={16} />
            </button>

            {/* 思考级别 High / Auto 切换（带虎鲸/AI徽标） */}
            <div
              className="input-pill-select"
              title="思考与推演深度模式"
              onClick={() => setModelRiskLevel(modelRiskLevel === 'High' ? 'Auto' : 'High')}
            >
              {/* 虎鲸 / AI 图标 */}
              <svg width="14" height="14" viewBox="0 0 24 24" fill="#388bfd">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9v-2h2v2zm0-4H9V7h2v5z" />
              </svg>
              <span>{modelRiskLevel}</span>
              <ChevronDown size={11} />
            </div>

            {/* 工具权限模式按钮 */}
            <div
              className="input-pill-select"
              title="工具权限模式"
              onClick={() => setShowPermissionMenu(!showPermissionMenu)}
            >
              {permissionMode === 'request_approval' ? (
                <Hand size={13} color="#f0f0f0" />
              ) : (
                <AlertTriangle size={13} color="#ff9900" />
              )}
              <ChevronDown size={11} />
            </div>

            {/* 工具权限模式弹出菜单 (1:1 还原用户参考图) */}
            <PermissionModeMenu
              isOpen={showPermissionMenu}
              permissionMode={permissionMode}
              menuRef={permissionMenuRef}
              onChange={(mode) => {
                onPermissionModeChange(mode);
                setShowPermissionMenu(false);
              }}
            />

            {/* 灵感快捷提示词 Sparkle */}
            <button
              type="button"
              className="input-tool-icon-btn"
              title="快捷运维提示词"
              onClick={() => setShowQuickPrompts(!showQuickPrompts)}
            >
              <Sparkles size={16} color={showQuickPrompts ? '#58a6ff' : undefined} />
            </button>
          </div>

          {/* 右侧发送 / 停止按钮 */}
          <div>
            {loading ? (
              <button
                type="button"
                className="input-send-btn active"
                style={{ backgroundColor: '#da3633' }}
                onClick={onStop}
                title="中止当前推演"
              >
                <Square size={13} fill="#ffffff" />
              </button>
            ) : (
              <button
                type="button"
                className={`input-send-btn ${prompt.trim() || images.length > 0 ? 'active' : ''}`}
                onClick={() => handleSubmit()}
                disabled={!prompt.trim() && images.length === 0}
                title="发送需求 (Enter)"
              >
                <Send size={14} style={{ marginLeft: '1px' }} />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 底部免责声明 */}
      <div className="disclaimer-footer">内容由 AI 生成，仅供参考</div>
    </div>
  );
};
