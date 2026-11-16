import React, { useState, useEffect } from 'react';
import { ScoreChip } from '../components/ScoreChip';
import { Thumb } from '../components/Thumb';
import { formatTimeET, formatDateET } from '../utils/timeFormat';
import { stripHashtags } from '../utils/textFormat';

interface DaySummary {
  date: string;
  found: number;
  approved: number;
  rejected?: number;
  is_running?: boolean;
  is_today?: boolean;
}

interface LineupItem {
  slot_time: string;
  time_display?: string;
  post_id: string;
  title: string;
  uploader: string;
  source_url?: string;
  hybrid_score: number;
  thumb_url?: string;
}

interface ReserveItem {
  post_id: string;
  title: string;
  uploader?: string;
  score?: number;
}

interface AgentDetailData {
  target_date: string;
  lineup_target_date?: string;
  estimated: boolean;
  totals: {
    found: number;
    rejected: number;
    accepted: number;
  };
  funnel: {
    dropped_before_judging: number;
    rejected_by_judge: number;
    stored_in_reserve: number;
    selected_for_tomorrow: number;
  };
  lineup: LineupItem[];
  reserve: ReserveItem[];
  reasons: Record<string, number>;
  sources: Record<string, { judged: number; accepted: number }>;
}

interface AgentViewProps {
  onSelectClipForYouTube?: (postId: string) => void;
}

