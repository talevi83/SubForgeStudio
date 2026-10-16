import React from 'react';
import { useJobStore } from '../../store/useJobStore';
import { useUiStore } from '../../store/useUiStore';
import { useT } from '../../i18n';
import { formatClock } from '../../utils/time';
import { Film, Settings, HelpCircle } from 'lucide-react';

export const Header: React.FC = () => {
  const activeJob = useJobStore((s) => s.activeJob);
  const wsConnected = useJobStore((s) => s.wsConnected);
  const view = useUiStore((s) => s.view);
  const t = useT();

  const inSettings = view === 'settings';
  const inHelp = view === 'help';
  const inSubView = inSettings || inHelp;
  const Icon = inSettings ? Settings : inHelp ? HelpCircle : Film;

  return (
    <header className="h-14 border-b border-line bg-sunken px-6 flex items-center justify-between flex-shrink-0">
      <div className="flex items-center gap-3 min-w-0">
        <Icon className="w-4 h-4 text-zinc-400 flex-shrink-0" />
        <span className="text-sm font-semibold text-zinc-200 truncate">
          {inSettings
            ? t('header.settingsTitle')
            : inHelp
            ? t('header.helpTitle')
            : activeJob
            ? activeJob.file_name
            : t('header.setupTitle')}
        </span>
        {!inSubView && activeJob?.video_duration ? (
          <span className="text-xs text-zinc-400 bg-zinc-800/80 px-2 py-0.5 rounded font-mono-time" dir="ltr">
            {formatClock(activeJob.video_duration)}
          </span>
        ) : null}
      </div>

      {!inSubView && activeJob && (
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-xs">
            <span className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-600'}`} />
            <span className="text-zinc-400 text-[11px]">{wsConnected ? t('header.live') : t('header.offline')}</span>
          </div>
          <div className="text-xs font-semibold px-2.5 py-1 rounded-md bg-zinc-800 border border-zinc-700/60 text-zinc-300">
            {t(`status.${activeJob.status}`)}
          </div>
        </div>
      )}
    </header>
  );
};
