import { useCallback, useState } from 'react';
import type { SubtitleSegment } from '../../../types';

/** Drag a row onto another to move its text there (time slots stay, see POST /segments/move). */
export function useRowDragDrop(
  segments: SubtitleSegment[],
  moveSegment: (segmentId: number, position: number) => unknown
) {
  const [dragId, setDragId] = useState<number | null>(null);
  const [overId, setOverId] = useState<number | null>(null);

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

  return { dragId, overId, dragFromPos, handleDragStart, handleDragOver, handleDragEnd, handleDrop };
}
