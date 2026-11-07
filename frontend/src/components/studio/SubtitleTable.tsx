import React, { useState, useRef, useEffect, useMemo, useCallback, useDeferredValue } from 'react';
import { useJobStore } from '../../store/useJobStore';
import { isActiveStatus, isRtlLanguage } from '../../types';
import type { Gender, ProblemFilter, SubtitleSegment, ThinkingLevel } from '../../types';
import { SubtitleRow } from './SubtitleRow';
import { RowTranslateDialog } from './RowTranslateDialog';
import { InsertSegmentDialog } from './InsertSegmentDialog';
import { findSegmentAt, parseTimecode } from '../../utils/time';
import { useT } from '../../i18n';
import {
  Search,
  Replace,
  AlertTriangle,
  Timer,
  Loader2,
  Undo2,
  ShieldCheck,
  X,
  Plus,
  Tag,
  Trash2,
  Regex,
  ChevronsDown,
  ChevronsUp,
  ArrowUpToLine,
  ArrowDownToLine,
} from 'lucide-react';
import { api } from '../../services/api';
import type { QAIssue } from '../../types';

/** Clicking a row starts playback this many seconds before the subtitle, to hear the lead-in */
const SEEK_LEAD_IN_S = 1.5;

/** Matches 3+ consecutive ASCII Latin letters left inside an RTL translation */
const LATIN_WORD_RE = /[A-Za-z]{3,}/;

/** Virtual table constants: only rows near the viewport are mounted in the DOM */
const ESTIMATED_ROW_HEIGHT = 64;
const OVERSCAN_ROWS = 12;
const VIRTUALIZE_THRESHOLD = 80;

