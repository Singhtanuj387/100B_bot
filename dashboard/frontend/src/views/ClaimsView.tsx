import React, { useState, useEffect } from 'react';
import {
  ShieldAlert,
  ShieldCheck,
  AlertTriangle,
  ExternalLink,
  CheckCircle2,
  Lock,
  RefreshCw,
  Plus,
  X,
  Info,
} from 'lucide-react';
import { Thumb } from '../components/Thumb';
import { formatTimeET, formatDateET } from '../utils/timeFormat';
import { stripHashtags } from '../utils/textFormat';

interface RightsStatusData {
  is_local_only: boolean;
  unreviewed_policy: string;
  active_layers: Record<string, boolean>;
  providers: {
    audd: { configured: boolean; calls_today: number; daily_cap: number };
    google_vision: { configured: boolean; calls_today: number; daily_cap: number; monthly_free_tier: number };
  };
  stats: {
    total_claims: number;
    total_published: number;
    claims_per_100_published: number;
    claims_by_subreddit: Record<string, number>;
  };
}

interface ReviewClip {
  post_id: string;
  video_id?: string;
  target_date?: string;
  title: string;
  uploader?: string;
  slot_time?: string;
  rights_status?: string;
  rights_risk: number;
  decision: string;
  reasons: string[];
  layers: Record<string, any>;
  thumb_url: string;
  privacy?: string;
}

interface PublishedClip {
  post_id: string;
  video_id?: string;
  target_date: string;
  slot_time?: string;
  title: string;
  privacy: string;
  live_privacy?: string;
  live_status?: string;
  is_missing?: boolean;
  claim_status: string;
  claim_action?: string;
  claimant?: string;
  claim_type?: string;
  rights_status?: string;
  rights_risk: number;
  thumb_url: string;
  views?: number;
  likes?: number;
}

interface ClaimRecord {
  id: number;
  video_id?: string;
  post_id: string;
  claim_type: string;
  claimant: string;
  asset_title?: string;
  found_in?: string;
  action: string;
  noted_at: string;
  source_name?: string;
  uploader?: string;
  notes?: string;
}

interface BlocklistData {
  active: Array<{ id: number; type: string; value: string; status: string }>;
  pending: Array<{ id: number; type: string; value: string; status: string }>;
  disabled: Array<{ id: number; type: string; value: string; status: string }>;
}

