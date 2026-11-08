import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { SubtitleSegment } from '../../../types';

export interface CheckedRangeBounds {
  firstSeg: SubtitleSegment;
  lastSeg: SubtitleSegment;
  toEndCount: number;
  fromStartCount: number;
  canExpandToEnd: boolean;
  canExpandFromStart: boolean;
}

/**
 * Checkbox selection of table rows: single / Shift-range clicks, "select all shown",
 * and one-click expansion to the end or the start of the displayed list.
 * Changing `resetKey` (another job shown, a run starting) clears the selection.
 */
export function useRowSelection(filteredSegments: SubtitleSegment[], resetKey: string) {
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const lastCheckedRef = useRef<number | null>(null);

  const filteredSegmentsRef = useRef<SubtitleSegment[]>(filteredSegments);
  useEffect(() => {
    filteredSegmentsRef.current = filteredSegments;
  }, [filteredSegments]);

  // Selection belongs to the job being shown and goes away when a run starts
  useEffect(() => {
    setChecked(new Set()); // eslint-disable-line react-hooks/set-state-in-effect
    lastCheckedRef.current = null;
  }, [resetKey]);

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

  const checkedRangeBounds = useMemo<CheckedRangeBounds | null>(() => {
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

  return {
    checked,
    setChecked,
    selectableFilteredIds,
    allFilteredChecked,
    selectAllRef,
    toggleSelectAllFiltered,
    handleCheck,
    handleSelectToEnd,
    handleSelectFromStart,
    checkedRangeBounds,
  };
}
