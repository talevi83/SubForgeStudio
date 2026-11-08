import { useCallback, useEffect, useRef, useState } from 'react';
import type React from 'react';
import type { SubtitleSegment } from '../../../types';

/** Virtual table constants: only rows near the viewport are mounted in the DOM */
const ESTIMATED_ROW_HEIGHT = 64;
const OVERSCAN_ROWS = 12;
const VIRTUALIZE_THRESHOLD = 80;

/**
 * Windowed rendering of the subtitle table: tracks the scroll position and viewport height,
 * returns only the rows near the viewport (plus spacer heights), scroll helpers, and follows
 * playback by scrolling to the active subtitle when it changes.
 */
export function useVirtualRows(filteredSegments: SubtitleSegment[], activeIndex: number | null, autoScroll: boolean) {
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(640);
  const scrollRafRef = useRef<number | null>(null);

  const filteredSegmentsRef = useRef<SubtitleSegment[]>(filteredSegments);
  useEffect(() => {
    filteredSegmentsRef.current = filteredSegments;
  }, [filteredSegments]);

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

  return {
    scrollContainerRef,
    handleTableScroll,
    scrollToListIndex,
    scrollToTop,
    scrollToBottom,
    visibleSegments,
    topSpacerHeight,
    bottomSpacerHeight,
  };
}
