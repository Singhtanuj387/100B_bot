import React, { useState } from 'react';
import { Play } from 'lucide-react';

interface ThumbProps {
  src?: string | null;
  alt?: string;
  className?: string;
  width?: number | string;
  height?: number | string;
}

export const Thumb: React.FC<ThumbProps> = ({
  src,
  alt = 'Video thumbnail',
  className = '',
  width,
  height,
}) => {
  const [hasError, setHasError] = useState(false);

  const styleObj: React.CSSProperties = {};
  if (width) styleObj.width = typeof width === 'number' ? `${width}px` : width;
  if (height) styleObj.height = typeof height === 'number' ? `${height}px` : height;

  const defaultClasses = width || height ? '' : 'w-[32px] h-[56px]';

  if (src && !hasError) {
    return (
      <div
        style={styleObj}
        className={`relative overflow-hidden rounded-[8px] bg-[#17302B] shrink-0 border border-[#D5DBD5]/30 ${defaultClasses} ${className}`}
      >
        <img
          src={src}
          alt={alt}
          onError={() => setHasError(true)}
          className="w-full h-full object-cover block"
          loading="lazy"
        />
      </div>
    );
  }

  // Figma gradient thumbnail fallback
  return (
    <div
      style={styleObj}
      className={`relative overflow-hidden rounded-[8px] bg-gradient-to-b from-[#335C4F] to-[#17302B] flex items-center justify-center shrink-0 border border-[#D5DBD5]/20 ${defaultClasses} ${className}`}
    >
      <Play className="size-[12px] text-white/70 fill-white/50" />
    </div>
  );
};
