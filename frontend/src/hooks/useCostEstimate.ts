import { useEffect, useState } from 'react';
import { api } from '../services/api';
import type { AppSettings, CostEstimate } from '../types';

interface Params {
  duration_seconds: number;
  enable_translation: boolean;
  detect_onscreen_text: boolean;
  has_forced_track?: boolean;
  /** "Sync my subtitles" with Gemini line matching: the number of lines in the file */
  sync_lines?: number;
  settings?: AppSettings | null;
}

/** Server-side cost estimate, refreshed (debounced) whenever the inputs change. */
export function useCostEstimate(params: Params | null): CostEstimate | null {
  const [estimate, setEstimate] = useState<CostEstimate | null>(null);
  const key = params ? JSON.stringify(params) : '';

  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    const p: Params = JSON.parse(key);
    const timer = setTimeout(() => {
      api
        .estimateCost({ ...p, settings: p.settings || undefined })
        .then((res) => !cancelled && setEstimate(res))
        .catch(() => !cancelled && setEstimate(null));
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key]);

  return key ? estimate : null;
}
