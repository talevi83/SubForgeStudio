import React from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { useJobStore } from '../../../store/useJobStore';
import { useT } from '../../../i18n';
import type { ProblemFilter } from '../../../types';
import type { ProblemStats } from './useSegmentFilters';

interface ProblemFilterSelectProps {
  stats: ProblemStats;
  isRtl: boolean;
  isSyncJob: boolean;
}

/** "Only problems" checkbox + category selector (untranslated, leftover English, fast reading speed). */
export const ProblemFilterSelect: React.FC<ProblemFilterSelectProps> = ({ stats, isRtl, isSyncJob }) => {
  const problemFilter = useJobStore((s) => s.problemFilter);
  const setProblemFilter = useJobStore((s) => s.setProblemFilter);
  const t = useT();
  return (
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
          {t('table.onlyProblems', { count: stats.all })}
        </option>
        <option value="ALL_PROBLEMS" className="bg-zinc-900 text-zinc-300">
          ⚠️ {t('table.problemAll', { count: stats.all })}
        </option>
        <option value="UNTRANSLATED" className="bg-zinc-900 text-zinc-300">
          ❌ {t('table.problemUntranslated', { count: stats.untranslated })}
        </option>
        {(isRtl || stats.latin > 0) && (
          <option value="LATIN_LEFTOVER" className="bg-zinc-900 text-zinc-300">
            🔤 {t('table.problemLatin', { count: stats.latin })}
          </option>
        )}
        <option value="HIGH_CPS" className="bg-zinc-900 text-zinc-300">
          ⚡ {t('table.problemHighCps', { count: stats.highCps })}
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
  );
};
