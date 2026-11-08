import React, { useState } from 'react';
import { Regex, Replace, Search } from 'lucide-react';
import { useJobStore } from '../../../store/useJobStore';
import { useT } from '../../../i18n';

interface SearchReplaceBarProps {
  searchTerm: string;
  onSearchChange: (value: string) => void;
  useRegex: boolean;
  onToggleRegex: () => void;
  regexError: string | null;
  isLive: boolean;
  /** Shown for a few seconds in the status line after "Replace all" */
  onResult: (message: string) => void;
}

/** Top toolbar row: search (plain or regex), replacement text, target column and "Replace all". */
export const SearchReplaceBar: React.FC<SearchReplaceBarProps> = ({
  searchTerm,
  onSearchChange,
  useRegex,
  onToggleRegex,
  regexError,
  isLive,
  onResult,
}) => {
  const replaceAll = useJobStore((s) => s.replaceAll);
  const t = useT();
  const [replaceTerm, setReplaceTerm] = useState('');
  const [targetColumn, setTargetColumn] = useState<'translated' | 'original'>('translated');

  const handleReplaceAll = async () => {
    if (!searchTerm.trim() || (useRegex && !!regexError)) return;
    const n = await replaceAll(searchTerm, replaceTerm, targetColumn, useRegex);
    onResult(n ? t('table.replaced', { count: n }) : t('table.noMatches'));
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.altKey && e.key.toLowerCase() === 'r') {
      e.preventDefault();
      onToggleRegex();
    }
  };

  return (
    <div className="flex items-center gap-2">
      <div className="relative flex-1">
        <Search className="w-3.5 h-3.5 absolute start-2.5 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none" />
        <input
          type="text"
          placeholder={useRegex ? t('table.searchRegex') : t('table.search')}
          dir="auto"
          value={searchTerm}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={handleSearchKeyDown}
          className={`w-full bg-surface border rounded-lg ps-8 pe-8 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none transition-colors ${
            useRegex && regexError
              ? 'border-rose-500/80 focus:border-rose-500 text-rose-200'
              : 'border-line focus:border-indigo-500'
          }`}
        />
        <button
          type="button"
          onClick={onToggleRegex}
          title={t('table.regexToggle')}
          aria-label={t('table.regexToggle')}
          aria-pressed={useRegex}
          className={`absolute end-1.5 top-1/2 -translate-y-1/2 p-1 rounded transition-colors cursor-pointer flex items-center justify-center ${
            useRegex
              ? 'bg-indigo-600/30 text-indigo-300 border border-indigo-500/50 hover:bg-indigo-600/40'
              : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/60'
          }`}
        >
          <Regex className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="relative flex-1">
        <Replace className="w-3.5 h-3.5 absolute start-2.5 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none" />
        <input
          type="text"
          placeholder={t('table.replace')}
          dir="auto"
          value={replaceTerm}
          onChange={(e) => setReplaceTerm(e.target.value)}
          className="w-full bg-surface border border-line rounded-lg ps-8 pe-3 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
        />
      </div>
      <select
        value={targetColumn}
        onChange={(e) => setTargetColumn(e.target.value as 'translated' | 'original')}
        className="bg-surface border border-line rounded-lg px-2.5 py-1.5 text-xs text-zinc-300 focus:outline-none focus:border-indigo-500"
      >
        <option value="translated">{t('table.inTranslated')}</option>
        <option value="original">{t('table.inOriginal')}</option>
      </select>
      <button
        onClick={handleReplaceAll}
        disabled={!searchTerm.trim() || (useRegex && !!regexError) || isLive}
        className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-xs font-semibold text-zinc-200 transition-colors cursor-pointer"
      >
        {t('table.replaceAll')}
      </button>
    </div>
  );
};
