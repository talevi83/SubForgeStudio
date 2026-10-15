import React, { useRef, useState } from 'react';
import { FileText, Upload, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react';
import { api } from '../../services/api';
import { useJobStore } from '../../store/useJobStore';
import { useT } from '../../i18n';
import { formatClock } from '../../utils/time';
import { TARGET_LANGUAGES } from './languages';

const ACCEPT = '.srt,.vtt,.ass,.ssa,.sub,.txt';

/** "Sync my subtitles": the subtitle file whose timing should be fixed. */
export const SubtitleFilePicker: React.FC = () => {
  const syncFile = useJobStore((s) => s.syncFile);
  const setSyncFile = useJobStore((s) => s.setSyncFile);
  const syncLanguage = useJobStore((s) => s.syncLanguage);
  const setSyncLanguage = useJobStore((s) => s.setSyncLanguage);
  const t = useT();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const read = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      setSyncFile(await api.parseSubtitles(file));
    } catch (e: unknown) {
      setSyncFile(null);
      setError(e instanceof Error ? e.message : t('syncFile.failed'));
    } finally {
      setBusy(false);
    }
  };

  const languages = TARGET_LANGUAGES.some((l) => l.value === syncLanguage) || !syncLanguage
    ? TARGET_LANGUAGES
    : [{ value: syncLanguage, label: syncLanguage }, ...TARGET_LANGUAGES];

  return (
    <div className="bg-surface border border-line rounded-xl p-5 shadow-lg" data-testid="subtitle-file-picker">
      <div className="border-b border-line pb-3 mb-4">
        <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
          <FileText className="w-4 h-4 text-indigo-400" />
          {t('syncFile.title')}
        </h2>
        <p className="text-xs text-zinc-400 mt-0.5">{t('syncFile.subtitle')}</p>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void read(file);
          e.target.value = '';
        }}
      />

      {!syncFile ? (
        <div
          role="button"
          tabIndex={0}
          onClick={() => !busy && inputRef.current?.click()}
          onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && !busy && inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const file = e.dataTransfer.files?.[0];
            if (file && !busy) void read(file);
          }}
          className={`border-2 border-dashed rounded-xl p-6 text-center bg-sunken/50 transition-colors flex flex-col items-center justify-center cursor-pointer ${
            dragging ? 'border-indigo-500' : 'border-line hover:border-indigo-500/50'
          }`}
        >
          {busy ? (
            <div className="flex items-center gap-2 text-xs text-indigo-400 font-medium">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              {t('syncFile.reading')}
            </div>
          ) : (
            <>
              <Upload className="w-7 h-7 text-indigo-400 mb-2" />
              <p className="text-xs font-medium text-zinc-300">{t('syncFile.dropTitle')}</p>
              <p className="text-[11px] text-zinc-500 mt-1" dir="ltr">
                SRT · VTT · ASS
              </p>
            </>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs flex items-start gap-2">
            <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <div className="min-w-0 flex-1 space-y-0.5">
              <div className="truncate">
                {t('syncFile.summary', {
                  name: syncFile.file_name,
                  count: syncFile.cue_count,
                  from: formatClock(syncFile.first_start),
                  to: formatClock(syncFile.last_end),
                })}
              </div>
              <div className="text-[11px] text-emerald-300/70 flex flex-wrap gap-x-3">
                <span dir="ltr">{syncFile.format.toUpperCase()}</span>
                <span>{t('syncFile.encoding', { encoding: syncFile.encoding })}</span>
                {syncFile.top_count > 0 && <span>{t('syncFile.signs', { count: syncFile.top_count })}</span>}
                {syncFile.sound_count > 0 && <span>{t('syncFile.sounds', { count: syncFile.sound_count })}</span>}
              </div>
            </div>
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className="text-[11px] text-indigo-400 hover:text-indigo-300 underline cursor-pointer whitespace-nowrap"
            >
              {t('syncFile.replace')}
            </button>
          </div>

          <div className="flex items-center gap-3 text-xs">
            <label htmlFor="sync-language" className="text-zinc-400 font-medium whitespace-nowrap">
              {t('syncFile.language')}
            </label>
            <select
              id="sync-language"
              value={syncLanguage || syncFile.language}
              onChange={(e) => setSyncLanguage(e.target.value)}
              className="bg-sunken border border-line rounded-lg px-3 py-1.5 text-zinc-200 focus:outline-none focus:border-indigo-500"
            >
              {languages.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </div>

          <ul className="rounded-lg bg-sunken border border-line divide-y divide-line text-[11px] text-zinc-300 max-h-28 overflow-y-auto">
            {syncFile.cues.slice(0, 4).map((cue, i) => (
              <li key={i} className="px-3 py-1.5 flex gap-3">
                <span className="font-mono text-zinc-500 flex-shrink-0" dir="ltr">
                  {formatClock(cue.start)}
                </span>
                <span dir="auto" className="truncate">
                  {cue.text.replace(/\n/g, ' / ')}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {error && (
        <div className="mt-3 p-3 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
};
