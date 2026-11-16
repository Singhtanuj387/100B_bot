import React from 'react';

interface ScoreChipProps {
  score: number | string | null | undefined;
  className?: string;
}

export const ScoreChip: React.FC<ScoreChipProps> = ({ score, className = '' }) => {
  let formatted = '—';
  if (typeof score === 'number' && !isNaN(score)) {
    formatted = score.toFixed(1);
  } else if (typeof score === 'string' && score.trim() !== '' && score !== 'null' && score !== 'undefined') {
    formatted = score;
  }

  return (
    <div className={`bg-white border border-[#D5DBD5] inline-flex items-center px-[8px] py-[3px] rounded-[8px] shrink-0 ${className}`}>
      <span className="text-[#1D3B33] text-[13px] font-semibold font-heading leading-none whitespace-nowrap">
        {formatted}
      </span>
    </div>
  );
};