export const AgentView: React.FC<AgentViewProps> = ({ onSelectClipForYouTube }) => {
  const [days, setDays] = useState<DaySummary[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>('');
  const [detail, setDetail] = useState<AgentDetailData | null>(null);
  const [loadingDays, setLoadingDays] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch 7-day picker
  useEffect(() => {
    let isMounted = true;
    async function loadDays() {
      try {
        const resp = await fetch('/api/agent/days?days=7');
        if (!resp.ok) throw new Error('Failed to fetch days');
        const data = await resp.json();
        if (isMounted) {
          setDays(data);
          if (data.length > 0) {
            // Start with today selected
            const todayEntry = data.find((d: DaySummary) => d.is_today);
            if (todayEntry) {
              setSelectedDate(todayEntry.date);
            } else {
              setSelectedDate(data[data.length - 1].date);
            }
          }
          setLoadingDays(false);
        }
      } catch (err: any) {
        if (isMounted) {
          setError(err.message);
          setLoadingDays(false);
        }
      }
    }
    loadDays();
    return () => { isMounted = false; };
  }, []);

  // Fetch detail for selected date
  useEffect(() => {
    if (!selectedDate) return;
    let isMounted = true;
    async function loadDetail() {
      setLoadingDetail(true);
      setError(null);
      try {
        const resp = await fetch(`/api/agent/day/${selectedDate}`);
        if (!resp.ok) throw new Error('Failed to fetch day details');
        const data = await resp.json();
        if (isMounted) {
          setDetail(data);
          setLoadingDetail(false);
        }
      } catch (err: any) {
        if (isMounted) {
          setError(err.message);
          setLoadingDetail(false);
        }
      }
    }
    loadDetail();
    return () => { isMounted = false; };
  }, [selectedDate]);

  // Format date header string: e.g. "Saturday 14 November"
  const formattedDateTitle = React.useMemo(() => {
    if (!selectedDate) return 'Saturday 14 November';
    try {
      const dt = new Date(`${selectedDate}T12:00:00-05:00`);
      return dt.toLocaleDateString('en-US', { timeZone: 'America/New_York', weekday: 'long', day: 'numeric', month: 'long' });
    } catch {
      return selectedDate;
    }
  }, [selectedDate]);

  // Format day picker card label: e.g. "Sun 8"
  const formatPickerLabel = (dateStr: string) => {
    try {
      const dt = new Date(`${dateStr}T12:00:00-05:00`);
      return dt.toLocaleDateString('en-US', { timeZone: 'America/New_York', weekday: 'short', day: 'numeric' });
    } catch {
      return dateStr;
    }
  };

  // Funnel calculations
  const funnel = detail?.funnel || {
    dropped_before_judging: 21,
    rejected_by_judge: 29,
    stored_in_reserve: 6,
    selected_for_tomorrow: 6,
  };
  const funnelTotal =
    (funnel.dropped_before_judging || 0) +
    (funnel.rejected_by_judge || 0) +
    (funnel.stored_in_reserve || 0) +
    (funnel.selected_for_tomorrow || 0) || 1;

  const pct = (val: number) => Math.max(8, Math.round((val / funnelTotal) * 100));

  // Rejection reasons list sorted by count descending
  const reasonLabels: Record<string, string> = {
    not_funny: 'Not funny enough',
    non_english: 'Speech not in English',
    watermark: "Another channel's watermark",
    duration: 'Too long or too short',
    tv_film: 'Looks like TV or film',
    other: 'Other criteria',
  };

  const reasonsList = Object.entries(detail?.reasons || {
    not_funny: 19,
    non_english: 11,
    watermark: 8,
    duration: 7,
    tv_film: 5,
  }).sort((a, b) => b[1] - a[1]);

  const maxReasonCount = Math.max(1, ...reasonsList.map(([, c]) => c));

  // Sources list sorted by yield
  const sourcesList = Object.entries(detail?.sources || {
    'r/Unexpected': { judged: 9, accepted: 4 },
    'r/Whatcouldgowrong': { judged: 8, accepted: 3 },
    'r/holdmybeer': { judged: 11, accepted: 2 },
    'r/AnimalsBeingDerps': { judged: 6, accepted: 1 },
  }).sort((a, b) => (b[1].accepted / (b[1].judged || 1)) - (a[1].accepted / (a[1].judged || 1)));

  return (
    <div className="flex-1 flex flex-col gap-[28px] p-[20px] lg:p-[40px] max-w-[1400px] w-full mx-auto pb-[100px] md:pb-[40px]">
      {/* Header */}
      <div className="flex flex-col gap-[6px]">
        <div className="flex items-center gap-[12px]">
          <h1 className="font-heading font-semibold text-[32px] lg:text-[36px] text-[#1D3B33] leading-tight">
            Agent
          </h1>
          {detail?.estimated && (
            <span className="bg-[#DADFDA] text-[#6F7A73] text-[11px] font-semibold px-[8px] py-[2px] rounded-[6px] tracking-wide uppercase">
              Estimated
            </span>
          )}
        </div>
        <p className="font-ui text-[15px] text-[#6F7A73]">
          What the agent found, judged and kept each day.
        </p>
      </div>

      {error && (
        <div className="p-[14px] rounded-[12px] bg-[#D4412F]/10 border border-[#D4412F]/30 text-[#D4412F] font-ui text-[14px]">
          {error}
        </div>
      )}

      {/* 7-Day Picker */}
      <div className="flex gap-[10px] items-stretch overflow-x-auto pb-[4px] no-scrollbar">
        {loadingDays ? (
          Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="flex-1 min-w-[130px] h-[78px] rounded-[12px] bg-[#FFFFFF] animate-pulse border border-[#D5DBD5]" />
          ))
        ) : (
          days.map((d) => {
            const isSelected = d.date === selectedDate;
            const acceptedRatio = d.found > 0 ? (d.approved / d.found) : 0.15;
            return (
              <button
                key={d.date}
                onClick={() => setSelectedDate(d.date)}
                className={`flex-1 min-w-[135px] flex flex-col gap-[8px] p-[12px] lg:px-[14px] lg:py-[12px] rounded-[12px] text-left transition-all cursor-pointer
                  ${isSelected
                    ? 'bg-white border-[1.5px] border-[#1D3B33] shadow-sm'
                    : 'bg-white/80 hover:bg-white border border-[#D5DBD5]'
                  }
                `}
              >
                <div className="flex items-center gap-[6px]">
                  <span className="font-ui font-semibold text-[14px] text-[#1D3B33]">
                    {formatPickerLabel(d.date)}
                  </span>
                  {d.is_today && (
                    <span className="text-[10px] font-bold px-[5px] py-[1px] rounded bg-[#DCEBE0] text-[#2E7D4F] uppercase tracking-wider">
                      Today
                    </span>
                  )}
                  {d.is_running && (
                    <span className="size-[7px] rounded-full bg-[#D4412F] animate-pulse" title="Active curation run" />
                  )}
                </div>
                {/* Mini proportional bar */}
                <div className="h-[6px] w-full rounded-[3px] bg-[#DADFDA] overflow-hidden flex">
                  <div
                    className="h-full bg-[#2E7D4F] transition-all"
                    style={{ width: `${Math.max(10, Math.min(100, acceptedRatio * 100))}%` }}
                  />
                </div>
                <span className="font-ui text-[12px] text-[#6F7A73] truncate">
                  {d.is_running ? `${d.found} found so far` : `${d.found} found · ${d.approved} kept`}
                </span>
              </button>
            );
          })
        )}
      </div>

      {/* Funnel Card */}
      <div className="bg-white rounded-[16px] p-[20px] lg:p-[28px] border border-[#D5DBD5]/40 flex flex-col gap-[22px] shadow-sm">
        <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-[16px] w-full">
          <div>
            <h2 className="font-heading font-semibold text-[22px] text-[#1D3B33]">
              {formattedDateTitle}
            </h2>
            <p className="font-ui text-[14px] text-[#6F7A73]">
              Every clip the agent looked at, and where it ended up.
            </p>
          </div>
          {/* Totals */}
          <div className="flex items-end gap-[24px] lg:gap-[36px]">
            <div className="flex items-baseline gap-[8px]">
              <span className="font-heading font-extrabold text-[30px] lg:text-[34px] text-[#1D3B33] leading-none">
                {detail?.totals.found ?? 62}
              </span>
              <span className="font-ui text-[14px] text-[#6F7A73]">found</span>
            </div>
            <div className="flex items-baseline gap-[8px]">
              <span className="font-heading font-extrabold text-[30px] lg:text-[34px] text-[#6F7A73] leading-none">
                {detail?.totals.rejected ?? 50}
              </span>
              <span className="font-ui text-[14px] text-[#6F7A73]">rejected</span>
            </div>
            <div className="flex items-baseline gap-[8px]">
              <span className="font-heading font-extrabold text-[30px] lg:text-[34px] text-[#2E7D4F] leading-none">
                {detail?.totals.accepted ?? 12}
              </span>
              <span className="font-ui text-[14px] text-[#6F7A73]">accepted</span>
            </div>
          </div>
        </div>

        {/* Proportional Funnel Ribbon */}
        <div className="flex flex-col md:flex-row gap-[6px] items-stretch w-full pt-[6px]">
          {/* Segment 1: dropped before judging */}
          <div
            style={{ flex: pct(funnel.dropped_before_judging) }}
            className="flex flex-col gap-[8px] min-w-[90px]"
          >
            <div className="h-[56px] rounded-[10px] bg-[#DADFDA] px-[16px] flex items-center shadow-inner">
              <span className="font-heading font-bold text-[24px] lg:text-[26px] text-[#1D3B33]">
                {funnel.dropped_before_judging}
              </span>
            </div>
            <span className="font-ui text-[13px] text-[#6F7A73] leading-tight">
              dropped before judging
            </span>
          </div>

          {/* Segment 2: rejected by the judge */}
          <div
            style={{ flex: pct(funnel.rejected_by_judge) }}
            className="flex flex-col gap-[8px] min-w-[90px]"
          >
            <div className="h-[56px] rounded-[10px] bg-[#6F7A73] px-[16px] flex items-center shadow-inner">
              <span className="font-heading font-bold text-[24px] lg:text-[26px] text-white">
                {funnel.rejected_by_judge}
              </span>
            </div>
            <span className="font-ui text-[13px] text-[#6F7A73] leading-tight">
              rejected by the judge
            </span>
          </div>

          {/* Segment 3: stored in reserve */}
          <div
            style={{ flex: pct(funnel.stored_in_reserve) }}
            className="flex flex-col gap-[8px] min-w-[80px]"
          >
            <div className="h-[56px] rounded-[10px] bg-[#DCEBE0] px-[16px] flex items-center shadow-inner">
              <span className="font-heading font-bold text-[24px] lg:text-[26px] text-[#2E7D4F]">
                {funnel.stored_in_reserve}
              </span>
            </div>
            <span className="font-ui text-[13px] text-[#6F7A73] leading-tight">
              stored in reserve
            </span>
          </div>

          {/* Segment 4: selected for tomorrow */}
          <div
            style={{ flex: pct(funnel.selected_for_tomorrow) }}
            className="flex flex-col gap-[8px] min-w-[80px]"
          >
            <div className="h-[56px] rounded-[10px] bg-[#2E7D4F] px-[16px] flex items-center shadow-inner">
              <span className="font-heading font-bold text-[24px] lg:text-[26px] text-white">
                {funnel.selected_for_tomorrow}
              </span>
            </div>
            <span className="font-ui text-[13px] text-[#6F7A73] leading-tight">
              selected for tomorrow
            </span>
          </div>
        </div>
      </div>

      {/* Two-Column Section: Lineup Card (Left) & Reasons / Sources (Right) */}
      <div className="flex flex-col xl:flex-row gap-[24px] items-start w-full">
        {/* Left: Lineup Card */}
        <div className="flex-1 bg-white rounded-[16px] p-[20px] lg:p-[28px] border border-[#D5DBD5]/40 flex flex-col w-full shadow-sm">
          <div className="flex flex-col gap-[4px] pb-[16px]">
            <div className="flex items-center justify-between">
              <h3 className="font-heading font-semibold text-[22px] text-[#1D3B33]">
                {(() => {
                  const isNextDay = detail?.lineup_target_date && detail.lineup_target_date > selectedDate;
                  const isToday = days.find(d => d.date === selectedDate)?.is_today;
                  if (isNextDay) {
                    return isToday ? "Tomorrow's lineup" : "Next day's lineup";
                  }
                  return `Lineup for ${formatDateET(detail?.lineup_target_date || selectedDate, { weekday: 'short', day: 'numeric' })}`;
                })()}
              </h3>
              {detail?.lineup_target_date && (
                <span className="font-ui text-[12px] font-semibold text-[#2E7D4F] bg-[#DCEBE0] px-[8px] py-[2px] rounded-[6px]">
                  For {formatDateET(detail.lineup_target_date, { month: 'short', day: 'numeric' })}
                </span>
              )}
            </div>
            <p className="font-ui text-[14px] text-[#6F7A73]">
              {detail?.lineup.length || 0} clips ranked by score. Click any row to preview.
            </p>
          </div>

          <div className="h-px bg-[#D5DBD5] w-full" />

          {/* Lineup Rows */}
          <div className="flex flex-col divide-y divide-[#D5DBD5]">
            {loadingDetail ? (
              Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="py-[12px] h-[64px] animate-pulse bg-gray-50 rounded-[8px] my-1" />
              ))
            ) : detail?.lineup && detail.lineup.length > 0 ? (
              detail.lineup.map((item, idx) => {
                const timeDisp = item.time_display || formatTimeET(item.slot_time || `${12 + idx}:00`);

                return (
                  <button
                    key={item.post_id || idx}
                    onClick={() => onSelectClipForYouTube?.(item.post_id)}
                    className="flex items-center gap-[16px] py-[10px] px-[8px] hover:bg-[#EDF0EC]/60 rounded-[8px] transition-colors text-left cursor-pointer group"
                  >
                    <span className="font-heading font-medium text-[15px] text-[#1D3B33] w-[86px] shrink-0">
                      {timeDisp}
                    </span>
                    <Thumb src={item.thumb_url} alt={stripHashtags(item.title)} />
                    <div className="flex-1 min-w-0 flex flex-col gap-[2px]">
                      <span className="font-ui font-semibold text-[15px] text-[#1D3B33] truncate group-hover:text-[#2E7D4F] transition-colors">
                        {stripHashtags(item.title)}
                      </span>
                      <span className="font-ui text-[13px] text-[#6F7A73] truncate">
                        {item.source_url ? `r/${item.source_url.split('/')[4] || 'funny'}` : 'r/Unexpected'} · u/{item.uploader}
                      </span>
                    </div>
                    <ScoreChip score={item.hybrid_score} />
                  </button>
                );
              })
            ) : (
              <div className="py-[32px] text-center text-[#6F7A73] font-ui text-[14px]">
                No curated lineup for this day yet.
              </div>
            )}
          </div>

          {/* Reserve Section */}
          <div className="h-px bg-[#D5DBD5] w-full mt-[12px]" />
          <div className="flex flex-col gap-[12px] pt-[18px]">
            <div className="flex items-center justify-between">
              <div>
                <h4 className="font-heading font-semibold text-[17px] text-[#1D3B33]">
                  Stored in reserve
                </h4>
                <p className="font-ui text-[13px] text-[#6F7A73]">
                  Approved but not scheduled. Used when a day comes up short.
                </p>
              </div>
              <div className="bg-white border border-[#D5DBD5] rounded-[8px] px-[8px] py-[3px]">
                <span className="font-heading font-semibold text-[13px] text-[#1D3B33]">
                  {detail?.reserve?.length || 0} clips
                </span>
              </div>
            </div>

            {/* Reserve Thumbnails Carousel/Row */}
            <div className="flex gap-[10px] items-center overflow-x-auto py-[4px] no-scrollbar">
              {detail?.reserve && detail.reserve.length > 0 ? (
                detail.reserve.map((res) => (
                  <Thumb
                    key={res.post_id}
                    src={`/api/thumbs/${res.post_id}.jpg`}
                    alt={stripHashtags(res.title)}
                    width={40}
                    height={70}
                  />
                ))
              ) : (
                <p className="text-[13px] text-[#6F7A73] font-ui py-[8px]">
                  No clips added to reserve on this day.
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Right: Rejection reasons & Best sources */}
        <div className="w-full xl:w-[372px] flex flex-col gap-[24px] shrink-0">
          {/* Rejection reasons card */}
          <div className="bg-white rounded-[16px] p-[20px] lg:p-[24px] border border-[#D5DBD5]/40 flex flex-col gap-[14px] shadow-sm">
            <h3 className="font-heading font-semibold text-[20px] text-[#1D3B33]">
              Why clips were rejected
            </h3>
            <div className="flex flex-col gap-[14px]">
              {reasonsList.map(([code, count]) => {
                const label = reasonLabels[code] || code;
                const ratio = Math.max(5, Math.round((count / maxReasonCount) * 100));
                return (
                  <div key={code} className="flex flex-col gap-[6px]">
                    <div className="flex items-center justify-between text-[#1D3B33] font-ui text-[14px]">
                      <span>{label}</span>
                      <span className="font-heading font-semibold text-[15px]">{count}</span>
                    </div>
                    <div className="h-[6px] w-full rounded-[3px] bg-[#DADFDA] overflow-hidden">
                      <div
                        className="h-full bg-[#6F7A73] rounded-[3px]"
                        style={{ width: `${ratio}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Best sources card */}
          <div className="bg-white rounded-[16px] p-[20px] lg:p-[24px] border border-[#D5DBD5]/40 flex flex-col gap-[14px] shadow-sm">
            <h3 className="font-heading font-semibold text-[20px] text-[#1D3B33]">
              Where good clips came from
            </h3>
            <div className="flex flex-col gap-[14px]">
              {sourcesList.length === 0 ? (
                <p className="text-[13.5px] text-[#6F7A73] font-ui py-[8px]">
                  No source data for this day.
                </p>
              ) : (
                sourcesList.map(([src, stat]) => {
                  const yieldPct = stat.judged > 0 ? (stat.accepted / stat.judged) : 0.2;
                  return (
                    <div key={src} className="flex flex-col gap-[6px]">
                      <div className="flex items-center justify-between font-ui text-[14px]">
                        <span className="text-[#1D3B33]">{src}</span>
                        <span className="text-[#6F7A73] text-[13px]">
                          {stat.accepted} of {stat.judged} accepted
                        </span>
                      </div>
                      <div className="h-[6px] w-full rounded-[3px] bg-[#DADFDA] overflow-hidden">
                        <div
                          className="h-full bg-[#2E7D4F] rounded-[3px]"
                          style={{ width: `${Math.max(8, Math.min(100, yieldPct * 100))}%` }}
                        />
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
