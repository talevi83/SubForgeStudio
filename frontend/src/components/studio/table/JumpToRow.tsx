import React, { useState } from 'react';
import { ArrowDownToLine, ArrowUpToLine } from 'lucide-react';
import { useJobStore } from '../../../store/useJobStore';
import { useT } from '../../../i18n';
import type { SubtitleSegment } from '../../../types';
import { parseTimecode } from '../../../utils/time';

interface JumpToRowProps {
  filteredSegments: SubtitleSegment[];
  scrollToListIndex: (listIdx: number) => void;
  scrollToTop: () => void;
  scrollToBottom: () => void;
}

/** Jump to a line number ("#1850") or a timestamp ("1:15:00"), plus top/bottom buttons. */
export const JumpToRow: React.FC<JumpToRowProps> = ({ filteredSegments, scrollToListIndex, scrollToTop, scrollToBottom }) => {
  const setSelectedSegmentIndex = useJobStore((s) => s.setSelectedSegmentIndex);
  const seekTo = useJobStore((s) => s.seekTo);
  const t = useT();
  const [jumpInput, setJumpInput] = useState('');

  const handleJumpKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter') return;
    const raw = jumpInput.trim().replace(/^#/, '');
    if (!raw || filteredSegments.length === 0) return;
    e.preventDefault();

    // If input contains a colon (e.g. "1:15:00" or "45:30"), jump to timestamp
    if (raw.includes(':')) {
      const sec = parseTimecode(raw);
      if (sec !== null) {
        let bestIdx = 0;
        for (let i = 0; i < filteredSegments.length; i++) {
          if (filteredSegments[i].start_time >= sec) {
            bestIdx = i;
            break;
          }
          bestIdx = i;
        }
        const seg = filteredSegments[bestIdx];
        scrollToListIndex(bestIdx);
        setSelectedSegmentIndex(seg.index);
        seekTo(seg.start_time, false);
        setJumpInput('');
      }
      return;
    }

    const num = Number(raw);
    if (!Number.isFinite(num) || num <= 0) return;
    let bestIdx = filteredSegments.findIndex((s) => s.index >= num);
    if (bestIdx < 0) bestIdx = filteredSegments.length - 1;
    const seg = filteredSegments[bestIdx];
    scrollToListIndex(bestIdx);
    setSelectedSegmentIndex(seg.index);
    setJumpInput('');
  };

  return (
    <div className="flex items-center gap-1">
      <input
        type="text"
        dir="ltr"
        value={jumpInput}
        onChange={(e) => setJumpInput(e.target.value)}
        onKeyDown={handleJumpKeyDown}
        placeholder={t('table.jumpPlaceholder')}
        title={t('table.jumpTitle')}
        aria-label={t('table.jumpTitle')}
        data-testid="jump-to-row-input"
        className="w-24 bg-surface/80 border border-line rounded-md px-2 py-0.5 text-[11px] font-mono text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
      />
      <button
        type="button"
        onClick={scrollToTop}
        title={t('table.scrollToTopTitle')}
        aria-label={t('table.scrollToTopTitle')}
        className="p-1 rounded border border-line bg-surface/70 text-zinc-400 hover:text-white hover:border-indigo-500/60 cursor-pointer"
      >
        <ArrowUpToLine className="w-3 h-3" />
      </button>
      <button
        type="button"
        onClick={scrollToBottom}
        title={t('table.scrollToBottomTitle')}
        aria-label={t('table.scrollToBottomTitle')}
        data-testid="scroll-to-bottom"
        className="p-1 rounded border border-line bg-surface/70 text-zinc-400 hover:text-white hover:border-indigo-500/60 cursor-pointer"
      >
        <ArrowDownToLine className="w-3 h-3" />
      </button>
    </div>
  );
};
