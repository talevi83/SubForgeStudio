import React from 'react';
import { Loader2, Plus, ShieldCheck, Timer } from 'lucide-react';
import { useJobStore } from '../../../store/useJobStore';
import { useT } from '../../../i18n';
import { HistoryControls } from './HistoryControls';

interface TableActionsProps {
  /** A finished job with subtitles: re-check timing and the quality check are available */
  canRecheck: boolean;
  canInsert: boolean;
  qaBusy: boolean;
  onRunQa: () => void;
  autoScroll: boolean;
  onAutoScrollChange: (value: boolean) => void;
}

/** Right side of the second toolbar row: undo/redo/history, re-check timing, add line, quality check, auto-scroll. */
export const TableActions: React.FC<TableActionsProps> = ({
  canRecheck,
  canInsert,
  qaBusy,
  onRunQa,
  autoScroll,
  onAutoScrollChange,
}) => {
  const retimeActiveJob = useJobStore((s) => s.retimeActiveJob);
  const timingBusy = useJobStore((s) => s.timingBusy);
  const timingNote = useJobStore((s) => s.timingNote);
  const openInsert = useJobStore((s) => s.openInsert);
  const t = useT();

  return (
    <>
      {canRecheck && <HistoryControls />}
      {canRecheck && (
        <span className="flex items-center gap-2">
          {timingNote !== null && !timingBusy && (
            <span className="text-indigo-300">
              {Number(timingNote) > 0 ? t('table.timingFixed', { count: Number(timingNote) }) : t('table.timingOk')}
            </span>
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
          onClick={() => openInsert({ time: useJobStore.getState().videoCurrentTime })}
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
          onClick={onRunQa}
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
          onChange={(e) => onAutoScrollChange(e.target.checked)}
          className="w-3.5 h-3.5 rounded border-zinc-700 text-indigo-600 bg-zinc-800"
        />
        <span>{t('table.autoScroll')}</span>
      </label>
    </>
  );
};
