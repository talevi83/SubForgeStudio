import { useCallback, useState } from 'react';
import { api } from '../../../services/api';
import type { QAIssue } from '../../../types';

/** Runs the deterministic quality check (GET /jobs/{id}/qa) and keeps its result for the QA panel. */
export function useQualityCheck(jobId: string | undefined) {
  const [issues, setIssues] = useState<QAIssue[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    if (!jobId) return;
    setBusy(true);
    setError(null);
    try {
      setIssues(await api.qualityCheck(jobId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [jobId]);

  const clear = useCallback(() => {
    setIssues(null);
    setError(null);
  }, []);

  return { issues, busy, error, run, clear };
}
