import React from 'react';
import { X } from 'lucide-react';

export interface UploadedImage {
  id: string;
  name: string;
  url: string;
}

interface ImageAttachmentListProps {
  images: UploadedImage[];
  onRemove: (index: number) => void;
}

/** 输入框上方已选图片胶囊列表 */
export const ImageAttachmentList: React.FC<ImageAttachmentListProps> = ({
  images,
  onRemove,
}) => {
  if (images.length === 0) return null;

  return (
    <div
      style={{
        display: 'flex',
        gap: '8px',
        padding: '6px 14px 4px 14px',
        flexWrap: 'wrap',
      }}
    >
      {images.map((img, idx) => (
        <div
          key={img.id || idx}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '3px 6px',
            borderRadius: '6px',
            backgroundColor: '#2b2b2b',
            border: '1px solid rgba(255, 255, 255, 0.08)',
            maxWidth: '220px',
          }}
        >
          <img
            src={img.url}
            alt={img.name}
            style={{
              width: '24px',
              height: '24px',
              borderRadius: '4px',
              objectFit: 'cover',
              flexShrink: 0,
            }}
          />
          <span
            style={{
              fontSize: '12.5px',
              color: '#ffffff',
              fontWeight: 500,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={img.name}
          >
            {img.name}
          </span>
          <button
            type="button"
            onClick={() => onRemove(idx)}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '16px',
              height: '16px',
              borderRadius: '3px',
              backgroundColor: 'rgba(255, 255, 255, 0.12)',
              color: '#ffffff',
              border: 'none',
              cursor: 'pointer',
              padding: 0,
              flexShrink: 0,
            }}
            title="移除图片"
          >
            <X size={10} />
          </button>
        </div>
      ))}
    </div>
  );
};
