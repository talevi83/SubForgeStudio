import React from 'react';
import { useJobStore } from '../../store/useJobStore';
import { Film } from 'lucide-react';

export const Header: React.FC = () => {
  const { activeJob, wsConnected } = useJobStore();

  const formatSeconds = (sec?: number | null) => {
    if (!sec) return '00:00';
    const mins = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${mins.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  return (
    <header className="h-14 border-b border-[#27272a] bg-[#121214] px-6 flex items-center justify-between flex-shrink-0">
      <div className="flex items-center space-x-3">
        <Film className="w-4 h-4 text-zinc-400" />
        <div className="flex items-center space-x-2">
          <span className="text-sm font-semibold text-zinc-200">
            {activeJob ? activeJob.file_name : 'Pipeline Setup & Media Ingest'}
          </span>
          {activeJob && activeJob.video_duration && (
            <span className="text-xs text-zinc-400 bg-zinc-800/80 px-2 py-0.5 rounded font-mono-time">
              {formatSeconds(activeJob.video_duration)}
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center space-x-4">
        {/* Realtime WebSocket Live Connection Pill */}
        {activeJob && (
          <div className="flex items-center space-x-2 text-xs">
            <span
              className={`w-2 h-2 rounded-full ${
                wsConnected ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-600'
              }`}
            />
            <span className="text-zinc-400 text-[11px]">
              {wsConnected ? 'Live Synchronized' : 'Offline'}
            </span>
          </div>
        )}

        {activeJob && (
          <div className="text-xs font-semibold px-2.5 py-1 rounded-md bg-zinc-800 border border-zinc-700/60 text-zinc-300">
            {activeJob.status}
          </div>
        )}
      </div>
    </header>
  );
};
