import React, { useEffect, useRef, useState } from 'react';
import { History, Loader2, Redo2, Undo2 } from 'lucide-react';
import { useJobStore } from '../../../store/useJobStore';
import { useT } from '../../../i18n';
import { useUiStore } from '../../../store/useUiStore';
import { historyLabel } from './historyLabel';
import { useHistoryShortcuts } from './useHistoryShortcuts';

/**
 * Undo / Redo buttons and a Photoshop-style History panel: every step of the project, the
 * current one highlighted; clicking a step goes back (or forward) to it. Steps after the
 * current one are dimmed and disappear with the next edit.
 */
export const HistoryControls: React.FC = () => {
  const history = useJobStore((s) => s.history);
  const historyBusy = useJobStore((s) => s.historyBusy);
  const timingBusy = useJobStore((s) => s.timingBusy);
  const undo = useJobStore((s) => s.undo);
  const redo = useJobStore((s) => s.redo);
  const restoreHistory = useJobStore((s) => s.restoreHistory);
  const refreshHistory = useJobStore((s) => s.refreshHistory);
  const language = useUiStore((s) => s.language);
  const t = useT();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const currentRef = useRef<HTMLButtonElement | null>(null);

  useHistoryShortcuts(true);

  useEffect(() => {
    if (!open) return;
    void refreshHistory();
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('pointerdown', onPointerDown, true);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown, true);
      window.removeEventListener('keydown', onKey);
    };
  }, [open, refreshHistory]);

  useEffect(() => {
    if (open) currentRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [open, history?.cursor]);

  const busy = historyBusy || timingBusy;
  const entries = history?.entries ?? [];
  const cursorPos = entries.findIndex((e) => e.id === history?.cursor);
  const time = (iso: string) =>
    new Date(iso).toLocaleTimeString(language === 'he' ? 'he-IL' : 'en-US', { hour: '2-digit', minute: '2-digit' });
  const button =
    'flex items-center gap-1 px-2 py-1 rounded border border-line text-zinc-300 hover:text-white hover:border-indigo-500/60 disabled:opacity-40 disabled:hover:text-zinc-300 disabled:hover:border-line cursor-pointer disabled:cursor-default';

  return (
    <div ref={rootRef} className="relative flex items-center gap-1" data-testid="history-controls">
      <button
        type="button"
        onClick={() => void undo()}
        disabled={busy || !history?.can_undo}
        title={t('history.undoTitle')}
        aria-label={t('history.undo')}
        data-testid="history-undo"
        className={button}
      >
        {historyBusy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Undo2 className="w-3 h-3" />}
      </button>
      <button
        type="button"
        onClick={() => void redo()}
        disabled={busy || !history?.can_redo}
        title={t('history.redoTitle')}
        aria-label={t('history.redo')}
        data-testid="history-redo"
        className={button}
      >
        <Redo2 className="w-3 h-3" />
      </button>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={t('history.panelTitle')}
        aria-expanded={open}
        data-testid="history-toggle"
        className={`${button} ${open ? 'border-indigo-500/60 text-white' : ''}`}
      >
        <History className="w-3 h-3" />
        {t('history.panel')}
      </button>

      {open && (
        <div
          role="dialog"
          data-testid="history-panel"
          className="absolute top-full end-0 mt-1 z-30 w-72 rounded-lg border border-line bg-surface shadow-xl shadow-black/40"
        >
          <div className="px-3 py-2 border-b border-line text-[11px] font-semibold text-zinc-300 flex items-center gap-1.5">
            <History className="w-3.5 h-3.5 text-indigo-400" />
            {t('history.panel')}
          </div>
          {entries.length === 0 ? (
            <p className="px-3 py-3 text-[11px] text-zinc-500">{t('history.empty')}</p>
          ) : (
            <ul className="max-h-72 overflow-y-auto py-1">
              {entries.map((entry, i) => {
                const current = i === cursorPos;
                const ahead = cursorPos >= 0 && i > cursorPos;
                return (
                  <li key={entry.id}>
                    <button
                      type="button"
                      ref={current ? currentRef : undefined}
                      onClick={() => !current && void restoreHistory(entry.id)}
                      disabled={busy}
                      aria-current={current ? 'step' : undefined}
                      data-testid="history-entry"
                      className={`w-full flex items-center gap-2 px-3 py-1.5 text-start text-[11px] transition-colors ${
                        current
                          ? 'bg-indigo-600/25 text-white font-medium'
                          : ahead
                          ? 'text-zinc-500 italic hover:bg-zinc-800/60'
                          : 'text-zinc-300 hover:bg-zinc-800/60'
                      } disabled:cursor-wait`}
                    >
                      <span
                        className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${current ? 'bg-indigo-400' : ahead ? 'bg-zinc-700' : 'bg-zinc-500'}`}
                      />
                      <span className="flex-1 truncate">{historyLabel(t, entry)}</span>
                      <span className="text-[10px] text-zinc-500 tabular-nums" dir="ltr">
                        {time(entry.created_at)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {entries.length > 1 && <p className="px-3 py-2 border-t border-line text-[10px] text-zinc-500">{t('history.hint')}</p>}
        </div>
      )}
    </div>
  );
};
