import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Crosshair, Loader2, Plus } from 'lucide-react';
import { useJobStore } from '../../store/useJobStore';
import { useT } from '../../i18n';
import { formatTimecode, parseTimecode } from '../../utils/time';
import { isRtlLanguage } from '../../types';
import type { InsertDraft, SubtitleSegment } from '../../types';

const GAP = 0.04; // kept between neighbouring lines (same as the timing tools)
const DEFAULT_LENGTH = 2.0;
const MIN_LENGTH = 0.3;

interface Issue {
  level: 'error' | 'warn';
  text: string;
  fix?: { label: string; apply: () => void };
}

const round3 = (x: number) => Math.round(x * 1000) / 1000;

/** First guess for the new line: in the gap after `afterId`, or at the playhead. */
function initialRange(draft: InsertDraft, sorted: SubtitleSegment[], duration: number | null): [number, number] {
  let start = Math.max(0, draft.time);
  if (draft.afterId !== undefined) {
    const k = sorted.findIndex((s) => s.id === draft.afterId);
    if (k >= 0) {
      const gapStart = sorted[k].end_time + GAP;
      const gapEnd = sorted[k + 1] ? sorted[k + 1].start_time - GAP : duration ?? gapStart + 10;
      start = draft.time >= gapStart && draft.time <= gapEnd - MIN_LENGTH ? draft.time : gapStart;
    }
  } else {
    const inside = sorted.find((s) => start >= s.start_time && start < s.end_time);
    if (inside) start = inside.end_time + GAP; // the playhead is on a line: start right after it
  }
  const next = sorted.find((s) => s.start_time >= start - 0.0005);
  let end = start + DEFAULT_LENGTH;
  if (next) end = Math.min(end, next.start_time - GAP);
  if (duration) end = Math.min(end, duration);
  if (end - start < MIN_LENGTH) end = start + MIN_LENGTH;
  return [round3(start), round3(end)];
}

