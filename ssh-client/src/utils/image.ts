/** 图片上传与压缩工具：约束体积与最长边，避免超大 base64 撑爆内存与 Token 预算 */

export const MAX_IMAGE_FILE_BYTES = 15 * 1024 * 1024;
// 与后端 ReActRequest 校验保持一致：单张 data URL 不超过 2.8M 字符（约 2MB 原始图片）
export const MAX_IMAGE_DATA_URL_CHARS = 2_800_000;

const MAX_IMAGE_SIDE = 1600;

const readFileAsDataURL = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error('读取图片失败'));
    reader.readAsDataURL(file);
  });

const loadImageElement = (src: string): Promise<HTMLImageElement> =>
  new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('图片解码失败'));
    img.src = src;
  });

/**
 * 读取图片并压缩到最长边 1600px、单张 data URL 不超过体积上限。
 */
export const prepareImageDataUrl = async (file: File): Promise<string> => {
  const original = await readFileAsDataURL(file);
  // GIF 动图 / SVG 保持原样，避免被静态化或缩放失真
  if (file.type === 'image/gif' || file.type === 'image/svg+xml') {
    return original;
  }

  const img = await loadImageElement(original);
  const scale = Math.min(1, MAX_IMAGE_SIDE / Math.max(img.width, img.height));
  if (scale === 1 && original.length <= MAX_IMAGE_DATA_URL_CHARS) {
    return original;
  }

  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(img.width * scale));
  canvas.height = Math.max(1, Math.round(img.height * scale));
  const ctx = canvas.getContext('2d');
  if (!ctx) return original;
  // JPEG 不支持透明通道，先铺白底，避免透明区域变黑
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

  let quality = 0.82;
  let result = canvas.toDataURL('image/jpeg', quality);
  while (result.length > MAX_IMAGE_DATA_URL_CHARS && quality > 0.4) {
    quality -= 0.12;
    result = canvas.toDataURL('image/jpeg', quality);
  }
  return result;
};
