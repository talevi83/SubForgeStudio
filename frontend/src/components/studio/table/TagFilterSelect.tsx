import React from 'react';
import { Tag, X } from 'lucide-react';
import { useT } from '../../../i18n';
import type { TagCounts } from './useSegmentFilters';

interface TagFilterSelectProps {
  value: string;
  onChange: (value: string) => void;
  counts: TagCounts;
}

/** Filter rows by segment type: ON_SCREEN, COMMS, FOREIGN or any tagged line. */
export const TagFilterSelect: React.FC<TagFilterSelectProps> = ({ value, onChange, counts }) => {
  const t = useT();
  return (
    <div className="flex items-center gap-1.5 ms-1">
      <div
        className={`flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-xs transition-colors ${
          value === 'ALL'
            ? 'border-line bg-surface/70 text-zinc-400'
            : value === 'ON_SCREEN'
            ? 'border-cyan-500/50 bg-cyan-500/10 text-cyan-300 font-medium'
            : value === 'COMMS'
            ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300 font-medium'
            : value === 'FOREIGN'
            ? 'border-purple-500/50 bg-purple-500/10 text-purple-300 font-medium'
            : 'border-indigo-500/50 bg-indigo-500/10 text-indigo-300 font-medium'
        }`}
      >
        <Tag className="w-3 h-3 opacity-70" />
        <select
          aria-label={t('table.filterByTagLabel')}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="bg-transparent border-0 text-inherit text-[11px] font-inherit focus:outline-none cursor-pointer pr-1"
        >
          <option value="ALL" className="bg-zinc-900 text-zinc-300">{t('table.allTags')}</option>
          {counts.anyTagged > 0 && (
            <option value="TAGGED" className="bg-zinc-900 text-zinc-300">
              {t('table.anyTag', { count: counts.anyTagged })}
            </option>
          )}
          <option value="ON_SCREEN" className="bg-zinc-900 text-zinc-300">
            📺 {t('table.onscreenBadge')} ({counts.onscreen})
          </option>
          <option value="COMMS" className="bg-zinc-900 text-zinc-300">
            📻 {t('table.commsBadge')} ({counts.comms})
          </option>
          <option value="FOREIGN" className="bg-zinc-900 text-zinc-300">
            🌐 {t('table.foreignBadge')} ({counts.foreign})
          </option>
        </select>
        {value !== 'ALL' && (
          <button
            type="button"
            onClick={() => onChange('ALL')}
            title={t('table.clearTagFilter')}
            className="p-0.5 rounded hover:bg-white/10 text-inherit opacity-70 hover:opacity-100 transition-opacity"
          >
            <X className="w-3 h-3" />
          </button>
        )}
      </div>
    </div>
  );
};