export const ClaimsView: React.FC = () => {
  const [status, setStatus] = useState<RightsStatusData | null>(null);
  const [reviewClips, setReviewClips] = useState<ReviewClip[]>([]);
  const [publishedClips, setPublishedClips] = useState<PublishedClip[]>([]);
  const [claims, setClaims] = useState<ClaimRecord[]>([]);
  const [blocklist, setBlocklist] = useState<BlocklistData>({ active: [], pending: [], disabled: [] });

  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  // Modals state
  const [claimModalClip, setClaimModalClip] = useState<ReviewClip | PublishedClip | null>(null);
  const [claimForm, setClaimForm] = useState({
    claim_type: 'audio',
    claimant: '',
    asset_title: '',
    found_in: '',
    action: 'held',
    notes: '',
  });

  const [replaceModalClip, setReplaceModalClip] = useState<ReviewClip | null>(null);
  const [replaceAction, setReplaceAction] = useState<'held' | 'delete'>('held');
  const [deleteConfirmationText, setDeleteConfirmationText] = useState('');

  const [deleteModalClip, setDeleteModalClip] = useState<PublishedClip | null>(null);
  const [deleteModalConfirm, setDeleteModalConfirm] = useState('');

  const [newBlockType, setNewBlockType] = useState('text');
  const [newBlockValue, setNewBlockValue] = useState('');

  const loadAllData = async () => {
    try {
      setLoading(true);
      const [resStatus, resReview, resPub, resClaims, resBlock] = await Promise.all([
        fetch('/api/rights/status').then((r) => r.json()),
        fetch('/api/rights/review').then((r) => r.json()),
        fetch('/api/rights/published').then((r) => r.json()),
        fetch('/api/rights/claims').then((r) => r.json()),
        fetch('/api/rights/blocklist').then((r) => r.json()),
      ]);

      setStatus(resStatus);
      setReviewClips(resReview.clips || []);
      setPublishedClips(resPub || []);
      setClaims(resClaims || []);
      setBlocklist(resBlock || { active: [], pending: [], disabled: [] });
    } catch (err: any) {
      setMessage({ text: 'Failed to load claims data: ' + err.message, type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAllData();
  }, []);

  const handleMarkClear = async (postId: string) => {
    try {
      setActionLoading(true);
      const res = await fetch('/api/rights/mark-clear', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Dashboard-Request': '1',
        },
        body: JSON.stringify({ post_id: postId }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Mark clear failed');

      setMessage({ text: `Clip ${postId} cleared and scheduled for ${data.publish_at || 'next slot'}`, type: 'success' });
      await loadAllData();
    } catch (err: any) {
      setMessage({ text: err.message, type: 'error' });
    } finally {
      setActionLoading(false);
    }
  };

  const handleHold = async (postId: string, videoId?: string) => {
    try {
      setActionLoading(true);
      const res = await fetch('/api/rights/hold', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Dashboard-Request': '1',
        },
        body: JSON.stringify({ post_id: postId, video_id: videoId }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Hold failed');

      setMessage({ text: `Clip ${postId} set to private with no publishAt (held)`, type: 'success' });
      await loadAllData();
    } catch (err: any) {
      setMessage({ text: err.message, type: 'error' });
    } finally {
      setActionLoading(false);
    }
  };

  const handleSaveClaim = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!claimModalClip) return;
    try {
      setActionLoading(true);
      const res = await fetch('/api/rights/mark-claimed', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Dashboard-Request': '1',
        },
        body: JSON.stringify({
          post_id: claimModalClip.post_id,
          video_id: claimModalClip.video_id,
          ...claimForm,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to save claim');

      setMessage({
        text: `Claim registered! Generated ${data.suggestions_created} blocklist suggestions for review.`,
        type: 'success',
      });
      setClaimModalClip(null);
      await loadAllData();
    } catch (err: any) {
      setMessage({ text: err.message, type: 'error' });
    } finally {
      setActionLoading(false);
    }
  };

  const handleReplaceFromReserve = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!replaceModalClip) return;
    if (replaceAction === 'delete' && deleteConfirmationText !== 'DELETE') {
      alert("Must type 'DELETE' to confirm deletion.");
      return;
    }

    try {
      setActionLoading(true);
      const res = await fetch('/api/rights/replace-from-reserve', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Dashboard-Request': '1',
        },
        body: JSON.stringify({
          post_id: replaceModalClip.post_id,
          action_on_claimed: replaceAction,
          delete_confirmation: deleteConfirmationText,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Replacement failed');

      setMessage({
        text: `Successfully replaced ${data.replaced_post_id} with reserve clip ${data.new_post_id}!`,
        type: 'success',
      });
      setReplaceModalClip(null);
      await loadAllData();
    } catch (err: any) {
      setMessage({ text: err.message, type: 'error' });
    } finally {
      setActionLoading(false);
    }
  };

  const handlePublishedAction = async (postId: string, videoId: string | undefined, action: string) => {
    try {
      setActionLoading(true);
      const res = await fetch('/api/rights/action-published', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Dashboard-Request': '1',
        },
        body: JSON.stringify({
          post_id: postId,
          video_id: videoId,
          action,
          delete_confirmation: deleteModalConfirm,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Action failed');

      setMessage({ text: `Action '${action}' applied to ${postId}`, type: 'success' });
      setDeleteModalClip(null);
      setDeleteModalConfirm('');
      await loadAllData();
    } catch (err: any) {
      setMessage({ text: err.message, type: 'error' });
    } finally {
      setActionLoading(false);
    }
  };

  const handleBlocklistApprove = async (id: number) => {
    await fetch(`/api/rights/blocklist/${id}/approve`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Dashboard-Request': '1',
      },
    });
    await loadAllData();
  };

  const handleBlocklistDismiss = async (id: number) => {
    await fetch(`/api/rights/blocklist/${id}/dismiss`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Dashboard-Request': '1',
      },
    });
    await loadAllData();
  };

  const handleBlocklistToggle = async (id: number) => {
    await fetch(`/api/rights/blocklist/${id}/toggle`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Dashboard-Request': '1',
      },
    });
    await loadAllData();
  };

  const handleAddManualBlocklist = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newBlockValue.trim()) return;
    await fetch('/api/rights/blocklist/add', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Dashboard-Request': '1',
      },
      body: JSON.stringify({ type: newBlockType, value: newBlockValue.trim() }),
    });
    setNewBlockValue('');
    await loadAllData();
  };

  return (
    <div className="flex-1 flex flex-col gap-[28px] p-[20px] lg:p-[36px] max-w-[1440px] mx-auto w-full">
      {/* View Header */}
      <div className="flex flex-col gap-[6px]">
        <div className="flex items-center gap-[12px]">
          <div className="size-[36px] rounded-[10px] bg-[#D4412F] flex items-center justify-center text-white shadow-sm">
            <ShieldAlert size={20} />
          </div>
          <h1 className="font-heading font-extrabold text-[28px] lg:text-[32px] text-[#1D3B33] tracking-tight">
            Rights Check & Claims
          </h1>
          {status?.is_local_only && (
            <span className="font-ui text-[12px] font-bold px-[8px] py-[3px] rounded-full bg-[#D97706]/15 text-[#D97706] border border-[#D97706]/30">
              Local-Only Protection
            </span>
          )}
        </div>
        <p className="font-ui text-[15px] text-[#6F7A73]">
          Pre-upload risk detection (5 layers), YouTube scheduled review gate, and learned Content ID memory.
        </p>
      </div>

      {message && (
        <div
          className={`p-[14px] rounded-[12px] font-ui text-[14px] flex items-center justify-between border ${
            message.type === 'success'
              ? 'bg-[#2E7D4F]/10 text-[#2E7D4F] border-[#2E7D4F]/30'
              : 'bg-[#D4412F]/10 text-[#D4412F] border-[#D4412F]/30'
          }`}
        >
          <span>{message.text}</span>
          <button onClick={() => setMessage(null)} className="cursor-pointer">
            <X size={16} />
          </button>
        </div>
      )}

      {/* SECTION 1: Protection Status Card */}
      <div className="bg-white rounded-[16px] p-[20px] lg:p-[28px] border border-[#D5DBD5]/40 flex flex-col gap-[20px] shadow-sm">
        <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-[16px]">
          <div>
            <h2 className="font-heading font-semibold text-[20px] text-[#1D3B33]">
              Protection Status & Limits
            </h2>
            <p className="font-ui text-[13px] text-[#6F7A73]">
              Multi-layer detection status, daily provider quotas, and historical claim frequency.
            </p>
          </div>
          <div className="flex items-center gap-[20px]">
            <div className="text-right">
              <span className="font-heading font-bold text-[24px] text-[#1D3B33] leading-none">
                {status?.stats.claims_per_100_published ?? 0}%
              </span>
              <p className="font-ui text-[12px] text-[#6F7A73]">claims / 100 published</p>
            </div>
            <div className="h-[32px] w-px bg-[#D5DBD5]" />
            <div className="text-right">
              <span className="font-heading font-bold text-[24px] text-[#D4412F] leading-none">
                {status?.stats.total_claims ?? 0}
              </span>
              <p className="font-ui text-[12px] text-[#6F7A73]">total claims noted</p>
            </div>
          </div>
        </div>

        {status?.is_local_only && (
          <div className="p-[14px] rounded-[12px] bg-[#D97706]/10 border border-[#D97706]/30 flex items-start gap-[12px]">
            <Info className="text-[#D97706] shrink-0 mt-0.5" size={18} />
            <div className="font-ui text-[13px] text-[#1D3B33] leading-relaxed">
              <strong>Reduced Protection (Local Only Mode):</strong> Cloud Vision web detection and paid API calls are
              disabled ($0.00 spend). Local OCR (RapidOCR), YAMNet music detection, and historical perceptual hash matching
              are fully active.
            </div>
          </div>
        )}

        {/* Layer Badges & Daily Caps */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-[14px]">
          <div className="p-[14px] rounded-[12px] bg-[#EDF0EC] flex flex-col gap-[6px]">
            <span className="font-ui text-[12px] text-[#6F7A73] font-medium">Layer 1: Agency Blocklist & OCR</span>
            <div className="flex items-center gap-[6px] text-[#2E7D4F] font-semibold text-[14px]">
              <CheckCircle2 size={16} /> Active (Local RapidOCR)
            </div>
          </div>

          <div className="p-[14px] rounded-[12px] bg-[#EDF0EC] flex flex-col gap-[6px]">
            <span className="font-ui text-[12px] text-[#6F7A73] font-medium">Layer 2: Audio & YAMNet</span>
            <div className="flex items-center gap-[6px] text-[#2E7D4F] font-semibold text-[14px]">
              <CheckCircle2 size={16} /> Active (Local ONNX)
            </div>
            <span className="font-ui text-[11px] text-[#6F7A73]">
              AudD Fingerprint: {status?.providers.audd.calls_today ?? 0} / {status?.providers.audd.daily_cap ?? 40} today
            </span>
          </div>

          <div className="p-[14px] rounded-[12px] bg-[#EDF0EC] flex flex-col gap-[6px]">
            <span className="font-ui text-[12px] text-[#6F7A73] font-medium">Layer 3: Cloud Vision Web</span>
            <div className="flex items-center gap-[6px] text-[#6F7A73] font-semibold text-[14px]">
              {status?.active_layers.layer3_cloud_vision ? (
                <>
                  <CheckCircle2 className="text-[#2E7D4F]" size={16} /> Active
                </>
              ) : (
                <>
                  <Lock size={16} /> Local Only ($0 spend)
                </>
              )}
            </div>
            <span className="font-ui text-[11px] text-[#6F7A73]">
              {status?.providers.google_vision.calls_today ?? 0} / {status?.providers.google_vision.daily_cap ?? 25} daily cap
            </span>
          </div>

          <div className="p-[14px] rounded-[12px] bg-[#EDF0EC] flex flex-col gap-[6px]">
            <span className="font-ui text-[12px] text-[#6F7A73] font-medium">Layer 4 & 5: Gate & History</span>
            <div className="flex items-center gap-[6px] text-[#2E7D4F] font-semibold text-[14px]">
              <CheckCircle2 size={16} /> Active (Policy: {status?.unreviewed_policy})
            </div>
            <span className="font-ui text-[11px] text-[#6F7A73]">
              Near-duplicate pHash & learned blocklist
            </span>
          </div>
        </div>
      </div>

      {/* SECTION 2: Needs Review (Scheduled Clips) */}
      <div className="bg-white rounded-[16px] p-[20px] lg:p-[28px] border border-[#D5DBD5]/40 flex flex-col gap-[20px] shadow-sm">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-[12px]">
          <div>
            <h2 className="font-heading font-semibold text-[20px] text-[#1D3B33]">
              Needs Review (Scheduled Clips)
            </h2>
            <p className="font-ui text-[13px] text-[#6F7A73]">
              Pre-broadcast review window for scheduled clips not yet public. Verified clips remain scheduled; flagged clips can be held or replaced from reserve.
            </p>
          </div>
          <button
            onClick={loadAllData}
            className="flex items-center gap-[6px] px-[12px] py-[6px] rounded-[8px] border border-[#D5DBD5] text-[#1D3B33] text-[13px] font-semibold hover:bg-gray-50 cursor-pointer"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>

        <div className="flex flex-col divide-y divide-[#D5DBD5]">
          {reviewClips.length === 0 ? (
            <div className="py-[32px] text-center text-[#6F7A73] font-ui text-[14px]">
              No scheduled clips pending review.
            </div>
          ) : (
            reviewClips.map((clip) => {
              const isHigh = clip.rights_risk >= 80 || clip.decision === 'block';
              const isReview = clip.rights_risk >= 30 && clip.rights_risk < 80;

              return (
                <div key={clip.post_id} className="py-[16px] flex flex-col gap-[12px]">
                  <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-[16px]">
                    <div className="flex items-center gap-[16px]">
                      <div className="flex flex-col text-left shrink-0 min-w-[90px]">
                        <span className="font-heading font-medium text-[15px] text-[#1D3B33]">
                          {formatTimeET(clip.slot_time)}
                        </span>
                        <span className="font-ui text-[11px] text-[#6F7A73]">
                          {clip.target_date ? formatDateET(clip.target_date, { month: 'short', day: 'numeric' }) : ''}
                        </span>
                      </div>
                      <Thumb src={clip.thumb_url} alt={stripHashtags(clip.title)} />
                      <div className="flex flex-col gap-[3px]">
                        <span className="font-ui font-semibold text-[15px] text-[#1D3B33]">
                          {stripHashtags(clip.title)}
                        </span>
                        <div className="flex items-center gap-[8px] font-ui text-[12px] text-[#6F7A73]">
                          <span>{clip.post_id}</span>
                          <span>·</span>
                          <span>u/{clip.uploader || 'unknown'}</span>
                          <span>·</span>
                          {clip.rights_status === 'cleared' ? (
                            <span className="text-[#2E7D4F] font-semibold">Cleared</span>
                          ) : clip.rights_status === 'held' ? (
                            <span className="text-[#D97706] font-semibold">Held (Private)</span>
                          ) : (
                            <span className="text-[#D4412F] font-semibold">Needs Check</span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Risk Badge & Actions */}
                    <div className="flex flex-wrap items-center gap-[10px] w-full lg:w-auto justify-end">
                      {isHigh ? (
                        <span className="px-[10px] py-[3px] rounded-full text-[12px] font-bold bg-[#D4412F]/15 text-[#D4412F] border border-[#D4412F]/30 flex items-center gap-[4px]">
                          <AlertTriangle size={13} /> High Risk ({clip.rights_risk})
                        </span>
                      ) : isReview ? (
                        <span className="px-[10px] py-[3px] rounded-full text-[12px] font-bold bg-[#D97706]/15 text-[#D97706] border border-[#D97706]/30 flex items-center gap-[4px]">
                          <ShieldAlert size={13} /> Needs Review ({clip.rights_risk})
                        </span>
                      ) : (
                        <span className="px-[10px] py-[3px] rounded-full text-[12px] font-bold bg-[#2E7D4F]/15 text-[#2E7D4F] border border-[#2E7D4F]/30 flex items-center gap-[4px]">
                          <ShieldCheck size={13} /> Cleared ({clip.rights_risk})
                        </span>
                      )}

                      {clip.video_id && (
                        <a
                          href={`https://studio.youtube.com/video/${clip.video_id}/edit`}
                          target="_blank"
                          rel="noreferrer"
                          className="flex items-center gap-[4px] px-[10px] py-[6px] rounded-[8px] border border-[#D5DBD5] text-[#1D3B33] text-[12px] font-semibold hover:bg-gray-50"
                        >
                          Studio <ExternalLink size={12} />
                        </a>
                      )}

                      <button
                        onClick={() => handleMarkClear(clip.post_id)}
                        disabled={actionLoading}
                        className="px-[10px] py-[6px] rounded-[8px] bg-[#2E7D4F] text-white text-[12px] font-semibold hover:bg-[#256841] transition-colors cursor-pointer"
                      >
                        Mark Clear
                      </button>

                      <button
                        onClick={() => handleHold(clip.post_id, clip.video_id)}
                        disabled={actionLoading}
                        className="px-[10px] py-[6px] rounded-[8px] bg-[#D97706]/15 border border-[#D97706]/40 text-[#D97706] text-[12px] font-semibold hover:bg-[#D97706]/25 transition-colors cursor-pointer"
                      >
                        Hold
                      </button>

                      <button
                        onClick={() => {
                          setClaimModalClip(clip);
                          setClaimForm({
                            claim_type: 'visual',
                            claimant: '',
                            asset_title: '',
                            found_in: '0:02 - 0:18',
                            action: 'held',
                            notes: '',
                          });
                        }}
                        className="px-[10px] py-[6px] rounded-[8px] bg-[#D4412F]/10 border border-[#D4412F]/30 text-[#D4412F] text-[12px] font-semibold hover:bg-[#D4412F]/20 transition-colors cursor-pointer"
                      >
                        Mark Claimed
                      </button>

                      <button
                        onClick={() => {
                          setReplaceModalClip(clip);
                          setReplaceAction('held');
                          setDeleteConfirmationText('');
                        }}
                        className="px-[10px] py-[6px] rounded-[8px] bg-[#1D3B33] text-white text-[12px] font-semibold hover:bg-[#132722] transition-colors cursor-pointer"
                      >
                        Replace from Reserve
                      </button>
                    </div>
                  </div>

                  {/* Findings breakdown */}
                  {clip.reasons && clip.reasons.length > 0 && (
                    <div className="ml-[102px] p-[10px] rounded-[8px] bg-gray-50 border border-gray-200 text-[12px] text-[#1D3B33] flex flex-wrap gap-[8px]">
                      <span className="font-semibold text-[#D4412F]">Flags:</span>
                      {clip.reasons.map((r, i) => (
                        <span key={i} className="px-[6px] py-[1px] rounded bg-white border border-gray-300 font-mono text-[11px]">
                          {r}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* SECTION 3: Recently Published (Last 7 Days) */}
      <div className="bg-white rounded-[16px] p-[20px] lg:p-[28px] border border-[#D5DBD5]/40 flex flex-col gap-[20px] shadow-sm">
        <div>
          <h2 className="font-heading font-semibold text-[20px] text-[#1D3B33]">
            Recently Published (Last 7 Days)
          </h2>
          <p className="font-ui text-[13px] text-[#6F7A73]">
            Check live videos. If YouTube Studio notifies you of a Content ID claim, mark it claimed or remove it immediately.
          </p>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left font-ui text-[14px]">
            <thead>
              <tr className="border-b border-[#D5DBD5] text-[#6F7A73] text-[12px] font-semibold uppercase tracking-wider">
                <th className="pb-[10px]">Date / Time</th>
                <th className="pb-[10px]">Video</th>
                <th className="pb-[10px]">Privacy</th>
                <th className="pb-[10px]">Claim Status</th>
                <th className="pb-[10px] text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#D5DBD5]">
              {publishedClips.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-[24px] text-center text-[#6F7A73]">
                    No published clips in the last 7 days.
                  </td>
                </tr>
              ) : (
                publishedClips.map((clip) => {
                  const isMissing = Boolean(
                    clip.is_missing ||
                    clip.privacy === 'missing' ||
                    clip.live_privacy === 'missing' ||
                    clip.live_status === 'missing'
                  );
                  const privacy = clip.live_privacy || clip.privacy || 'private';

                  return (
                    <tr key={clip.post_id} className="hover:bg-gray-50 transition-colors">
                      <td className="py-[12px] font-medium text-[#1D3B33] whitespace-nowrap">
                        {formatDateET(clip.target_date, { month: 'short', day: 'numeric' })} · {formatTimeET(clip.slot_time)}
                      </td>
                      <td className="py-[12px]">
                        <div className="flex items-center gap-[12px]">
                          <Thumb src={clip.thumb_url} alt={stripHashtags(clip.title)} />
                          <div className="flex flex-col min-w-0 max-w-[340px]">
                            <span className="font-semibold text-[#1D3B33] truncate">
                              {stripHashtags(clip.title)}
                            </span>
                            <div className="flex items-center gap-[6px] text-[12px] text-[#6F7A73] truncate">
                              <span>{clip.post_id}</span>
                              {clip.video_id && <span>· {clip.video_id}</span>}
                              {isMissing && (
                                <span className="text-[#D4412F] font-semibold">· Missing on YouTube</span>
                              )}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="py-[12px]">
                        {isMissing ? (
                          <span className="px-[8px] py-[2px] rounded text-[11px] font-bold uppercase tracking-wider bg-gray-200 text-gray-700 border border-gray-300">
                            Missing
                          </span>
                        ) : (
                          <span className={`px-[8px] py-[2px] rounded text-[11px] font-bold uppercase tracking-wider ${
                            privacy === 'public'
                              ? 'bg-[#2E7D4F]/15 text-[#2E7D4F]'
                              : privacy === 'unlisted'
                              ? 'bg-blue-100 text-blue-700'
                              : 'bg-[#D97706]/15 text-[#D97706]'
                          }`}>
                            {privacy}
                          </span>
                        )}
                      </td>
                      <td className="py-[12px]">
                        {clip.claim_status === 'claimed' ? (
                          <span className="px-[8px] py-[2px] rounded text-[11px] font-bold bg-[#D4412F]/15 text-[#D4412F] border border-[#D4412F]/30">
                            Claimed{clip.claim_action ? ` (${clip.claim_action})` : ''}
                          </span>
                        ) : (
                          <span className="text-[#6F7A73] text-[12px]">Clean</span>
                        )}
                      </td>
                      <td className="py-[12px] text-right whitespace-nowrap">
                        <div className="flex items-center justify-end gap-[8px]">
                          {clip.video_id && !isMissing && (
                            <a
                              href={`https://studio.youtube.com/video/${clip.video_id}/edit`}
                              target="_blank"
                              rel="noreferrer"
                              className="p-[6px] rounded-[6px] border border-[#D5DBD5] text-[#1D3B33] hover:bg-gray-100"
                              title="Studio"
                            >
                              <ExternalLink size={14} />
                            </a>
                          )}

                          <button
                            onClick={() => handlePublishedAction(clip.post_id, clip.video_id, 'keep')}
                            disabled={actionLoading}
                            className="px-[8px] py-[4px] rounded-[6px] border border-[#D5DBD5] text-[#1D3B33] text-[12px] font-semibold hover:bg-gray-100 cursor-pointer"
                          >
                            Keep
                          </button>

                          <button
                            onClick={() => handlePublishedAction(clip.post_id, clip.video_id, 'make_private')}
                            disabled={actionLoading || isMissing || privacy === 'private'}
                            className={`px-[8px] py-[4px] rounded-[6px] text-[12px] font-semibold ${
                              isMissing || privacy === 'private'
                                ? 'opacity-40 cursor-not-allowed bg-gray-100 text-gray-400 border border-gray-200'
                                : 'bg-[#D97706]/15 border border-[#D97706]/30 text-[#D97706] hover:bg-[#D97706]/25 cursor-pointer'
                            }`}
                          >
                            Make Private
                          </button>

                          <button
                            onClick={() => {
                              setDeleteModalClip(clip);
                              setDeleteModalConfirm('');
                            }}
                            disabled={actionLoading}
                            className="px-[8px] py-[4px] rounded-[6px] bg-[#D4412F]/10 border border-[#D4412F]/30 text-[#D4412F] text-[12px] font-semibold hover:bg-[#D4412F]/20 cursor-pointer"
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* SECTION 4: Claims History & Learned Blocklist (Two-Column) */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-[24px] items-start">
        {/* Left: Claims History */}
        <div className="bg-white rounded-[16px] p-[20px] lg:p-[28px] border border-[#D5DBD5]/40 flex flex-col gap-[16px] shadow-sm">
          <div className="flex items-center justify-between">
            <h3 className="font-heading font-semibold text-[20px] text-[#1D3B33]">
              Claims History ({claims.length})
            </h3>
          </div>
          <p className="font-ui text-[13px] text-[#6F7A73]">
            Historical Content ID claims logged in the database to train future risk detection.
          </p>

          <div className="flex flex-col divide-y divide-[#D5DBD5] max-h-[480px] overflow-y-auto">
            {claims.length === 0 ? (
              <div className="py-[24px] text-center text-[#6F7A73] text-[13px]">
                No claims recorded yet.
              </div>
            ) : (
              claims.map((c) => (
                <div key={c.id} className="py-[12px] flex flex-col gap-[6px]">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-[8px]">
                      <span className="font-ui font-semibold text-[14px] text-[#1D3B33]">
                        {c.claimant}
                      </span>
                      <span className="px-[6px] py-[1px] rounded text-[10px] font-bold uppercase bg-[#D4412F]/10 text-[#D4412F]">
                        {c.claim_type}
                      </span>
                    </div>
                    <span className="font-ui text-[12px] text-[#6F7A73]">
                      Action: <strong>{c.action}</strong>
                    </span>
                  </div>

                  <span className="font-ui text-[13px] text-[#1D3B33] font-medium">
                    {c.asset_title || 'Unnamed Asset'}
                  </span>

                  <div className="flex items-center gap-[10px] font-ui text-[11px] text-[#6F7A73]">
                    <span>Post: {c.post_id}</span>
                    <span>·</span>
                    <span>Range: {c.found_in || 'N/A'}</span>
                    <span>·</span>
                    <span>{c.source_name || 'r/funny'}</span>
                  </div>
                  {c.notes && (
                    <p className="font-ui text-[12px] text-[#D97706] italic">Note: {c.notes}</p>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* Right: Learned Blocklist Manager */}
        <div className="bg-white rounded-[16px] p-[20px] lg:p-[28px] border border-[#D5DBD5]/40 flex flex-col gap-[16px] shadow-sm">
          <div>
            <h3 className="font-heading font-semibold text-[20px] text-[#1D3B33]">
              Learned Rights Blocklist ({blocklist.active.length} active)
            </h3>
            <p className="font-ui text-[13px] text-[#6F7A73]">
              Keywords, claimants, tracks, uploaders, and subreddits learned from past claims.
            </p>
          </div>

          {/* Pending confirmation suggestions banner */}
          {blocklist.pending.length > 0 && (
            <div className="p-[14px] rounded-[12px] bg-[#D97706]/10 border border-[#D97706]/30 flex flex-col gap-[10px]">
              <span className="font-ui text-[12px] font-bold text-[#D97706] uppercase tracking-wider">
                Pending Confirmation ({blocklist.pending.length})
              </span>
              <div className="flex flex-col gap-[8px]">
                {blocklist.pending.map((p) => (
                  <div key={p.id} className="flex items-center justify-between bg-white p-[8px] rounded-[8px] border border-gray-200">
                    <div className="flex items-center gap-[8px]">
                      <span className="px-[6px] py-[1px] rounded bg-gray-100 text-[10px] font-bold uppercase text-[#6F7A73]">
                        {p.type}
                      </span>
                      <span className="font-ui font-medium text-[13px] text-[#1D3B33]">
                        {p.value}
                      </span>
                    </div>
                    <div className="flex items-center gap-[6px]">
                      <button
                        onClick={() => handleBlocklistApprove(p.id)}
                        className="px-[8px] py-[3px] rounded bg-[#2E7D4F] text-white text-[11px] font-bold cursor-pointer"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => handleBlocklistDismiss(p.id)}
                        className="px-[8px] py-[3px] rounded bg-gray-200 text-[#6F7A73] text-[11px] font-bold cursor-pointer"
                      >
                        Dismiss
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Add Manual Entry */}
          <form onSubmit={handleAddManualBlocklist} className="flex gap-[8px] items-center pt-[4px]">
            <select
              value={newBlockType}
              onChange={(e) => setNewBlockType(e.target.value)}
              className="px-[10px] py-[8px] rounded-[8px] border border-[#D5DBD5] font-ui text-[13px] bg-white text-[#1D3B33]"
            >
              <option value="text">Agency / Text</option>
              <option value="uploader">Uploader</option>
              <option value="subreddit">Subreddit</option>
              <option value="claimant">Claimant</option>
              <option value="track">Commercial Track</option>
            </select>
            <input
              type="text"
              placeholder="e.g. ViralHog, BadUser, survivor"
              value={newBlockValue}
              onChange={(e) => setNewBlockValue(e.target.value)}
              className="flex-1 px-[12px] py-[8px] rounded-[8px] border border-[#D5DBD5] font-ui text-[13px] text-[#1D3B33]"
            />
            <button
              type="submit"
              className="px-[12px] py-[8px] rounded-[8px] bg-[#1D3B33] text-white font-ui text-[13px] font-semibold flex items-center gap-[4px] cursor-pointer"
            >
              <Plus size={16} /> Add
            </button>
          </form>

          {/* Active List */}
          <div className="flex flex-col divide-y divide-[#D5DBD5] max-h-[300px] overflow-y-auto pt-[8px]">
            {blocklist.active.map((item) => (
              <div key={item.id} className="py-[8px] flex items-center justify-between">
                <div className="flex items-center gap-[8px]">
                  <span className="px-[6px] py-[1px] rounded bg-gray-100 text-[10px] font-bold uppercase text-[#6F7A73]">
                    {item.type}
                  </span>
                  <span className="font-ui text-[13px] font-medium text-[#1D3B33]">
                    {item.value}
                  </span>
                </div>
                <button
                  onClick={() => handleBlocklistToggle(item.id)}
                  className="text-[12px] font-semibold text-[#D4412F] hover:underline cursor-pointer"
                >
                  Disable
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* MODAL 1: Mark Claimed Modal with Dispute Warning */}
      {claimModalClip && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-[16px] backdrop-blur-xs">
          <div className="bg-white rounded-[16px] max-w-[540px] w-full p-[24px] lg:p-[32px] flex flex-col gap-[20px] shadow-xl animate-in fade-in zoom-in-95">
            <div className="flex items-center justify-between">
              <h3 className="font-heading font-semibold text-[22px] text-[#1D3B33]">
                Log Content ID Claim
              </h3>
              <button onClick={() => setClaimModalClip(null)} className="cursor-pointer text-[#6F7A73]">
                <X size={20} />
              </button>
            </div>

            <form onSubmit={handleSaveClaim} className="flex flex-col gap-[14px]">
              <div>
                <label className="font-ui text-[12px] font-semibold text-[#6F7A73]">Claim Type</label>
                <select
                  value={claimForm.claim_type}
                  onChange={(e) => setClaimForm({ ...claimForm, claim_type: e.target.value })}
                  className="w-full mt-[4px] px-[12px] py-[8px] rounded-[8px] border border-[#D5DBD5] font-ui text-[14px]"
                >
                  <option value="visual">Visual Match (e.g. CollabDRM, ViralHog)</option>
                  <option value="audio">Audio Match (e.g. SME, UMG, WMG)</option>
                  <option value="both">Both Audio and Visual</option>
                </select>
              </div>

              <div>
                <label className="font-ui text-[12px] font-semibold text-[#6F7A73]">Claimant / Company</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. CollabDRM, SME, Warner Chappell"
                  value={claimForm.claimant}
                  onChange={(e) => setClaimForm({ ...claimForm, claimant: e.target.value })}
                  className="w-full mt-[4px] px-[12px] py-[8px] rounded-[8px] border border-[#D5DBD5] font-ui text-[14px]"
                />
              </div>

              <div>
                <label className="font-ui text-[12px] font-semibold text-[#6F7A73]">Asset Title / Song Title</label>
                <input
                  type="text"
                  placeholder="e.g. Collab ViralHog - Female Pheasant, Eye of the Tiger"
                  value={claimForm.asset_title}
                  onChange={(e) => setClaimForm({ ...claimForm, asset_title: e.target.value })}
                  className="w-full mt-[4px] px-[12px] py-[8px] rounded-[8px] border border-[#D5DBD5] font-ui text-[14px]"
                />
              </div>

              <div>
                <label className="font-ui text-[12px] font-semibold text-[#6F7A73]">Found in (Timestamp range)</label>
                <input
                  type="text"
                  placeholder="e.g. 0:02 - 0:19"
                  value={claimForm.found_in}
                  onChange={(e) => setClaimForm({ ...claimForm, found_in: e.target.value })}
                  className="w-full mt-[4px] px-[12px] py-[8px] rounded-[8px] border border-[#D5DBD5] font-ui text-[14px]"
                />
              </div>

              <div>
                <label className="font-ui text-[12px] font-semibold text-[#6F7A73]">Action Taken</label>
                <select
                  value={claimForm.action}
                  onChange={(e) => setClaimForm({ ...claimForm, action: e.target.value })}
                  className="w-full mt-[4px] px-[12px] py-[8px] rounded-[8px] border border-[#D5DBD5] font-ui text-[14px]"
                >
                  <option value="held">Hold (Set private on YouTube)</option>
                  <option value="kept">Kept (Leave published / revenue shared)</option>
                  <option value="pulled">Pulled (Deleted from YouTube)</option>
                  <option value="disputed">Disputed (Formal YouTube dispute)</option>
                </select>
              </div>

              {/* CRITICAL WARNING ON DISPUTE ACTION */}
              {claimForm.action === 'disputed' && (
                <div className="p-[12px] rounded-[10px] bg-[#D4412F]/15 border border-[#D4412F]/40 flex items-start gap-[10px]">
                  <AlertTriangle className="text-[#D4412F] shrink-0 mt-0.5" size={18} />
                  <div className="font-ui text-[12px] text-[#D4412F] leading-tight">
                    <strong>Warning on Disputing:</strong> Disputing a Content ID claim notifies the copyright holder. If
                    they reject the dispute and issue a DMCA notice, your channel will receive a <strong>copyright strike</strong>.
                    Only dispute if you possess explicit written licensing or original copyright.
                  </div>
                </div>
              )}

              <div>
                <label className="font-ui text-[12px] font-semibold text-[#6F7A73]">Notes</label>
                <input
                  type="text"
                  placeholder="Optional notes or details"
                  value={claimForm.notes}
                  onChange={(e) => setClaimForm({ ...claimForm, notes: e.target.value })}
                  className="w-full mt-[4px] px-[12px] py-[8px] rounded-[8px] border border-[#D5DBD5] font-ui text-[14px]"
                />
              </div>

              <div className="flex items-center justify-end gap-[10px] pt-[8px]">
                <button
                  type="button"
                  onClick={() => setClaimModalClip(null)}
                  className="px-[14px] py-[8px] rounded-[8px] border border-[#D5DBD5] text-[#1D3B33] text-[13px] font-semibold cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  className="px-[16px] py-[8px] rounded-[8px] bg-[#D4412F] text-white text-[13px] font-semibold hover:bg-[#b83424] cursor-pointer"
                >
                  Save Claim & Learn
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 2: Replace from Reserve Modal */}
      {replaceModalClip && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-[16px] backdrop-blur-xs">
          <div className="bg-white rounded-[16px] max-w-[500px] w-full p-[24px] lg:p-[32px] flex flex-col gap-[20px] shadow-xl">
            <div className="flex items-center justify-between">
              <h3 className="font-heading font-semibold text-[22px] text-[#1D3B33]">
                Replace from Reserve
              </h3>
              <button onClick={() => setReplaceModalClip(null)} className="cursor-pointer text-[#6F7A73]">
                <X size={20} />
              </button>
            </div>

            <p className="font-ui text-[14px] text-[#6F7A73]">
              The system will select the highest-scoring reserve pool clip that has cleared the rights check, schedule it
              into slot <strong>{replaceModalClip.slot_time}</strong>, and update the claimed video.
            </p>

            <form onSubmit={handleReplaceFromReserve} className="flex flex-col gap-[14px]">
              <div>
                <label className="font-ui text-[12px] font-semibold text-[#6F7A73]">Action on Claimed Video</label>
                <select
                  value={replaceAction}
                  onChange={(e) => setReplaceAction(e.target.value as any)}
                  className="w-full mt-[4px] px-[12px] py-[8px] rounded-[8px] border border-[#D5DBD5] font-ui text-[14px]"
                >
                  <option value="held">Hold (Set private on YouTube — Recommended)</option>
                  <option value="delete">Delete from YouTube (Permanent)</option>
                </select>
              </div>

              {replaceAction === 'delete' && (
                <div className="flex flex-col gap-[6px]">
                  <label className="font-ui text-[12px] font-semibold text-[#D4412F]">
                    Type &quot;DELETE&quot; to confirm permanent deletion:
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="DELETE"
                    value={deleteConfirmationText}
                    onChange={(e) => setDeleteConfirmationText(e.target.value)}
                    className="w-full px-[12px] py-[8px] rounded-[8px] border border-[#D4412F] font-mono text-[14px] text-[#D4412F]"
                  />
                </div>
              )}

              <div className="flex items-center justify-end gap-[10px] pt-[8px]">
                <button
                  type="button"
                  onClick={() => setReplaceModalClip(null)}
                  className="px-[14px] py-[8px] rounded-[8px] border border-[#D5DBD5] text-[#1D3B33] text-[13px] font-semibold cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  className="px-[16px] py-[8px] rounded-[8px] bg-[#1D3B33] text-white text-[13px] font-semibold hover:bg-[#132722] cursor-pointer"
                >
                  Confirm Replacement
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 3: Delete Published Video Modal */}
      {deleteModalClip && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-[16px] backdrop-blur-xs">
          <div className="bg-white rounded-[16px] max-w-[460px] w-full p-[24px] lg:p-[32px] flex flex-col gap-[16px] shadow-xl">
            <h3 className="font-heading font-semibold text-[20px] text-[#D4412F]">
              {deleteModalClip.is_missing ? 'Remove Missing Clip Record' : 'Confirm Video Deletion'}
            </h3>
            <p className="font-ui text-[13px] text-[#6F7A73]">
              {deleteModalClip.is_missing ? (
                <>This video is already missing/deleted from YouTube. Type <strong>DELETE</strong> to remove this record from the database.</>
              ) : (
                <>Are you sure you want to permanently delete <strong>{deleteModalClip.title}</strong> from YouTube? This cannot be undone.</>
              )}
            </p>
            <div className="flex flex-col gap-[6px]">
              <label className="font-ui text-[12px] font-semibold text-[#D4412F]">
                Type &quot;DELETE&quot; to proceed:
              </label>
              <input
                type="text"
                placeholder="DELETE"
                value={deleteModalConfirm}
                onChange={(e) => setDeleteModalConfirm(e.target.value)}
                className="w-full px-[12px] py-[8px] rounded-[8px] border border-[#D4412F] font-mono text-[14px] text-[#D4412F]"
              />
            </div>
            <div className="flex items-center justify-end gap-[10px] pt-[8px]">
              <button
                type="button"
                onClick={() => setDeleteModalClip(null)}
                className="px-[14px] py-[8px] rounded-[8px] border border-[#D5DBD5] text-[#1D3B33] text-[13px] font-semibold cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={deleteModalConfirm !== 'DELETE' || actionLoading}
                onClick={() => handlePublishedAction(deleteModalClip.post_id, deleteModalClip.video_id, 'delete')}
                className="px-[16px] py-[8px] rounded-[8px] bg-[#D4412F] text-white text-[13px] font-semibold hover:bg-[#b83424] disabled:opacity-50 cursor-pointer"
              >
                {deleteModalClip.is_missing ? 'Remove Record' : 'Delete Video'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
