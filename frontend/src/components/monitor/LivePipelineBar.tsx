import React from 'react';
import { useJobStore } from '../../store/useJobStore';
import {
  Search,
  Music,
  Mic,
  Sliders,
  Languages,
  Clock,
  Zap,
  StopCircle,
} from 'lucide-react';

const STAGES = [
  { key: 'PROBING', label: 'Probe Streams', icon: Search },
  { key: 'EXTRACTING_AUDIO', label: 'Extract Audio', icon: Music },
  { key: 'TRANSCRIBING', label: 'Transcribe (Whisper)', icon: Mic },
  { key: 'REFINING', label: 'Refine & Balance', icon: Sliders },
  { key: 'TRANSLATING', label: 'Translate (Gemini)', icon: Languages },
];

export const LivePipelineBar: React.FC = () => {
  const { activeJob, cancelActiveJob, systemInfo } = useJobStore();

  if (!activeJob) return null;

  const currentStatus = activeJob.status;
  const isCompleted = currentStatus === 'COMPLETED';
  const isFailed = currentStatus === 'FAILED';
  const isCancelled = currentStatus === 'CANCELLED';
  const isRunning = !isCompleted && !isFailed && !isCancelled && currentStatus !== 'IDLE';

  const formatEta = (seconds?: number | null) => {
    if (!seconds || seconds <= 0) return null;
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    if (mins > 0) return `${mins}m ${secs}s`;
    return `${secs}s`;
  };

  const getStageStatus = (stageKey: string) => {
    if (isCompleted) return 'done';
    if (isFailed || isCancelled) return 'idle';

    const stageOrder = ['PROBING', 'EXTRACTING_AUDIO', 'TRANSCRIBING', 'REFINING', 'TRANSLATING'];
    const currentIndex = stageOrder.indexOf(currentStatus);
    const stageIndex = stageOrder.indexOf(stageKey);

    if (stageIndex < currentIndex) return 'done';
    if (stageIndex === currentIndex) return 'active';
    return 'idle';
  };

  const isCuda = systemInfo?.hardware?.cuda_available;

  return (
    <div className="bg-[#18181b] border-b border-[#27272a] px-6 py-3 shadow-md">
      <div className="flex items-center justify-between gap-4">
        {/* Step-by-step Stepper */}
        <div className="flex items-center gap-1 flex-1 overflow-x-auto py-1">
          {STAGES.map((s, idx) => {
            const status = getStageStatus(s.key);
            const Icon = s.icon;
            const isLast = idx === STAGES.length - 1;

            return (
              <React.Fragment key={s.key}>
                <div
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                    status === 'active'
                      ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/40 shadow-sm'
                      : status === 'done'
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : 'bg-[#121214] text-zinc-500 border border-[#27272a]'
                  }`}
                >
                  <Icon
                    className={`w-3.5 h-3.5 ${
                      status === 'active'
                        ? 'animate-pulse text-indigo-400'
                        : status === 'done'
                        ? 'text-emerald-400'
                        : 'text-zinc-600'
                    }`}
                  />
                  <span className="whitespace-nowrap">{s.label}</span>
                </div>
                {!isLast && <div className="w-3 h-0.5 bg-[#27272a] flex-shrink-0" />}
              </React.Fragment>
            );
          })}
        </div>

        {/* Live Metrics: Speed, ETA, Device Badge & Cancel */}
        <div className="flex items-center gap-3 text-xs flex-shrink-0">
          {isRunning && activeJob.speed_factor && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-[#121214] border border-[#27272a] text-zinc-300 font-mono">
              <Zap className="w-3.5 h-3.5 text-amber-400" />
              <span>{activeJob.speed_factor}x Real-Time</span>
            </div>
          )}

          {isRunning && activeJob.eta_seconds && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-[#121214] border border-[#27272a] text-zinc-300 font-mono">
              <Clock className="w-3.5 h-3.5 text-indigo-400" />
              <span>ETA: {formatEta(activeJob.eta_seconds)}</span>
            </div>
          )}

          <div className="px-2.5 py-1 rounded bg-zinc-800 border border-zinc-700/60 text-zinc-300 font-semibold text-[11px]">
            {isCuda ? 'CUDA FP16' : 'CPU INT8'}
          </div>

          {isRunning && (
            <button
              onClick={cancelActiveJob}
              className="flex items-center gap-1 px-3 py-1 rounded-md bg-rose-500/15 hover:bg-rose-500/25 text-rose-400 border border-rose-500/30 font-medium transition-colors cursor-pointer"
            >
              <StopCircle className="w-3.5 h-3.5" />
              Cancel
            </button>
          )}
        </div>
      </div>

      {/* Realtime Progress Bar */}
      <div className="mt-2.5 w-full bg-[#121214] h-1.5 rounded-full overflow-hidden border border-[#27272a]">
        <div
          className={`h-full transition-all duration-300 rounded-full ${
            isFailed
              ? 'bg-rose-500'
              : isCompleted
              ? 'bg-emerald-500'
              : 'bg-gradient-to-r from-indigo-500 to-violet-500'
          }`}
          style={{ width: `${Math.min(100, Math.max(0, activeJob.progress_percent))}%` }}
        />
      </div>
    </div>
  );
};
