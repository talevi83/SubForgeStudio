import React, { useRef, useEffect } from 'react';
import { useJobStore } from '../../store/useJobStore';
import { Radio } from 'lucide-react';

export const LiveStreamFeed: React.FC = () => {
  const { activeJob } = useJobStore();
  const feedRef = useRef<HTMLDivElement | null>(null);

  const segments = activeJob?.segments || [];
  const isRunning =
    activeJob?.status === 'TRANSCRIBING' || activeJob?.status === 'TRANSLATING';

  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [segments.length]);

  if (!isRunning && segments.length === 0) return null;

  return (
    <div className="bg-[#18181b] border border-[#27272a] rounded-xl p-4 shadow-lg space-y-3">
      <div className="flex items-center justify-between border-b border-[#27272a] pb-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-zinc-200">
          <Radio className="w-3.5 h-3.5 text-indigo-400 animate-pulse" />
          <span>Live Segment Stream</span>
        </div>
        <span className="text-[11px] text-zinc-400">
          {segments.length} segments received
        </span>
      </div>

      <div
        ref={feedRef}
        className="max-h-48 overflow-y-auto space-y-2 pr-1 font-mono text-xs"
      >
        {segments.map((seg) => (
          <div
            key={seg.index}
            className="p-2 rounded bg-[#121214] border border-[#27272a] flex items-start justify-between gap-3"
          >
            <div className="flex items-center gap-2 text-zinc-500 text-[11px]">
              <span>#{seg.index}</span>
              <span className="text-zinc-400">
                {seg.start_time.toFixed(1)}s - {seg.end_time.toFixed(1)}s
              </span>
            </div>

            <div className="flex-1 text-right text-zinc-200">
              <p className="font-sans text-xs">{seg.original_text}</p>
              {seg.translated_text && (
                <p className="text-amber-300 text-xs font-rtl-hebrew mt-0.5" dir="rtl">
                  {seg.translated_text}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
