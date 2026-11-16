import React from 'react';
import { Bot, Calendar, PlaySquare } from 'lucide-react';

export type NavTab = 'agent' | 'schedule' | 'youtube';

interface PipelineStatusData {
  last_run?: {
    status?: string;
    finished_at?: string;
    uploaded?: number;
    scheduled?: number;
    failed?: number;
  } | null;
  next_run_time?: string;
}

interface RailProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  pipelineStatus?: PipelineStatusData | null;
}

export const Rail: React.FC<RailProps> = ({
  activeTab,
  onTabChange,
  pipelineStatus,
}) => {
  const lastRun = pipelineStatus?.last_run;
  const isLastRunPassed = lastRun?.status === 'success';
  const isLastRunPartial = lastRun?.status === 'partial';

  // Format last run time
  let lastRunTimeStr = 'Sat 6:02 PM · 6 of 6 uploaded';
  if (lastRun?.finished_at) {
    try {
      const dt = new Date(lastRun.finished_at);
      const dayName = dt.toLocaleDateString('en-US', { weekday: 'short' });
      const timeStr = dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
      const up = lastRun.uploaded || 0;
      const sched = lastRun.scheduled || 0;
      const total = up + sched || 6;
      lastRunTimeStr = `${dayName} ${timeStr} · ${total} of ${total} uploaded`;
    } catch {
      // fallback
    }
  }

  // Format next run time
  let nextRunTimeStr = 'Next run Sun 6:00 PM';
  if (pipelineStatus?.next_run_time) {
    try {
      const dt = new Date(pipelineStatus.next_run_time);
      const dayName = dt.toLocaleDateString('en-US', { weekday: 'short' });
      const timeStr = dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
      nextRunTimeStr = `Next run ${dayName} ${timeStr}`;
    } catch {
      // fallback
    }
  }

  const navItems = [
    { id: 'agent' as NavTab, label: 'Agent', icon: Bot },
    { id: 'schedule' as NavTab, label: 'Schedule', icon: Calendar },
    { id: 'youtube' as NavTab, label: 'YouTube', icon: PlaySquare },
  ];

  return (
    <>
      {/* Desktop & Tablet Sidebar Rail */}
      <aside
        className="hidden md:flex flex-col shrink-0 bg-[#1D3B33] self-stretch min-h-screen text-white transition-all duration-200 z-30
                   w-[72px] xl:w-[248px] px-[12px] xl:px-[20px] pt-[28px] pb-[24px]"
      >
        {/* Brand header */}
        <div className="flex items-center gap-[12px] overflow-hidden w-full">
          <div className="size-[44px] rounded-[22px] bg-[#D4412F] flex items-center justify-center shrink-0 shadow-sm">
            <span className="font-heading font-extrabold text-[22px] text-white leading-none">
              B
            </span>
          </div>
          <div className="hidden xl:flex flex-col gap-px leading-tight overflow-hidden whitespace-nowrap">
            <span className="font-heading font-semibold text-[18px] text-white">
              Betterverse
            </span>
            <span className="font-ui text-[13px] text-[#B0C7BD]">
              @betterverse-f2
            </span>
          </div>
        </div>

        {/* Spacing gap */}
        <div className="h-[36px] shrink-0" />

        {/* Navigation links */}
        <nav className="flex flex-col gap-[4px] w-full" aria-label="Main Navigation">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onTabChange(item.id)}
                className={`w-full flex items-center gap-[12px] px-[12px] py-[11px] rounded-[10px] cursor-pointer transition-colors text-left
                  ${isActive ? 'bg-[#2A4F43] text-white shadow-sm' : 'text-[#B0C7BD] hover:bg-[#2A4F43]/40 hover:text-white'}
                `}
                aria-current={isActive ? 'page' : undefined}
              >
                <Icon className={`size-[20px] shrink-0 ${isActive ? 'text-white' : 'text-[#B0C7BD]'}`} />
                <span className="hidden xl:inline flex-1 min-w-0 font-ui font-medium text-[15px] truncate">
                  {item.label}
                </span>
                {isActive && (
                  <span className="hidden xl:block size-[7px] rounded-full bg-[#2E7D4F] shrink-0" />
                )}
              </button>
            );
          })}
        </nav>

        {/* Spacer to push status card to bottom */}
        <div className="flex-1 min-h-[40px]" />

        {/* Pipeline status card */}
        <div className="bg-[#2A4F43] rounded-[12px] p-[10px] xl:p-[14px] flex flex-col gap-[6px] shrink-0 w-full overflow-hidden border border-[#D5DBD5]/10">
          <div className="flex items-center gap-[8px]">
            <span
              className={`size-[8px] rounded-full shrink-0 ${
                isLastRunPassed ? 'bg-[#2E7D4F]' : isLastRunPartial ? 'bg-[#D99A1E]' : 'bg-[#D4412F]'
              }`}
            />
            <span className="hidden xl:inline font-ui font-semibold text-[13px] text-white whitespace-nowrap">
              {isLastRunPassed ? 'Last run passed' : isLastRunPartial ? 'Last run partial' : 'Last run failed'}
            </span>
          </div>
          <p className="hidden xl:block font-ui text-[12px] text-[#B0C7BD] truncate">
            {lastRunTimeStr}
          </p>
          <p className="hidden xl:block font-ui text-[12px] text-[#B0C7BD] truncate">
            {nextRunTimeStr}
          </p>
        </div>
      </aside>

      {/* Mobile Bottom Tab Bar (<768px) */}
      <nav
        className="md:hidden fixed bottom-0 left-0 right-0 h-[64px] bg-[#1D3B33] border-t border-[#2A4F43] flex items-center justify-around px-[16px] z-50 shadow-lg"
        aria-label="Mobile Navigation"
      >
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onTabChange(item.id)}
              className={`flex flex-col items-center justify-center gap-[4px] px-[16px] py-[8px] rounded-[8px] transition-colors
                ${isActive ? 'text-white' : 'text-[#B0C7BD]'}`}
            >
              <Icon className="size-[20px]" />
              <span className="font-ui text-[11px] font-medium leading-none">
                {item.label}
              </span>
            </button>
          );
        })}
      </nav>
    </>
  );
};
