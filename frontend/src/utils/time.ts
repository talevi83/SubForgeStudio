import type { SubtitleSegment } from '../types';

/** HH:MM:SS,mmm */
export const formatTimecode = (seconds: number) => {
  const totalMs = Math.round(Math.max(0, seconds) * 1000);
  const h = Math.floor(totalMs / 3_600_000);
  const m = Math.floor((totalMs % 3_600_000) / 60_000);
  const s = Math.floor((totalMs % 60_000) / 1000);
  const ms = totalMs % 1000;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')},${String(ms).padStart(3, '0')}`;
};

/**
 * Parse a time the user typed. The full form is the one the table shows, HH:MM:SS,mmm
 * ("00:12:34,500"); hours and milliseconds may be left out ("12:34,5" = 12 min 34.5 s) and "."
 * works instead of ",". Returns seconds, or null when the text is not a valid time.
 */
export const parseTimecode = (text: string): number | null => {
  const m = text.trim().match(/^(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2})(?:[,.](\d{1,3}))?$/);
  if (!m) return null;
  const [, h, mm, ss, ms] = m;
  const minutes = Number(mm);
  const seconds = Number(ss);
  if (minutes > 59 || seconds > 59) return null;
  return Number(h || 0) * 3600 + minutes * 60 + seconds + (ms ? Number(ms.padEnd(3, '0')) / 1000 : 0);
};

/** M:SS or H:MM:SS */
export const formatClock = (seconds: number) => {
  const t = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
};

/** Index into `segments` (sorted by start_time) of the subtitle shown at `time`, or -1. */
export const findSegmentAt = (segments: SubtitleSegment[], time: number): number => {
  let lo = 0;
  let hi = segments.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const seg = segments[mid];
    if (time < seg.start_time) hi = mid - 1;
    else if (time > seg.end_time) lo = mid + 1;
    else return mid;
  }
  return -1;
};

export const isTypingTarget = (target: EventTarget | null) => {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
};