export const SubtitleTable: React.FC = () => {
  const activeJob = useJobStore((s) => s.activeJob);
  const selectedSegmentIndex = useJobStore((s) => s.selectedSegmentIndex);
  const setSelectedSegmentIndex = useJobStore((s) => s.setSelectedSegmentIndex);
  const problemFilter = useJobStore((s) => s.problemFilter);
  const setProblemFilter = useJobStore((s) => s.setProblemFilter);
  const seekTo = useJobStore((s) => s.seekTo);
  const updateSegment = useJobStore((s) => s.updateSegment);
  const replaceAll = useJobStore((s) => s.replaceAll);
  const deleteSegment = useJobStore((s) => s.deleteSegment);
  const deleteSegments = useJobStore((s) => s.deleteSegments);
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
  const deferredSearchTerm = useDeferredValue(searchTerm);
  const [replaceTerm, setReplaceTerm] = useState('');
  const [targetColumn, setTargetColumn] = useState<'translated' | 'original'>('translated');
  const [useRegex, setUseRegex] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [tagFilter, setTagFilter] = useState<string>('ALL');
  const [replaceResult, setReplaceResult] = useState<string | null>(null);
  const [jumpInput, setJumpInput] = useState('');

  const activeRowRef = useRef<HTMLTableRowElement | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(640);
  const scrollRafRef = useRef<number | null>(null);

  const [checked, setChecked] = useState<Set<number>>(new Set());
  const lastCheckedRef = useRef<number | null>(null);
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
  const jobFinished = !!activeJob && !isActiveStatus(activeJob.status);
  // Only promise "Translating…" while a translation is actually running
  const translationEnabled = !!activeJob?.enable_translation && isActiveStatus(activeJob?.status);
  const hasTranslationExpectation =
    !!activeJob?.enable_translation || segments.some((s) => !!s.translated_text || !!s.translation_failed);

  // Subscribe only to the primitive active subtitle index (not raw videoCurrentTime ticks)
  // so the table only re-renders when the active subtitle actually changes (~every 3-4s).
  const activeIndex = useJobStore(
    useCallback(
      (s) => {
        const i = findSegmentAt(segments, s.videoCurrentTime);
        return i >= 0 ? segments[i].index : null;
      },
      [segments]
    )
  );

  // A synced file also flags lines that were placed with low confidence
  const isSyncJob = activeJob?.job_kind === 'sync';

  const isUntranslated = useCallback(
    (s: SubtitleSegment) =>
      !!s.translation_failed || (hasTranslationExpectation && !translationEnabled && !s.translated_text?.trim()),
    [hasTranslationExpectation, translationEnabled]
  );

  const hasLatinLeftover = useCallback(
    (s: SubtitleSegment) => isRtl && !!s.translated_text && LATIN_WORD_RE.test(s.translated_text),
    [isRtl]
  );

  const isHighCps = useCallback(
    (s: SubtitleSegment) => (s.cps ?? 0) > maxCps || (isSyncJob && (s.confidence ?? 1) < 0.5),
    [maxCps, isSyncJob]
  );

  const isAnyProblem = useCallback(
    (s: SubtitleSegment) => isUntranslated(s) || hasLatinLeftover(s) || isHighCps(s),
    [isUntranslated, hasLatinLeftover, isHighCps]
  );

  const problemStats = useMemo(() => {
    let all = 0;
    let untranslated = 0;
    let latin = 0;
    let highCps = 0;
    for (const s of segments) {
      const u = isUntranslated(s);
      const l = hasLatinLeftover(s);
      const c = isHighCps(s);
      if (u) untranslated++;
      if (l) latin++;
      if (c) highCps++;
      if (u || l || c) all++;
    }
    return { all, untranslated, latin, highCps };
  }, [segments, isUntranslated, hasLatinLeftover, isHighCps]);

  const tagCounts = useMemo(() => {
    let onscreen = 0;
    let comms = 0;
    let foreign = 0;
    let anyTagged = 0;
    for (const s of segments) {
      if (s.segment_type && s.segment_type !== 'DIALOGUE') {
        anyTagged++;
        if (s.segment_type === 'ON_SCREEN') onscreen++;
        else if (s.segment_type === 'COMMS') comms++;
        else if (s.segment_type === 'FOREIGN') foreign++;
      }
    }
    return { onscreen, comms, foreign, anyTagged };
  }, [segments]);

  const { regex: searchRegex, error: regexError } = useMemo(() => {
    if (!useRegex || !deferredSearchTerm) return { regex: null, error: null };
    try {
      const match = deferredSearchTerm.match(/^\/(.*)\/([a-z]*)$/s);
      if (match) {
        // Strip 'g' flag so RegExp.prototype.test() remains stateless across segments
        const flags = match[2] ? match[2].replace(/g/g, '') : 'i';
        return { regex: new RegExp(match[1], flags), error: null };
      }
      return { regex: new RegExp(deferredSearchTerm, 'i'), error: null };
    } catch (err) {
      return { regex: null, error: err instanceof Error ? err.message : String(err) };
    }
  }, [useRegex, deferredSearchTerm]);

  const filteredSegments = useMemo(() => {
    let list = segments;
    if (problemFilter === 'ALL_PROBLEMS') list = list.filter(isAnyProblem);
    else if (problemFilter === 'UNTRANSLATED') list = list.filter(isUntranslated);
    else if (problemFilter === 'LATIN_LEFTOVER') list = list.filter(hasLatinLeftover);
    else if (problemFilter === 'HIGH_CPS') list = list.filter(isHighCps);

    if (tagFilter !== 'ALL') {
      if (tagFilter === 'TAGGED') {
        list = list.filter((s) => s.segment_type && s.segment_type !== 'DIALOGUE');
      } else {
        list = list.filter((s) => s.segment_type === tagFilter);
      }
    }
    if (!deferredSearchTerm) return list;
    if (useRegex) {
      if (regexError || !searchRegex) return [];
      return list.filter(
        (s) => searchRegex.test(s.original_text) || (s.translated_text ? searchRegex.test(s.translated_text) : false)
      );
    }
    if (!deferredSearchTerm.trim()) return list;
    const term = deferredSearchTerm.toLowerCase();
    return list.filter(
      (s) => s.original_text.toLowerCase().includes(term) || s.translated_text?.toLowerCase().includes(term)
    );
  }, [
    segments,
    deferredSearchTerm,
    useRegex,
    searchRegex,
    regexError,
    problemFilter,
    isAnyProblem,
    isUntranslated,
    hasLatinLeftover,
    isHighCps,
    tagFilter,
  ]);

  const filteredSegmentsRef = useRef<SubtitleSegment[]>(filteredSegments);
  useEffect(() => {
    filteredSegmentsRef.current = filteredSegments;
  }, [filteredSegments]);

  const onlyProblems = problemFilter !== 'NONE';
  // Moving rows only makes sense in the full, time-ordered list of a finished job
  const canDrag = !!activeJob && jobFinished && !searchTerm && !onlyProblems && tagFilter === 'ALL' && !timingBusy;
  const canRecheck = !!activeJob && jobFinished && segments.length > 0;

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

  const isLive = segments.length > 0 && segments.some((s) => !s.id);
  const canSelect = jobFinished && !isLive;
  // Selection belongs to the job being shown and goes away when a run starts
  useEffect(() => {
    setChecked(new Set()); // eslint-disable-line react-hooks/set-state-in-effect
    lastCheckedRef.current = null;
  }, [activeJob?.id, jobFinished]);

  const selectableFilteredIds = useMemo(
    () => filteredSegments.map((s) => s.id).filter((id): id is number => typeof id === 'number'),
    [filteredSegments]
  );
  const allFilteredChecked =
    selectableFilteredIds.length > 0 && selectableFilteredIds.every((id) => checked.has(id));
  const someFilteredChecked =
    selectableFilteredIds.length > 0 && !allFilteredChecked && selectableFilteredIds.some((id) => checked.has(id));

  const selectAllRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someFilteredChecked;
    }
  }, [someFilteredChecked]);

  const toggleSelectAllFiltered = useCallback(() => {
    setChecked((prev) => {
      const next = new Set(prev);
      const allIn = selectableFilteredIds.length > 0 && selectableFilteredIds.every((id) => next.has(id));
      if (allIn) {
        for (const id of selectableFilteredIds) next.delete(id);
      } else {
        for (const id of selectableFilteredIds) next.add(id);
      }
      return next;
    });
  }, [selectableFilteredIds]);

  // Stable reference (never invalidates React.memo across 1000+ rows when checking a box)
  const handleCheck = useCallback((id: number, shift: boolean) => {
    const list = filteredSegmentsRef.current;
    const prevLast = lastCheckedRef.current;
    setChecked((prev) => {
      const next = new Set(prev);
      if (shift && prevLast !== null) {
        const a = list.findIndex((s) => s.id === prevLast);
        const b = list.findIndex((s) => s.id === id);
        if (a >= 0 && b >= 0) {
          const [lo, hi] = a < b ? [a, b] : [b, a];
          for (let i = lo; i <= hi; i++) {
            if (list[i].id) next.add(list[i].id!);
          }
          return next;
        }
      }
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    lastCheckedRef.current = id;
  }, []);

  // Select from `id` through the end of the currently displayed list in one click
  const handleSelectToEnd = useCallback((id: number) => {
    const list = filteredSegmentsRef.current;
    const startIdx = list.findIndex((s) => s.id === id);
    if (startIdx < 0) return;
    setChecked((prev) => {
      const next = new Set(prev);
      for (let i = startIdx; i < list.length; i++) {
        if (list[i].id) next.add(list[i].id!);
      }
      return next;
    });
    lastCheckedRef.current = id;
  }, []);

  // Select from the beginning of the displayed list up to `id` in one click
  const handleSelectFromStart = useCallback((id: number) => {
    const list = filteredSegmentsRef.current;
    const endIdx = list.findIndex((s) => s.id === id);
    if (endIdx < 0) return;
    setChecked((prev) => {
      const next = new Set(prev);
      for (let i = 0; i <= endIdx; i++) {
        if (list[i].id) next.add(list[i].id!);
      }
      return next;
    });
    lastCheckedRef.current = id;
  }, []);

  const checkedRangeBounds = useMemo(() => {
    if (checked.size === 0 || filteredSegments.length === 0) return null;
    let firstPos = -1;
    let lastPos = -1;
    for (let i = 0; i < filteredSegments.length; i++) {
      const id = filteredSegments[i].id;
      if (id && checked.has(id)) {
        if (firstPos === -1) firstPos = i;
        lastPos = i;
      }
    }
    if (firstPos === -1) return null;
    const firstSeg = filteredSegments[firstPos];
    const lastSeg = filteredSegments[lastPos];
    const toEndCount = filteredSegments.length - firstPos;
    const fromStartCount = lastPos + 1;
    return {
      firstSeg,
      lastSeg,
      toEndCount,
      fromStartCount,
      canExpandToEnd: toEndCount > checked.size,
      canExpandFromStart: fromStartCount > checked.size && lastPos > 0,
    };
  }, [checked, filteredSegments]);

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
    if (!searchTerm.trim() || (useRegex && !!regexError)) return;
    const n = await replaceAll(searchTerm, replaceTerm, targetColumn, useRegex);
    setReplaceResult(n ? t('table.replaced', { count: n }) : t('table.noMatches'));
    setTimeout(() => setReplaceResult(null), 3000);
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.altKey && e.key.toLowerCase() === 'r') {
      e.preventDefault();
      setUseRegex((v) => !v);
    }
  };

  const onSelect = useCallback(
    (seg: SubtitleSegment) => {
      setSelectedSegmentIndex(seg.index);
      seekTo(seg.start_time - SEEK_LEAD_IN_S, true); // seekTo clamps at 0
    },
    [setSelectedSegmentIndex, seekTo]
  );

  // Track scroll container height for virtualization
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    setViewportHeight(el.clientHeight || 640);
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const h = entry.contentRect.height;
        if (h > 0) setViewportHeight(h);
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    return () => {
      if (scrollRafRef.current !== null) cancelAnimationFrame(scrollRafRef.current);
    };
  }, []);

  const handleTableScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const top = e.currentTarget.scrollTop;
    if (scrollRafRef.current !== null) cancelAnimationFrame(scrollRafRef.current);
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = null;
      setScrollTop(top);
    });
  }, []);

  const scrollToListIndex = useCallback((listIdx: number) => {
    const el = scrollContainerRef.current;
    if (!el || listIdx < 0) return;
    const targetTop = listIdx * ESTIMATED_ROW_HEIGHT;
    const viewH = el.clientHeight || 640;
    const desired = Math.max(0, targetTop - Math.floor(viewH * 0.35));
    el.scrollTop = desired;
    setScrollTop(desired);
  }, []);

  const scrollToTop = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    el.scrollTop = 0;
    setScrollTop(0);
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
    el.scrollTop = maxTop;
    setScrollTop(maxTop);
  }, []);

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

  // Follow playback — only when the active subtitle changes, not on every time tick
  useEffect(() => {
    if (!autoScroll || activeIndex === null) return;
    const el = scrollContainerRef.current;
    if (!el) return;
    const idx = filteredSegmentsRef.current.findIndex((s) => s.index === activeIndex);
    if (idx < 0) return;
    const rowTop = idx * ESTIMATED_ROW_HEIGHT;
    const rowBottom = rowTop + ESTIMATED_ROW_HEIGHT;
    const viewTop = el.scrollTop;
    const viewBottom = viewTop + el.clientHeight - 40;
    if (rowTop < viewTop + 36 || rowBottom > viewBottom) {
      const nextTop = Math.max(0, rowTop - Math.floor(el.clientHeight * 0.3));
      el.scrollTop = nextTop;
      setScrollTop(nextTop);
    }
  }, [activeIndex, autoScroll]);

  const isVirtualized = filteredSegments.length > VIRTUALIZE_THRESHOLD;
  const totalRows = filteredSegments.length;
  const startIndex = isVirtualized
    ? Math.max(0, Math.floor(scrollTop / ESTIMATED_ROW_HEIGHT) - OVERSCAN_ROWS)
    : 0;
  const visibleCount = isVirtualized
    ? Math.ceil(viewportHeight / ESTIMATED_ROW_HEIGHT) + OVERSCAN_ROWS * 2
    : totalRows;
  const endIndex = isVirtualized ? Math.min(totalRows, startIndex + visibleCount) : totalRows;
  const topSpacerHeight = isVirtualized ? startIndex * ESTIMATED_ROW_HEIGHT : 0;
  const bottomSpacerHeight = isVirtualized ? Math.max(0, (totalRows - endIndex) * ESTIMATED_ROW_HEIGHT) : 0;
  const visibleSegments = isVirtualized ? filteredSegments.slice(startIndex, endIndex) : filteredSegments;

  const insertDraft = useJobStore((s) => s.insertDraft);
  const openInsert = useJobStore((s) => s.openInsert);
  const videoCurrentTimeNow = () => useJobStore.getState().videoCurrentTime;
  const canInsert = !!activeJob && jobFinished && !isLive;
  const insertAfter = useCallback(
    (seg: SubtitleSegment) => openInsert({ time: useJobStore.getState().videoCurrentTime, afterId: seg.id }),
    [openInsert]
  );
  const handleDeleteRow = useCallback(
    (seg: SubtitleSegment) => {
      if (seg.id) void deleteSegment(seg.id);
    },
    [deleteSegment]
  );

  const handleDeleteSelected = async () => {
    if (checked.size === 0) return;
    if (!window.confirm(t('table.confirmDeleteSelectedRows', { count: checked.size }))) return;
    const ids = [...checked];
    setChecked(new Set());
    await deleteSegments(ids);
  };

  return (
    <div className="flex flex-col h-full bg-surface border border-line rounded-xl overflow-hidden shadow-xl">
      <div className="p-3 bg-sunken border-b border-line space-y-2">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="w-3.5 h-3.5 absolute start-2.5 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none" />
            <input
              type="text"
              placeholder={useRegex ? t('table.searchRegex') : t('table.search')}
              dir="auto"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              className={`w-full bg-surface border rounded-lg ps-8 pe-8 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none transition-colors ${
                useRegex && regexError
                  ? 'border-rose-500/80 focus:border-rose-500 text-rose-200'
                  : 'border-line focus:border-indigo-500'
              }`}
            />
            <button
              type="button"
              onClick={() => setUseRegex((v) => !v)}
              title={t('table.regexToggle')}
              aria-label={t('table.regexToggle')}
              aria-pressed={useRegex}
              className={`absolute end-1.5 top-1/2 -translate-y-1/2 p-1 rounded transition-colors cursor-pointer flex items-center justify-center ${
                useRegex
                  ? 'bg-indigo-600/30 text-indigo-300 border border-indigo-500/50 hover:bg-indigo-600/40'
                  : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/60'
              }`}
            >
              <Regex className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="relative flex-1">
            <Replace className="w-3.5 h-3.5 absolute start-2.5 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none" />
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
            disabled={!searchTerm.trim() || (useRegex && !!regexError) || isLive}
            className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-xs font-semibold text-zinc-200 transition-colors cursor-pointer"
          >
            {t('table.replaceAll')}
          </button>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-zinc-400 px-1">
          <div className="flex items-center gap-3 flex-wrap">
            <span>
              {useRegex && regexError ? (
                <span className="text-rose-400 font-medium flex items-center gap-1">
                  <AlertTriangle className="w-3 h-3 inline" />
                  {t('table.invalidRegex')}: {regexError}
                </span>
              ) : replaceResult ? (
                <span className="text-indigo-300">{replaceResult}</span>
              ) : (
                <>
                  {t('table.showing', { shown: filteredSegments.length, total: segments.length })}
                  {isLive && <span className="text-zinc-500"> · {t('table.livePreview')}</span>}
                </>
              )}
            </span>

            {/* Tag / Segment-type filter */}
            <div className="flex items-center gap-1.5 ms-1">
              <div
                className={`flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-xs transition-colors ${
                  tagFilter === 'ALL'
                    ? 'border-line bg-surface/70 text-zinc-400'
                    : tagFilter === 'ON_SCREEN'
                    ? 'border-cyan-500/50 bg-cyan-500/10 text-cyan-300 font-medium'
                    : tagFilter === 'COMMS'
                    ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300 font-medium'
                    : tagFilter === 'FOREIGN'
                    ? 'border-purple-500/50 bg-purple-500/10 text-purple-300 font-medium'
                    : 'border-indigo-500/50 bg-indigo-500/10 text-indigo-300 font-medium'
                }`}
              >
                <Tag className="w-3 h-3 opacity-70" />
                <select
                  aria-label={t('table.filterByTagLabel')}
                  value={tagFilter}
                  onChange={(e) => setTagFilter(e.target.value)}
                  className="bg-transparent border-0 text-inherit text-[11px] font-inherit focus:outline-none cursor-pointer pr-1"
                >
                  <option value="ALL" className="bg-zinc-900 text-zinc-300">{t('table.allTags')}</option>
                  {tagCounts.anyTagged > 0 && (
                    <option value="TAGGED" className="bg-zinc-900 text-zinc-300">
                      {t('table.anyTag', { count: tagCounts.anyTagged })}
                    </option>
                  )}
                  <option value="ON_SCREEN" className="bg-zinc-900 text-zinc-300">
                    📺 {t('table.onscreenBadge')} ({tagCounts.onscreen})
                  </option>
                  <option value="COMMS" className="bg-zinc-900 text-zinc-300">
                    📻 {t('table.commsBadge')} ({tagCounts.comms})
                  </option>
                  <option value="FOREIGN" className="bg-zinc-900 text-zinc-300">
                    🌐 {t('table.foreignBadge')} ({tagCounts.foreign})
                  </option>
                </select>
                {tagFilter !== 'ALL' && (
                  <button
                    type="button"
                    onClick={() => setTagFilter('ALL')}
                    title={t('table.clearTagFilter')}
                    className="p-0.5 rounded hover:bg-white/10 text-inherit opacity-70 hover:opacity-100 transition-opacity"
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>
            </div>

            {/* Quick jump to line # or timestamp + Top/Bottom scroll buttons */}
            {filteredSegments.length > 20 && (
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
            )}
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            {/* Rich problem filter (checkbox + category selector for untranslated, leftover English, CPS) */}
            <div
              className={`flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-xs transition-colors ${
                problemFilter !== 'NONE'
                  ? 'border-amber-500/50 bg-amber-500/10 text-amber-300 font-medium'
                  : 'border-line bg-surface/70 text-zinc-400'
              }`}
              title={isSyncJob ? t('table.onlyProblemsSyncTitle') : t('table.onlyProblemsTitle')}
            >
              <input
                type="checkbox"
                data-testid="only-problems-checkbox"
                checked={problemFilter !== 'NONE'}
                onChange={(e) => setProblemFilter(e.target.checked ? 'ALL_PROBLEMS' : 'NONE')}
                className="w-3.5 h-3.5 rounded border-zinc-700 text-indigo-600 bg-zinc-800 cursor-pointer"
              />
              <AlertTriangle className="w-3 h-3 text-amber-400 flex-shrink-0" />
              <select
                aria-label={t('table.problemFilterLabel')}
                data-testid="problem-filter-select"
                value={problemFilter}
                onChange={(e) => setProblemFilter(e.target.value as ProblemFilter)}
                className="bg-transparent border-0 text-inherit text-[11px] font-inherit focus:outline-none cursor-pointer pr-1"
              >
                <option value="NONE" className="bg-zinc-900 text-zinc-300">
                  {t('table.onlyProblems', { count: problemStats.all })}
                </option>
                <option value="ALL_PROBLEMS" className="bg-zinc-900 text-zinc-300">
                  ⚠️ {t('table.problemAll', { count: problemStats.all })}
                </option>
                <option value="UNTRANSLATED" className="bg-zinc-900 text-zinc-300">
                  ❌ {t('table.problemUntranslated', { count: problemStats.untranslated })}
                </option>
                {(isRtl || problemStats.latin > 0) && (
                  <option value="LATIN_LEFTOVER" className="bg-zinc-900 text-zinc-300">
                    🔤 {t('table.problemLatin', { count: problemStats.latin })}
                  </option>
                )}
                <option value="HIGH_CPS" className="bg-zinc-900 text-zinc-300">
                  ⚡ {t('table.problemHighCps', { count: problemStats.highCps })}
                </option>
              </select>
              {problemFilter !== 'NONE' && (
                <button
                  type="button"
                  onClick={() => setProblemFilter('NONE')}
                  className="p-0.5 rounded hover:bg-white/10 text-inherit opacity-70 hover:opacity-100 transition-opacity"
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>

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
            {canInsert && (
              <button
                onClick={() => openInsert({ time: videoCurrentTimeNow() })}
                title={t('table.addLineTitle')}
                data-testid="add-line"
                className="flex items-center gap-1.5 px-2 py-1 rounded border border-line text-zinc-300 hover:text-white hover:border-indigo-500/60"
              >
                <Plus className="w-3 h-3" />
                {t('table.addLine')}
              </button>
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
                      const idx = filteredSegments.findIndex((s) => s.id === i.segment_id);
                      if (idx >= 0) {
                        scrollToListIndex(idx);
                        onSelect(filteredSegments[idx]);
                      } else {
                        const seg = segments.find((s) => s.id === i.segment_id);
                        if (seg) onSelect(seg);
                      }
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

          {/* Fast 1-click range expansion: from checked row to end, or from start to checked row */}
          {checkedRangeBounds?.canExpandToEnd && checkedRangeBounds.firstSeg.id && (
            <button
              type="button"
              onClick={() => handleSelectToEnd(checkedRangeBounds.firstSeg.id!)}
              data-testid="select-checked-to-end"
              className="flex items-center gap-1 px-2 py-1 rounded border border-indigo-400/40 bg-indigo-500/15 text-indigo-200 hover:bg-indigo-500/25 cursor-pointer"
            >
              <ChevronsDown className="w-3 h-3" />
              {t('table.selectFromToEnd', {
                index: checkedRangeBounds.firstSeg.index,
                count: checkedRangeBounds.toEndCount,
              })}
            </button>
          )}
          {checkedRangeBounds?.canExpandFromStart && checkedRangeBounds.lastSeg.id && (
            <button
              type="button"
              onClick={() => handleSelectFromStart(checkedRangeBounds.lastSeg.id!)}
              data-testid="select-start-to-checked"
              className="flex items-center gap-1 px-2 py-1 rounded border border-line text-zinc-300 hover:border-indigo-500/60 hover:text-white cursor-pointer"
            >
              <ChevronsUp className="w-3 h-3" />
              {t('table.selectFromStartTo', {
                index: checkedRangeBounds.lastSeg.index,
                count: checkedRangeBounds.fromStartCount,
              })}
            </button>
          )}

          {canTranslate && (
            <>
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
            </>
          )}
          {canInsert && (
            <button
              onClick={() => void handleDeleteSelected()}
              data-testid="rows-delete"
              className="flex items-center gap-1 px-2 py-1 rounded border border-rose-500/40 text-rose-300 hover:bg-rose-500/15"
            >
              <Trash2 className="w-3 h-3" />
              {t('table.deleteSelectedRows', { count: checked.size })}
            </button>
          )}
          <button onClick={() => setChecked(new Set())} className="ms-auto text-zinc-400 hover:text-white">
            {t('table.clearSelection')}
          </button>
        </div>
      )}
      {insertDraft && <InsertSegmentDialog draft={insertDraft} />}
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

      <div ref={scrollContainerRef} onScroll={handleTableScroll} className="flex-1 overflow-y-auto">
        <table className="w-full text-start border-collapse text-xs">
          <thead className="bg-sunken text-zinc-400 sticky top-0 z-10 border-b border-line uppercase font-semibold text-[10px] tracking-wider select-none">
            <tr>
              <th className="py-2.5 px-3 w-14 text-center">
                <div className="flex items-center justify-center gap-1.5">
                  {canSelect && selectableFilteredIds.length > 0 && (
                    <input
                      ref={selectAllRef}
                      type="checkbox"
                      checked={allFilteredChecked}
                      onChange={toggleSelectAllFiltered}
                      title={
                        allFilteredChecked
                          ? t('table.deselectAllShown')
                          : t('table.selectAllShown', { count: selectableFilteredIds.length })
                      }
                      aria-label={
                        allFilteredChecked
                          ? t('table.deselectAllShown')
                          : t('table.selectAllShown', { count: selectableFilteredIds.length })
                      }
                      data-testid="select-all-shown"
                      className="w-3.5 h-3.5 rounded border-zinc-600 text-indigo-600 bg-zinc-800 cursor-pointer"
                    />
                  )}
                  <span>#</span>
                </div>
              </th>
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
              <>
                {topSpacerHeight > 0 && (
                  <tr aria-hidden="true" className="border-0">
                    <td colSpan={5} style={{ height: `${topSpacerHeight}px`, padding: 0, border: 0 }} />
                  </tr>
                )}
                {visibleSegments.map((seg) => {
                  const isActive = seg.index === activeIndex;
                  const rowCanDrag = canDrag && !!seg.id;
                  const dropPosition =
                    rowCanDrag && dragId !== null && overId === seg.id && dragId !== seg.id
                      ? seg.index > (segments[dragFromPos]?.index ?? 0)
                        ? 'after'
                        : 'before'
                      : null;
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
                      onSelectToEnd={canSelect ? handleSelectToEnd : undefined}
                      onInsertAfter={canInsert ? insertAfter : undefined}
                      onDelete={canInsert ? handleDeleteRow : undefined}
                      onFilterTag={setTagFilter}
                      canDrag={rowCanDrag}
                      isDragging={rowCanDrag && dragId === seg.id}
                      dropPosition={dropPosition}
                      onDragStart={rowCanDrag ? handleDragStart : undefined}
                      onDragOver={rowCanDrag ? handleDragOver : undefined}
                      onDrop={rowCanDrag ? handleDrop : undefined}
                      onDragEnd={rowCanDrag ? handleDragEnd : undefined}
                    />
                  );
                })}
                {bottomSpacerHeight > 0 && (
                  <tr aria-hidden="true" className="border-0">
                    <td colSpan={5} style={{ height: `${bottomSpacerHeight}px`, padding: 0, border: 0 }} />
                  </tr>
                )}
              </>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
