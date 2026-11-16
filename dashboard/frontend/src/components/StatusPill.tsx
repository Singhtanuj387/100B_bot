import React from 'react';

export type StatusType = 'Selected' | 'Stored' | 'Scheduled' | 'On air' | 'Published' | 'Draft' | 'Failed';

interface StatusPillProps {
  status: StatusType | string;
  className?: string;
}

export const StatusPill: React.FC<StatusPillProps> = ({ status, className = '' }) => {
  const norm = status.toLowerCase();

  if (norm === 'on air' || norm === 'on_air') {
    return (
      <div className={`bg-[#D4412F] inline-flex items-center gap-[6px] px-[10px] py-[4px] rounded-[999px] shrink-0 ${className}`}>
        <span className="size-[7px] rounded-full bg-white animate-pulse" />
        <span className="text-white text-[12px] font-semibold font-ui leading-none whitespace-nowrap">
          On air
        </span>
      </div>
    );
  }

  if (norm === 'selected') {
    return (
      <div className={`bg-[#2E7D4F] inline-flex items-center px-[10px] py-[4px] rounded-[999px] shrink-0 ${className}`}>
        <span className="text-white text-[12px] font-semibold font-ui leading-none whitespace-nowrap">
          Selected
        </span>
      </div>
    );
  }

  if (norm === 'stored' || norm === 'reserve') {
    return (
      <div className={`bg-[#DCEBE0] inline-flex items-center px-[10px] py-[4px] rounded-[999px] shrink-0 ${className}`}>
        <span className="text-[#2E7D4F] text-[12px] font-semibold font-ui leading-none whitespace-nowrap">
          Stored
        </span>
      </div>
    );
  }

  if (norm === 'scheduled') {
    return (
      <div className={`bg-[#F7E9C8] inline-flex items-center px-[10px] py-[4px] rounded-[999px] shrink-0 ${className}`}>
        <span className="text-[#7A5600] text-[12px] font-semibold font-ui leading-none whitespace-nowrap">
          Scheduled
        </span>
      </div>
    );
  }

  if (norm === 'published' || norm === 'uploaded') {
    return (
      <div className={`bg-[#DADFDA] inline-flex items-center px-[10px] py-[4px] rounded-[999px] shrink-0 ${className}`}>
        <span className="text-[#1D3B33] text-[12px] font-semibold font-ui leading-none whitespace-nowrap">
          Published
        </span>
      </div>
    );
  }

  if (norm === 'failed' || norm === 'error') {
    return (
      <div className={`bg-[#D4412F] inline-flex items-center px-[10px] py-[4px] rounded-[999px] shrink-0 ${className}`}>
        <span className="text-white text-[12px] font-semibold font-ui leading-none whitespace-nowrap">
          Failed
        </span>
      </div>
    );
  }

  // Draft / Queued fallback
  return (
    <div className={`bg-[#DADFDA] inline-flex items-center px-[10px] py-[4px] rounded-[999px] shrink-0 ${className}`}>
      <span className="text-[#6F7A73] text-[12px] font-semibold font-ui leading-none whitespace-nowrap capitalize">
        {status}
      </span>
    </div>
  );
};