/** Add a subtitle line by hand between two existing lines. */
export const InsertSegmentDialog: React.FC<{ draft: InsertDraft }> = ({ draft }) => {
  const t = useT();
  const activeJob = useJobStore((s) => s.activeJob);
  const playhead = useJobStore((s) => s.videoCurrentTime);
  const closeInsert = useJobStore((s) => s.closeInsert);
  const addSegment = useJobStore((s) => s.addSegment);

  const sorted = useMemo(
    () => [...(activeJob?.segments || [])].sort((a, b) => a.start_time - b.start_time || a.end_time - b.end_time),
    [activeJob?.segments]
  );
  const duration = activeJob?.video_duration ?? null;
  const hasTranslation = !!activeJob && activeJob.segments.some((s) => s.translated_text);
  const translationRtl = isRtlLanguage(activeJob?.target_language);

  const [onScreen, setOnScreen] = useState(false);
  const [initStart, initEnd] = useMemo(() => initialRange(draft, sorted, duration), [draft]); // eslint-disable-line react-hooks/exhaustive-deps
  const [startText, setStartText] = useState(formatTimecode(initStart));
  const [endText, setEndText] = useState(formatTimecode(initEnd));
  const [original, setOriginal] = useState('');
  const [translated, setTranslated] = useState('');
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const firstText = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    firstText.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && closeInsert();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [closeInsert]);

  const relevantSorted = useMemo(
    () => sorted.filter((s) => (onScreen ? s.segment_type === 'ON_SCREEN' : s.segment_type !== 'ON_SCREEN')),
    [sorted, onScreen]
  );

  const start = parseTimecode(startText);
  const end = parseTimecode(endText);
  const setStart = (x: number) => setStartText(formatTimecode(round3(Math.max(0, x))));
  const setEnd = (x: number) => setEndText(formatTimecode(round3(Math.max(0, x))));

  // Neighbours of the new line (by its start; the opening guess while the start is invalid)
  const anchor = start ?? initStart;
  const nextPos = relevantSorted.findIndex((s) => s.start_time >= anchor - 0.0005);
  const next = nextPos >= 0 ? relevantSorted[nextPos] : undefined;
  const before = nextPos >= 0 ? relevantSorted.slice(0, nextPos) : relevantSorted;
  const prev = before.length ? before[before.length - 1] : undefined;
  const gapFrom = prev ? prev.end_time + GAP : 0;
  const gapTo = next ? next.start_time - GAP : duration ?? null;

  const issues: Issue[] = [];
  if (start !== null && end !== null) {
    if (end <= start) issues.push({ level: 'error', text: t('table.insert.endBeforeStart') });
    if (duration && start >= duration)
      issues.push({ level: 'error', text: t('table.insert.afterVideo', { time: formatTimecode(duration) }) });
    if (prev && start < prev.end_time - 0.0005)
      issues.push({
        level: 'error',
        text: t('table.insert.overlapPrev', { index: prev.index, time: formatTimecode(prev.end_time) }),
        fix: { label: t('table.insert.fixStart'), apply: () => setStart(prev.end_time + GAP) },
      });
    if (next && end > next.start_time + 0.0005)
      issues.push({
        level: 'error',
        text: t('table.insert.overlapNext', { index: next.index, time: formatTimecode(next.start_time) }),
        fix: { label: t('table.insert.fixEnd'), apply: () => setEnd(next.start_time - GAP) },
      });
    const length = end - start;
    if (end > start && length < 0.7) issues.push({ level: 'warn', text: t('table.insert.short', { value: length.toFixed(2) }) });
    if (length > 8) issues.push({ level: 'warn', text: t('table.insert.long', { value: length.toFixed(1) }) });
  }
  const noText = !original.trim() && !translated.trim();
  const blocked = start === null || end === null || noText || issues.some((i) => i.level === 'error');

  const fitIntoGap = () => {
    const from = Math.max(gapFrom, start !== null && start >= gapFrom ? start : gapFrom);
    let to = from + DEFAULT_LENGTH;
    if (gapTo !== null) to = Math.min(to, gapTo);
    setStart(from);
    setEnd(Math.max(to, from + MIN_LENGTH));
  };

  const submit = async () => {
    if (blocked || saving || start === null || end === null) return;
    setSaving(true);
    setServerError(null);
    const error = await addSegment({
      start_time: round3(start),
      end_time: round3(end),
      original_text: original.trim(),
      translated_text: translated.trim() || null,
      segment_type: onScreen ? 'ON_SCREEN' : 'DIALOGUE',
    });
    setSaving(false);
    if (error) setServerError(error);
  };

  const position = !prev && !next
    ? t('table.insert.first')
    : !prev
    ? t('table.insert.before', { b: next!.index })
    : !next
    ? t('table.insert.after', { a: prev.index })
    : t('table.insert.between', { a: prev.index, b: next.index });

  const neighbour = (seg: SubtitleSegment | undefined, label: string) =>
    seg ? (
      <div className="flex gap-3 px-3 py-1.5 text-[11px] text-zinc-500" data-testid={`insert-${label}`}>
        <span className="font-mono-time whitespace-nowrap" dir="ltr">
          #{seg.index} · {formatTimecode(seg.start_time)} → {formatTimecode(seg.end_time)}
        </span>
        <span className="truncate" dir="auto">
          {(seg.translated_text || seg.original_text).replace(/\n/g, ' / ')}
        </span>
      </div>
    ) : null;

  const timeField = (label: string, text: string, setText: (v: string) => void, value: number | null, testid: string) => (
    <label className="block space-y-1">
      <span className="text-[11px] text-zinc-400 font-medium">{label}</span>
      <div className="flex items-center gap-1.5" dir="ltr">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => value !== null && setText(formatTimecode(value))}
          onKeyDown={(e) => e.key === 'Enter' && void submit()}
          placeholder="00:00:00,000"
          inputMode="numeric"
          spellCheck={false}
          data-testid={testid}
          aria-invalid={value === null}
          className={`w-36 bg-sunken border rounded-lg px-2.5 py-1.5 text-sm font-mono-time text-zinc-100 focus:outline-none ${
            value === null ? 'border-rose-500/70 focus:border-rose-400' : 'border-line focus:border-indigo-500'
          }`}
        />
        <button
          type="button"
          onClick={() => setText(formatTimecode(round3(playhead)))}
          title={t('table.insert.playheadTitle', { time: formatTimecode(playhead) })}
          data-testid={`${testid}-playhead`}
          className="flex items-center gap-1 px-2 py-1.5 rounded-lg border border-line text-[11px] text-zinc-300 hover:text-white hover:border-indigo-500/60 cursor-pointer"
        >
          <Crosshair className="w-3 h-3" />
          {t('table.insert.playhead')}
        </button>
      </div>
      {value === null && <span className="block text-[11px] text-rose-300">{t('table.insert.invalid')}</span>}
    </label>
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      onClick={closeInsert}
    >
      <div
        className="w-full max-w-lg bg-surface border border-line rounded-xl p-5 space-y-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        data-testid="insert-dialog"
      >
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-sm font-semibold text-zinc-100">{t('table.insert.title')}</h2>
          <span className="text-[11px] text-indigo-300" data-testid="insert-position">{position}</span>
        </div>

        {/* Where the line goes: previous line, the new one, next line */}
        <div className="rounded-lg border border-line bg-sunken/60 divide-y divide-line">
          {neighbour(prev, 'prev')}
          <div className="p-3 space-y-3 bg-indigo-500/5">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {timeField(t('table.insert.start'), startText, setStartText, start, 'insert-start')}
              {timeField(t('table.insert.end'), endText, setEndText, end, 'insert-end')}
            </div>
            <p className="text-[11px] text-zinc-500 leading-relaxed">{t('table.insert.format')}</p>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
              {start !== null && end !== null && end > start && (
                <span className="text-zinc-300" data-testid="insert-duration">
                  {t('table.insert.duration', { value: (end - start).toFixed(2) })}
                </span>
              )}
              <span className="text-zinc-500" dir="auto">
                {gapTo !== null && gapTo > gapFrom
                  ? t('table.insert.gap', {
                      from: formatTimecode(gapFrom),
                      to: formatTimecode(gapTo),
                      value: (gapTo - gapFrom).toFixed(1),
                    })
                  : t('table.insert.noGap')}
              </span>
              {gapTo !== null && gapTo > gapFrom && (
                <button type="button" onClick={fitIntoGap} className="text-indigo-300 hover:text-white underline cursor-pointer" data-testid="insert-fit">
                  {t('table.insert.fit')}
                </button>
              )}
            </div>
          </div>
          {neighbour(next, 'next')}
        </div>

        {issues.length > 0 && (
          <ul className="space-y-1" data-testid="insert-issues">
            {issues.map((issue, k) => (
              <li
                key={k}
                className={`flex items-start gap-1.5 text-[11px] ${issue.level === 'error' ? 'text-rose-300' : 'text-amber-300'}`}
              >
                <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <span>
                  {issue.text}
                  {issue.fix && (
                    <button type="button" onClick={issue.fix.apply} className="ms-2 underline text-indigo-300 hover:text-white cursor-pointer">
                      {issue.fix.label}
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}

        <div className={`grid gap-3 ${hasTranslation ? 'grid-cols-1 sm:grid-cols-2' : 'grid-cols-1'}`}>
          <label className="block space-y-1">
            <span className="text-[11px] text-zinc-400 font-medium">{t('table.colOriginal')}</span>
            <textarea
              ref={firstText}
              rows={2}
              dir="auto"
              value={original}
              onChange={(e) => setOriginal(e.target.value)}
              onKeyDown={(e) => (e.ctrlKey || e.metaKey) && e.key === 'Enter' && void submit()}
              placeholder={t('table.insert.textPlaceholder')}
              data-testid="insert-original"
              className="w-full bg-sunken border border-line rounded-lg p-2 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500 resize-none"
            />
          </label>
          {hasTranslation && (
            <label className="block space-y-1">
              <span className="text-[11px] text-zinc-400 font-medium">{t('table.colTranslated')}</span>
              <textarea
                rows={2}
                dir={translationRtl ? 'rtl' : 'auto'}
                value={translated}
                onChange={(e) => setTranslated(e.target.value)}
                onKeyDown={(e) => (e.ctrlKey || e.metaKey) && e.key === 'Enter' && void submit()}
                data-testid="insert-translated"
                className="w-full bg-sunken border border-line rounded-lg p-2 text-xs text-amber-300 font-rtl-hebrew placeholder-zinc-600 focus:outline-none focus:border-indigo-500 resize-none"
              />
            </label>
          )}
        </div>

        <label className="flex items-center gap-2 text-[11px] text-zinc-300 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={onScreen}
            onChange={(e) => setOnScreen(e.target.checked)}
            className="w-3.5 h-3.5 rounded border-zinc-700 text-indigo-600 bg-zinc-800"
          />
          {t('table.insert.onScreen')}
        </label>

        {serverError && (
          <p className="text-[11px] text-rose-300" data-testid="insert-error">
            {serverError}
          </p>
        )}

        <div className="flex items-center justify-between gap-3 pt-1">
          <span className="text-[11px] text-zinc-500">{noText ? t('table.insert.needText') : t('table.insert.keys')}</span>
          <div className="flex gap-2">
            <button onClick={closeInsert} className="px-3 py-1.5 rounded-lg text-xs text-zinc-300 hover:text-white cursor-pointer">
              {t('table.insert.cancel')}
            </button>
            <button
              onClick={() => void submit()}
              disabled={blocked || saving}
              data-testid="insert-submit"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white text-xs font-semibold cursor-pointer disabled:cursor-not-allowed"
            >
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : blocked ? <Plus className="w-3.5 h-3.5" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
              {saving ? t('table.insert.adding') : t('table.insert.add')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
