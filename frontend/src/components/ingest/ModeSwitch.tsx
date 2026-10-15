import React from 'react';
import { Captions, TimerReset } from 'lucide-react';
import { useJobStore } from '../../store/useJobStore';
import { useT } from '../../i18n';
import type { IngestMode } from '../../types';

/** New project: make subtitles from the audio, or sync a subtitle file the user already has. */
export const ModeSwitch: React.FC = () => {
  const mode = useJobStore((s) => s.ingestMode);
  const setMode = useJobStore((s) => s.setIngestMode);
  const t = useT();

  const option = (value: IngestMode, Icon: typeof Captions, title: string, hint: string) => {
    const active = mode === value;
    return (
      <button
        type="button"
        role="radio"
        aria-checked={active}
        onClick={() => setMode(value)}
        data-testid={`mode-${value}`}
        className={`flex-1 flex items-start gap-3 p-4 rounded-xl border text-start transition-colors cursor-pointer ${
          active
            ? 'bg-indigo-500/10 border-indigo-500/50 shadow-sm'
            : 'bg-surface border-line hover:border-zinc-600'
        }`}
      >
        <span
          className={`mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg ${
            active ? 'bg-indigo-600 text-white' : 'bg-sunken text-zinc-400'
          }`}
        >
          <Icon className="w-4 h-4" />
        </span>
        <span className="min-w-0">
          <span className={`block text-sm font-semibold ${active ? 'text-zinc-50' : 'text-zinc-200'}`}>{title}</span>
          <span className="block text-[11px] text-zinc-400 mt-0.5 leading-relaxed">{hint}</span>
        </span>
      </button>
    );
  };

  return (
    <div role="radiogroup" className="flex flex-col sm:flex-row gap-3">
      {option('transcribe', Captions, t('mode.transcribe'), t('mode.transcribeHint'))}
      {option('sync', TimerReset, t('mode.sync'), t('mode.syncHint'))}
    </div>
  );
};
