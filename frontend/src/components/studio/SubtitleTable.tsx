import React, { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { useJobStore } from '../../store/useJobStore';
import { isActiveStatus, isRtlLanguage } from '../../types';
import type { Gender, SubtitleSegment, ThinkingLevel } from '../../types';
import { SubtitleRow } from './SubtitleRow';
import { RowTranslateDialog } from './RowTranslateDialog';
import { findSegmentAt } from '../../utils/time';
import { useT } from '../../i18n';
import { Search, Replace, AlertTriangle, Timer, Loader2, Undo2, ShieldCheck, X } from 'lucide-react';
import { api } from '../../services/api';
import type { QAIssue } from '../../types';

/** Clicking a row starts playback this many seconds before the subtitle, to hear the lead-in */
const SEEK_LEAD_IN_S = 1.5;

export const SubtitleTable: React.FC = () => {
  const activeJob = useJobStore((s) => s.activeJob);
  const videoCurrentTime = useJobStore((s) => s.videoCurrentTime);
  const selectedSegmentIndex = useJobStore((s) => s.selectedSegmentIndex);
  const setSelectedSegmentIndex = useJobStore((s) => s.setSelectedSegmentIndex);
  const seekTo = useJobStore((s) => s.seekTo);
  const updateSegment = useJobStore((s) => s.updateSegment);
  const replaceAll = useJobStore((s) => s.replaceAll);
  const retimeActiveJob = useJobStore((s) => s.retimeActiveJob);
  const undoRetime = useJobStore((s) => s.undoRetime);
  const moveSegment = useJobStore((s) => s.moveSegment);
  const retranslate = useJobStore((s) => s.retranslateActiveJob);
  const canTranslate = useJobStore((s) => !!s.systemInfo?.is_gemini_configured || !!s.pipelineConfig.gemini_api_key_override);
  const timingBusy = useJobStore((s) => s.timingBusy);
  const timingNote = useJobStore((s) => s.timingNote);
  const timingUndo = useJobStore((s) => s.timingUndo);
  const maxCps = useJobStore((s) => s.systemInfo?.max_cps_warning ?? 20);
  const t = useT();

  const [searchTerm, setSearchTerm] = useState('');
  const [replaceTerm, setReplaceTerm] = useState('');
  const [targetColumn, setTargetColumn] = useState<'translated' | 'original'>('translated');
  const [autoScroll, setAutoScroll] = useState(true);
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [replaceResult, setReplaceResult] = useState<string | null>(null);

  const activeRowRef = useRef<HTMLTableRowElement | null>(null);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [lastChecked, setLastChecked] = useState<number | null>(null);
  const [rowsDialog, setRowsDialog] = useState(false);
  const [qaIssues, setQaIssues] = useState<QAIssue[] | null>(null);
  const [qaBusy, setQaBusy] = useState(false);
  const [qaError, setQaError] = useState<string | null>(null);
  const [dragId, setDragId] = useState<number | null>(null);
  const [overId, setOverId] = useState<number | null>(null);

  const segments = useMemo(
    () => [...(activeJob?.segments || [])].sort((a, b) => a.start_time - b.start_time),
    [activeJob?.segments]
  );
  const isRtl = isRtlLanguage(activeJob?.target_language);
  // Only promise "Translating…" while a translation is actually running
  const translationEnabled = !!activeJob?.enable_translation && isActiveStatus(activeJob?.status);

  const activeIndex = useMemo(() => {
    const i = findSegmentAt(segments, videoCurrentTime);
    return i >= 0 ? segments[i].index : null;
  }, [segments, videoCurrentTime]);

  const problemCount = useMemo(
    () => segments.filter((s) => s.translation_failed || (s.cps ?? 0) > maxCps).length,
    [segments, maxCps]
  );

  const filteredSegments = useMemo(() => {
    let list = segments;
    if (onlyProblems) list = list.filter((s) => s.translation_failed || (s.cps ?? 0) > maxCps);
    if (!searchTerm.trim()) return list;
    const term = searchTerm.toLowerCase();
    return list.filter(
      (s) => s.original_text.toLowerCase().includes(term) || s.translated_text?.toLowerCase().includes(term)
    );
  }, [segments, searchTerm, onlyProblems, maxCps]);

  // Moving rows only makes sense in the full, time-ordered list of a finished job
  const canDrag = !!activeJob && !isActiveStatus(activeJob.status) && !searchTerm.trim() && !onlyProblems && !timingBusy;
  const canRecheck = !!activeJob && !isActiveStatus(activeJob.status) && segments.length > 0;

  const handleDragStart = useCallback((id: number) => setDragId(id), []);
  const handleDragOver = useCallback((id: number) => setOverId((prev) => (prev === id ? prev : id)), []);
  const handleDragEnd = useCallback(() => {
    setDragId(null);
    setOverId(null);
  }, []);
  const handleDrop = useCallback(
    (targetId: number) => {
      const from = dragId;
      setDragId(null);
      setOverId(null);
      if (from === null || from === targetId) return;
      const position = segments.findIndex((s) => s.id === targetId);
      if (position >= 0) void moveSegment(from, position);
    },
    [dragId, segments, moveSegment]
  );
  const dragFromPos = dragId === null ? -1 : segments.findIndex((s) => s.id === dragId);

  const jobFinished = !!activeJob && !isActiveStatus(activeJob.status);
  const canSelect = jobFinished && canTranslate;
  // Selection belongs to the job being shown and goes away when a run starts
  useEffect(() => {
    setChecked(new Set()); // eslint-disable-line react-hooks/set-state-in-effect
    setLastChecked(null);
  }, [activeJob?.id, jobFinished]);

  const handleCheck = useCallback(
    (id: number, shift: boolean) => {
      setChecked((prev) => {
        const next = new Set(prev);
        if (shift && lastChecked !== null) {
          const a = segments.findIndex((s) => s.id === lastChecked);
          const b = segments.findIndex((s) => s.id === id);
          if (a >= 0 && b >= 0) {
            const [lo, hi] = a < b ? [a, b] : [b, a];
            for (let i = lo; i <= hi; i++) if (segments[i].id) next.add(segments[i].id!);
            return next;
          }
        }
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
      setLastChecked(id);
    },
    [lastChecked, segments]
  );

  const translateRows = (opts: {
    speaker?: Gender | null;
    addressee?: Gender | null;
    hint?: string;
    remember?: boolean;
    model?: string;
    thinking?: ThinkingLevel;
  }) => {
    setRowsDialog(false);
    void retranslate({
      segment_ids: [...checked],
      speaker_gender: opts.speaker ?? null,
      addressee_gender: opts.addressee ?? null,
      hint: opts.hint || undefined,
      remember_hint: !!opts.remember,
      gemini_model: opts.model || undefined,
      thinking: opts.thinking,
    });
    setChecked(new Set());
  };

  const runQualityCheck = async () => {
    if (!activeJob) return;
    setQaBusy(true);
    setQaError(null);
    try {
      setQaIssues(await api.qualityCheck(activeJob.id));
    } catch (e) {
      setQaError(e instanceof Error ? e.message : String(e));
    } finally {
      setQaBusy(false);
    }
  };

  const selectIssueRows = () => {
    if (!qaIssues) return;
    setChecked(new Set(qaIssues.map((i) => i.segment_id)));
  };

  const handleReplaceAll = async () => {
    if (!searchTerm.trim()) return;
    const n = await replaceAll(searchTerm, replaceTerm, targetColumn);
    setReplaceResult(n ? t('table.replaced', { count: n }) : t('table.noMatches'));
    setTimeout(() => setReplaceResult(null), 3000);
  };

  const onSelect = useCallback(
    (seg: SubtitleSegment) => {
      setSelectedSegmentIndex(seg.index);
      seekTo(seg.start_time - SEEK_LEAD_IN_S, true); // seekTo clamps at 0
    },
    [setSelectedSegmentIndex, seekTo]
  );

  // Follow playback — only when the active subtitle changes, not on every time tick
  useEffect(() => {
    if (autoScroll && activeIndex !== null && activeRowRef.current) {
      activeRowRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [activeIndex, autoScroll]);

  const isLive = segments.length > 0 && segments.some((s) => !s.id);

  return (
    <div className="flex flex-col h-full bg-surface border border-line rounded-xl overflow-hidden shadow-xl">
      <div className="p-3 bg-sunken border-b border-line space-y-2">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="w-3.5 h-3.5 absolute start-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              placeholder={t('table.search')}
              dir="auto"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-surface border border-line rounded-lg ps-8 pe-3 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div className="relative flex-1">
            <Replace className="w-3.5 h-3.5 absolute start-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              placeholder={t('table.replace')}
              dir="auto"
              value={replaceTerm}
              onChange={(e) => setReplaceTerm(e.target.value)}
              className="w-full bg-surface border border-line rounded-lg ps-8 pe-3 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <select
            value={targetColumn}
            onChange={(e) => setTargetColumn(e.target.value as 'translated' | 'original')}
            className="bg-surface border border-line rounded-lg px-2.5 py-1.5 text-xs text-zinc-300 focus:outline-none focus:border-indigo-500"
          >
            <option value="translated">{t('table.inTranslated')}</option>
            <option value="original">{t('table.inOriginal')}</option>
          </select>
          <button
            onClick={handleReplaceAll}
            disabled={!searchTerm.trim() || isLive}
            className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-xs font-semibold text-zinc-200 transition-colors cursor-pointer"
          >
            {t('table.replaceAll')}
          </button>
        </div>

        <div className="flex items-center justify-between text-[11px] text-zinc-400 px-1">
          <span>
            {replaceResult ? (
              <span className="text-indigo-300">{replaceResult}</span>
            ) : (
              <>
                {t('table.showing', { shown: filteredSegments.length, total: segments.length })}
                {isLive && <span className="text-zinc-500"> · {t('table.livePreview')}</span>}
              </>
            )}
          </span>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-1.5 cursor-pointer select-none" title={t('table.onlyProblemsTitle')}>
              <input
                type="checkbox"
                checked={onlyProblems}
                onChange={(e) => setOnlyProblems(e.target.checked)}
                className="w-3.5 h-3.5 rounded border-zinc-700 text-indigo-600 bg-zinc-800"
              />
              <AlertTriangle className="w-3 h-3 text-amber-400" />
              <span>{t('table.onlyProblems', { count: problemCount })}</span>
            </label>
            {canRecheck && (
              <span className="flex items-center gap-2">
                {timingNote !== null && !timingBusy && (
                  <span className="text-indigo-300">
                    {Number(timingNote) > 0 ? t('table.timingFixed', { count: Number(timingNote) }) : t('table.timingOk')}
                  </span>
                )}
                {timingUndo && !timingBusy && (
                  <button
                    onClick={() => void undoRetime()}
                    className="flex items-center gap-1 text-zinc-300 hover:text-white"
                  >
                    <Undo2 className="w-3 h-3" />
                    {t('table.undo')}
                  </button>
                )}
                <button
                  onClick={() => void retimeActiveJob()}
                  disabled={timingBusy}
                  title={t('table.recheckTimingTitle')}
                  className="flex items-center gap-1.5 px-2 py-1 rounded border border-line text-zinc-300 hover:text-white hover:border-indigo-500/60 disabled:opacity-60 disabled:cursor-wait"
                >
                  {timingBusy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Timer className="w-3 h-3" />}
                  {timingBusy ? t('table.recheckingTiming') : t('table.recheckTiming')}
                </button>
              </span>
            )}
            {canRecheck && (
              <button
                onClick={() => void runQualityCheck()}
                disabled={qaBusy}
                title={t('table.qaTitle')}
                data-testid="qa-check"
                className="flex items-center gap-1.5 px-2 py-1 rounded border border-line text-zinc-300 hover:text-white hover:border-indigo-500/60 disabled:opacity-60 disabled:cursor-wait"
              >
                {qaBusy ? <Loader2 className="w-3 h-3 animate-spin" /> : <ShieldCheck className="w-3 h-3" />}
                {t('table.qa')}
              </button>
            )}
            <label className="flex items-center gap-1.5 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="w-3.5 h-3.5 rounded border-zinc-700 text-indigo-600 bg-zinc-800"
              />
              <span>{t('table.autoScroll')}</span>
            </label>
          </div>
        </div>
      </div>

      {(qaIssues !== null || qaError) && (
        <div className="px-4 py-2 border-y border-line bg-zinc-900/60 text-xs" data-testid="qa-panel">
          <div className="flex items-center gap-3">
            <span className={qaError ? 'text-red-300' : qaIssues && qaIssues.length ? 'text-amber-300' : 'text-emerald-300'}>
              {qaError ?? (qaIssues && qaIssues.length ? t('table.qaFound', { count: qaIssues.length }) : t('table.qaClean'))}
            </span>
            {canSelect && qaIssues && qaIssues.length > 0 && (
              <button onClick={selectIssueRows} className="text-indigo-300 hover:text-white" data-testid="qa-select">
                {t('table.qaSelect')}
              </button>
            )}
            <button onClick={() => { setQaIssues(null); setQaError(null); }} className="ml-auto text-zinc-500 hover:text-white">
              <X className="w-3 h-3" />
            </button>
          </div>
          {qaIssues && qaIssues.length > 0 && (
            <ul className="mt-1 max-h-32 overflow-y-auto space-y-0.5">
              {qaIssues.map((i, k) => (
                <li key={`${i.segment_id}-${k}`}>
                  <button
                    onClick={() => {
                      const seg = segments.find((s) => s.id === i.segment_id);
                      if (seg) onSelect(seg);
                    }}
                    className="text-zinc-300 hover:text-white text-start"
                  >
                    <span className="text-zinc-500">#{i.index}</span> {i.message}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {checked.size > 0 && canSelect && (
        <div
          className="flex flex-wrap items-center gap-2 px-4 py-2 bg-indigo-950/40 border-y border-indigo-500/30 text-xs"
          data-testid="rows-bar"
        >
          <span className="text-indigo-200 font-medium">{t('table.selected', { count: checked.size })}</span>
          <button
            onClick={() => translateRows({ addressee: 'female' })}
            className="px-2 py-1 rounded border border-line text-zinc-200 hover:border-indigo-500/60"
            data-testid="rows-to-woman"
          >
            {t('table.toWoman')}
          </button>
          <button
            onClick={() => translateRows({ addressee: 'male' })}
            className="px-2 py-1 rounded border border-line text-zinc-200 hover:border-indigo-500/60"
          >
            {t('table.toMan')}
          </button>
          <button
            onClick={() => translateRows({ speaker: 'female' })}
            className="px-2 py-1 rounded border border-line text-zinc-200 hover:border-indigo-500/60"
          >
            {t('table.speakerWoman')}
          </button>
          <button
            onClick={() => translateRows({ addressee: 'group' })}
            className="px-2 py-1 rounded border border-line text-zinc-200 hover:border-indigo-500/60"
          >
            {t('table.toGroup')}
          </button>
          <button
            onClick={() => setRowsDialog(true)}
            data-testid="rows-note"
            className="px-2 py-1 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-500/10"
          >
            {t('table.retranslateRows')}
          </button>
          <button onClick={() => setChecked(new Set())} className="ms-auto text-zinc-400 hover:text-white">
            {t('table.clearSelection')}
          </button>
        </div>
      )}
      {rowsDialog && (
        <RowTranslateDialog
          count={checked.size}
          onCancel={() => setRowsDialog(false)}
          defaultModel={activeJob?.gemini_model}
          onDetect={
            activeJob && canRecheck
              ? (model, thinking) =>
                  api.detectSpeakers(
                    activeJob.id,
                    [...checked],
                    useJobStore.getState().pipelineConfig.gemini_api_key_override,
                    model,
                    thinking
                  )
              : undefined
          }
          onSubmit={(o) => translateRows(o)}
        />
      )}

      <div className="flex-1 overflow-y-auto">
        <table className="w-full text-start border-collapse text-xs">
          <thead className="bg-sunken text-zinc-400 sticky top-0 z-10 border-b border-line uppercase font-semibold text-[10px] tracking-wider select-none">
            <tr>
              <th className="py-2.5 px-3 w-12 text-center">#</th>
              <th className="py-2.5 px-3 w-48 text-start">{t('table.colTimecode')}</th>
              <th className="py-2.5 px-3 w-1/2 text-start">{t('table.colOriginal')}</th>
              <th className="py-2.5 px-3 w-1/2 text-start">{t('table.colTranslated')}</th>
              <th className="py-2.5 px-3 w-20 text-center">{t('table.colCps')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/60 font-sans">
            {filteredSegments.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-12 text-center text-zinc-500">
                  {segments.length === 0 ? t('table.empty') : t('table.noFilterMatch')}
                </td>
              </tr>
            ) : (
              filteredSegments.map((seg) => {
                const isActive = seg.index === activeIndex;
                return (
                  <SubtitleRow
                    key={seg.id ?? `live-${seg.index}`}
                    seg={seg}
                    isActive={isActive}
                    isSelected={selectedSegmentIndex === seg.index}
                    isRtl={isRtl}
                    translationEnabled={translationEnabled}
                    maxCps={maxCps}
                    onSelect={onSelect}
                    onSave={updateSegment}
                    rowRef={isActive ? activeRowRef : undefined}
                    checked={checked.has(seg.id ?? -1)}
                    onCheck={canSelect ? handleCheck : undefined}
                    dnd={
                      canDrag && seg.id
                        ? {
                            isDragging: dragId === seg.id,
                            drop:
                              dragId !== null && overId === seg.id && dragId !== seg.id
                                ? seg.index > (segments[dragFromPos]?.index ?? 0)
                                  ? 'after'
                                  : 'before'
                                : null,
                            onDragStart: handleDragStart,
                            onDragOver: handleDragOver,
                            onDrop: handleDrop,
                            onDragEnd: handleDragEnd,
                          }
                        : undefined
                    }
                  />
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
