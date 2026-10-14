import React, { useCallback, useEffect, useState } from 'react';
import { HardDrive, Loader2, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '../../services/api';
import { useT } from '../../i18n';
import type { MessageKey } from '../../i18n';
import type { StorageCategoryId, StorageReport } from '../../types';

const card = 'bg-surface border border-line rounded-xl p-5 shadow-lg space-y-4';

const LABELS: Record<StorageCategoryId, { title: MessageKey; hint: MessageKey }> = {
  temp: { title: 'settings.storageTemp', hint: 'settings.storageTempHint' },
  cache: { title: 'settings.storageCache', hint: 'settings.storageCacheHint' },
  logs: { title: 'settings.storageLogs', hint: 'settings.storageLogsHint' },
  exports: { title: 'settings.storageExports', hint: 'settings.storageExportsHint' },
  uploads: { title: 'settings.storageUploads', hint: 'settings.storageUploadsHint' },
  jobs: { title: 'settings.storageJobs', hint: 'settings.storageJobsHint' },
};

/** Human readable size, e.g. 1.4 GB */
function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

/** Disk usage by category with one-click cleanup. Deleting finished projects needs a second click. */
export const StorageSection: React.FC = () => {
  const t = useT();
  const [days, setDays] = useState(30);
  const [report, setReport] = useState<StorageReport | null>(null);
  const [picked, setPicked] = useState<Set<StorageCategoryId>>(new Set(['temp', 'cache', 'logs', 'exports', 'uploads']));
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setReport(await api.storageReport(days));
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }, [days]);

  useEffect(() => {
    void load(); // eslint-disable-line react-hooks/set-state-in-effect
  }, [load]);

  const toggle = (id: StorageCategoryId) => {
    setConfirming(false);
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selected = (report?.categories ?? []).filter((c) => picked.has(c.id) && c.count > 0);
  const hasJobs = selected.some((c) => c.id === 'jobs');

  const doClean = async () => {
    setBusy(true);
    setConfirming(false);
    setMessage(null);
    try {
      const res = await api.storageClean(
        selected.map((c) => c.id),
        hasJobs ? days : undefined
      );
      setMessage(t('settings.storageCleaned', { size: formatBytes(res.freed_bytes) }));
      await load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onClean = () => {
    if (hasJobs && !confirming) setConfirming(true);
    else void doClean();
  };

  return (
    <section className={card} data-testid="storage-section">
      <div className="border-b border-line pb-3">
        <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
          <HardDrive className="w-4 h-4 text-indigo-400" />
          {t('settings.storage')}
          <button
            onClick={() => void load()}
            title={t('settings.storageRefresh')}
            className="ms-auto text-zinc-400 hover:text-white"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </h2>
        <p className="text-[11px] text-zinc-400 mt-1">{t('settings.storageHint')}</p>
      </div>

      <ul className="space-y-2">
        {(report?.categories ?? []).map((c) => (
          <li key={c.id} className="flex items-start gap-3 text-xs">
            <input
              type="checkbox"
              checked={picked.has(c.id)}
              onChange={() => toggle(c.id)}
              disabled={c.count === 0}
              aria-label={t(LABELS[c.id].title)}
              data-testid={`storage-${c.id}`}
              className="mt-0.5 w-3.5 h-3.5 rounded border-zinc-700 text-indigo-600 bg-zinc-800"
            />
            <div className="flex-1">
              <div className="text-zinc-200">{t(LABELS[c.id].title)}</div>
              <div className="text-[11px] text-zinc-500">{t(LABELS[c.id].hint)}</div>
              {c.id === 'jobs' && (
                <label className="inline-flex items-center gap-2 mt-1 text-[11px] text-zinc-400">
                  {t('settings.storageJobsOlder')}
                  <input
                    type="number"
                    min={0}
                    value={days}
                    onChange={(e) => setDays(Math.max(0, Number(e.target.value) || 0))}
                    className="w-16 bg-sunken border border-line rounded px-2 py-0.5 text-zinc-200"
                  />
                </label>
              )}
            </div>
            <div className="text-end text-zinc-300 tabular-nums">
              {c.count === 0 ? <span className="text-zinc-500">{t('settings.storageEmpty')}</span> : formatBytes(c.bytes)}
              {c.count > 0 && <div className="text-[10px] text-zinc-500">{t('settings.storageFiles', { count: c.count })}</div>}
            </div>
          </li>
        ))}
      </ul>

      <div className="flex items-center gap-3 pt-1">
        <button
          onClick={onClean}
          disabled={busy || selected.length === 0}
          data-testid="storage-clean"
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white disabled:opacity-40 ${
            confirming ? 'bg-red-600 hover:bg-red-500' : 'bg-indigo-600 hover:bg-indigo-500'
          }`}
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          {confirming ? t('settings.storageConfirm') : t('settings.storageClean')}
        </button>
        {confirming && (
          <button onClick={() => setConfirming(false)} className="text-xs text-zinc-300 hover:text-white">
            {t('settings.storageCancel')}
          </button>
        )}
        {message && <span className="text-xs text-indigo-300" data-testid="storage-message">{message}</span>}
        {report && (
          <span className="ms-auto text-[11px] text-zinc-500">
            {t('settings.storageDb')}: {formatBytes(report.database_bytes)}
          </span>
        )}
      </div>
    </section>
  );
};
