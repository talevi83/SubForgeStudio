import React, { useState, useRef, useEffect, useMemo } from 'react';
import { useJobStore } from '../../store/useJobStore';
import type { SubtitleSegment } from '../../types';
import { CpsBadge } from './CpsBadge';
import {
  Search,
  Replace,
  Plus,
  Minus,
} from 'lucide-react';

export const SubtitleTable: React.FC = () => {
  const {
    activeJob,
    videoCurrentTime,
    selectedSegmentIndex,
    setSelectedSegmentIndex,
    updateSegment,
  } = useJobStore();

  const [searchTerm, setSearchTerm] = useState('');
  const [replaceTerm, setReplaceTerm] = useState('');
  const [targetColumn, setTargetColumn] = useState<'translated' | 'original'>('translated');
  const [autoScroll, setAutoScroll] = useState(true);

  const tableContainerRef = useRef<HTMLDivElement | null>(null);
  const activeRowRef = useRef<HTMLTableRowElement | null>(null);

  const segments = activeJob?.segments || [];
  const isRtl =
    activeJob?.target_language?.toLowerCase() === 'hebrew' ||
    activeJob?.target_language?.toLowerCase() === 'arabic';

  // Format seconds to HH:MM:SS,mmm
  const formatTimecode = (seconds: number) => {
    const sec = Math.max(0, seconds);
    const hours = Math.floor(sec / 3600);
    const mins = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    const ms = Math.floor((sec - Math.floor(sec)) * 1000);
    return `${hours.toString().padStart(2, '0')}:${mins
      .toString()
      .padStart(2, '0')}:${s.toString().padStart(2, '0')},${ms
      .toString()
      .padStart(3, '0')}`;
  };

  // Filter segments by search term
  const filteredSegments = useMemo(() => {
    if (!searchTerm.trim()) return segments;
    const term = searchTerm.toLowerCase();
    return segments.filter((s) => {
      const origMatch = s.original_text.toLowerCase().includes(term);
      const transMatch = s.translated_text?.toLowerCase().includes(term);
      return origMatch || transMatch;
    });
  }, [segments, searchTerm]);

  // Handle Find and Replace All
  const handleReplaceAll = async () => {
    if (!searchTerm.trim() || !activeJob) return;

    for (const seg of segments) {
      if (!seg.id) continue;
      if (targetColumn === 'translated' && seg.translated_text) {
        if (seg.translated_text.includes(searchTerm)) {
          const updated = seg.translated_text.replaceAll(searchTerm, replaceTerm);
          await updateSegment(seg.id, { translated_text: updated });
        }
      } else if (targetColumn === 'original' && seg.original_text) {
        if (seg.original_text.includes(searchTerm)) {
          const updated = seg.original_text.replaceAll(searchTerm, replaceTerm);
          await updateSegment(seg.id, { original_text: updated });
        }
      }
    }
  };

  // Nudge timing by delta (+/- 0.1s)
  const handleNudge = (
    seg: SubtitleSegment,
    field: 'start_time' | 'end_time',
    delta: number
  ) => {
    if (!seg.id) return;
    const newVal = Math.max(0, Number((seg[field] + delta).toFixed(3)));
    updateSegment(seg.id, { [field]: newVal });
  };

  // Auto-scroll to active video playback timestamp
  useEffect(() => {
    if (autoScroll && activeRowRef.current && tableContainerRef.current) {
      activeRowRef.current.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
      });
    }
  }, [videoCurrentTime, autoScroll]);

  return (
    <div className="flex flex-col h-full bg-[#18181b] border border-[#27272a] rounded-xl overflow-hidden shadow-xl">
      {/* Search & Replace Header */}
      <div className="p-3 bg-[#121214] border-b border-[#27272a] space-y-2">
        <div className="flex items-center gap-2">
          {/* Search Input */}
          <div className="relative flex-1">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              placeholder="Search subtitle text..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-[#18181b] border border-[#27272a] rounded-lg pl-8 pr-3 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          {/* Replace Input */}
          <div className="relative flex-1">
            <Replace className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              placeholder="Replace with..."
              value={replaceTerm}
              onChange={(e) => setReplaceTerm(e.target.value)}
              className="w-full bg-[#18181b] border border-[#27272a] rounded-lg pl-8 pr-3 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          {/* Target Column Selector */}
          <select
            value={targetColumn}
            onChange={(e: any) => setTargetColumn(e.target.value)}
            className="bg-[#18181b] border border-[#27272a] rounded-lg px-2.5 py-1.5 text-xs text-zinc-300 focus:outline-none focus:border-indigo-500"
          >
            <option value="translated">In Translated</option>
            <option value="original">In Original</option>
          </select>

          {/* Replace All CTA */}
          <button
            onClick={handleReplaceAll}
            disabled={!searchTerm.trim()}
            className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-xs font-semibold text-zinc-200 transition-colors cursor-pointer"
          >
            Replace All
          </button>
        </div>

        {/* Status Bar */}
        <div className="flex items-center justify-between text-[11px] text-zinc-400 px-1">
          <span>
            Showing <strong>{filteredSegments.length}</strong> of{' '}
            <strong>{segments.length}</strong> subtitle blocks
          </span>

          <label className="flex items-center gap-1.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="w-3.5 h-3.5 rounded border-zinc-700 text-indigo-600 bg-zinc-800"
            />
            <span>Auto-scroll with video</span>
          </label>
        </div>
      </div>

      {/* Virtualized/Scrollable Subtitles Table */}
      <div ref={tableContainerRef} className="flex-1 overflow-y-auto">
        <table className="w-full text-left border-collapse text-xs">
          <thead className="bg-[#141416] text-zinc-400 sticky top-0 z-10 border-b border-[#27272a] uppercase font-semibold text-[10px] tracking-wider select-none">
            <tr>
              <th className="py-2.5 px-3 w-12 text-center">#</th>
              <th className="py-2.5 px-3 w-48">Timecode (Start / End)</th>
              <th className="py-2.5 px-3 w-1/2">Original Dialogue</th>
              <th className="py-2.5 px-3 w-1/2">Translated Subtitle</th>
              <th className="py-2.5 px-3 w-20 text-center">Reading Speed</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#27272a]/60 font-sans">
            {filteredSegments.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-12 text-center text-zinc-500">
                  {segments.length === 0
                    ? 'No subtitle segments transcribed yet.'
                    : 'No subtitle segments matching search term.'}
                </td>
              </tr>
            ) : (
              filteredSegments.map((seg) => {
                const isCurrentlySpoken =
                  videoCurrentTime >= seg.start_time && videoCurrentTime <= seg.end_time;
                const isSelected = selectedSegmentIndex === seg.index;

                return (
                  <tr
                    key={seg.id || seg.index}
                    ref={isCurrentlySpoken ? activeRowRef : undefined}
                    onClick={() => setSelectedSegmentIndex(seg.index)}
                    className={`transition-colors cursor-pointer group ${
                      isCurrentlySpoken
                        ? 'bg-indigo-950/40 border-l-2 border-indigo-500'
                        : isSelected
                        ? 'bg-zinc-800/60'
                        : 'hover:bg-zinc-800/30'
                    }`}
                  >
                    {/* Index */}
                    <td className="py-2 px-3 text-center text-zinc-500 font-mono text-[11px] select-none">
                      {seg.index}
                    </td>

                    {/* Start & End Timecode with +/- 100ms Nudge */}
                    <td
                      className="py-2 px-3 font-mono-time text-zinc-400 text-[11px] whitespace-nowrap"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div className="space-y-1">
                        <div className="flex items-center gap-1">
                          <span className="text-zinc-500 text-[9px]">IN:</span>
                          <span>{formatTimecode(seg.start_time)}</span>
                          <div className="opacity-0 group-hover:opacity-100 flex items-center ml-1">
                            <button
                              onClick={() => handleNudge(seg, 'start_time', -0.1)}
                              className="p-0.5 hover:text-white text-zinc-400"
                              title="-100ms"
                            >
                              <Minus className="w-2.5 h-2.5" />
                            </button>
                            <button
                              onClick={() => handleNudge(seg, 'start_time', 0.1)}
                              className="p-0.5 hover:text-white text-zinc-400"
                              title="+100ms"
                            >
                              <Plus className="w-2.5 h-2.5" />
                            </button>
                          </div>
                        </div>

                        <div className="flex items-center gap-1">
                          <span className="text-zinc-500 text-[9px]">OUT:</span>
                          <span>{formatTimecode(seg.end_time)}</span>
                          <div className="opacity-0 group-hover:opacity-100 flex items-center ml-1">
                            <button
                              onClick={() => handleNudge(seg, 'end_time', -0.1)}
                              className="p-0.5 hover:text-white text-zinc-400"
                              title="-100ms"
                            >
                              <Minus className="w-2.5 h-2.5" />
                            </button>
                            <button
                              onClick={() => handleNudge(seg, 'end_time', 0.1)}
                              className="p-0.5 hover:text-white text-zinc-400"
                              title="+100ms"
                            >
                              <Plus className="w-2.5 h-2.5" />
                            </button>
                          </div>
                        </div>
                      </div>
                    </td>

                    {/* Original Text (Editable) */}
                    <td className="py-2 px-3" onClick={(e) => e.stopPropagation()}>
                      <textarea
                        rows={2}
                        value={seg.original_text}
                        onChange={(e) => {
                          if (seg.id) updateSegment(seg.id, { original_text: e.target.value });
                        }}
                        className="w-full bg-transparent hover:bg-[#121214] focus:bg-[#121214] p-1.5 rounded border border-transparent hover:border-[#27272a] focus:border-indigo-500/60 text-zinc-200 focus:outline-none resize-none text-xs leading-relaxed transition-all"
                      />
                    </td>

                    {/* Translated Text (Editable with RTL Support) */}
                    <td className="py-2 px-3" onClick={(e) => e.stopPropagation()}>
                      <textarea
                        rows={2}
                        dir={isRtl ? 'rtl' : 'ltr'}
                        value={seg.translated_text || ''}
                        placeholder={activeJob?.enable_translation ? 'Translating...' : '—'}
                        onChange={(e) => {
                          if (seg.id) updateSegment(seg.id, { translated_text: e.target.value });
                        }}
                        className={`w-full bg-transparent hover:bg-[#121214] focus:bg-[#121214] p-1.5 rounded border border-transparent hover:border-[#27272a] focus:border-indigo-500/60 text-amber-300 focus:outline-none resize-none text-xs leading-relaxed transition-all font-rtl-hebrew`}
                      />
                    </td>

                    {/* CPS Badge */}
                    <td className="py-2 px-3 text-center select-none">
                      <CpsBadge cps={seg.cps} />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
