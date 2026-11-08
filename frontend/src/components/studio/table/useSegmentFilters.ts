import { useCallback, useMemo } from 'react';
import type { ProblemFilter, SubtitleSegment } from '../../../types';

/** Matches 3+ consecutive ASCII Latin letters left inside an RTL translation */
const LATIN_WORD_RE = /[A-Za-z]{3,}/;

export interface ProblemStats {
  all: number;
  untranslated: number;
  latin: number;
  highCps: number;
}

export interface TagCounts {
  onscreen: number;
  comms: number;
  foreign: number;
  anyTagged: number;
}

export interface SegmentFilterOptions {
  segments: SubtitleSegment[];
  isRtl: boolean;
  /** The job translates (or did): an empty translation counts as a problem */
  hasTranslationExpectation: boolean;
  /** A translation is running right now, so empty lines are still on their way */
  translationEnabled: boolean;
  /** A synced file also flags lines that were placed with low confidence */
  isSyncJob: boolean;
  maxCps: number;
  problemFilter: ProblemFilter;
  tagFilter: string;
  /** Already deferred by the caller (useDeferredValue) */
  searchTerm: string;
  useRegex: boolean;
}

/** Problem / tag / text filters of the subtitle table, plus the counts shown in the filter menus. */
export function useSegmentFilters({
  segments,
  isRtl,
  hasTranslationExpectation,
  translationEnabled,
  isSyncJob,
  maxCps,
  problemFilter,
  tagFilter,
  searchTerm,
  useRegex,
}: SegmentFilterOptions) {
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

  const problemStats = useMemo<ProblemStats>(() => {
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

  const tagCounts = useMemo<TagCounts>(() => {
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
    if (!useRegex || !searchTerm) return { regex: null, error: null };
    try {
      const match = searchTerm.match(/^\/(.*)\/([a-z]*)$/s);
      if (match) {
        // Strip 'g' flag so RegExp.prototype.test() remains stateless across segments
        const flags = match[2] ? match[2].replace(/g/g, '') : 'i';
        return { regex: new RegExp(match[1], flags), error: null };
      }
      return { regex: new RegExp(searchTerm, 'i'), error: null };
    } catch (err) {
      return { regex: null, error: err instanceof Error ? err.message : String(err) };
    }
  }, [useRegex, searchTerm]);

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
    if (!searchTerm) return list;
    if (useRegex) {
      if (regexError || !searchRegex) return [];
      return list.filter(
        (s) => searchRegex.test(s.original_text) || (s.translated_text ? searchRegex.test(s.translated_text) : false)
      );
    }
    if (!searchTerm.trim()) return list;
    const term = searchTerm.toLowerCase();
    return list.filter(
      (s) => s.original_text.toLowerCase().includes(term) || s.translated_text?.toLowerCase().includes(term)
    );
  }, [
    segments,
    searchTerm,
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

  return { filteredSegments, problemStats, tagCounts, regexError };
}
