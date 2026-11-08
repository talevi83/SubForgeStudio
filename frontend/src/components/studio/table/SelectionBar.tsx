import React from 'react';
import { ChevronsDown, ChevronsUp, Trash2 } from 'lucide-react';
import { useT } from '../../../i18n';
import type { Gender, ThinkingLevel } from '../../../types';
import type { CheckedRangeBounds } from './useRowSelection';

export interface RowTranslateOptions {
  speaker?: Gender | null;
  addressee?: Gender | null;
  hint?: string;
  remember?: boolean;
  model?: string;
  thinking?: ThinkingLevel;
}

interface SelectionBarProps {
  count: number;
  bounds: CheckedRangeBounds | null;
  onSelectToEnd: (id: number) => void;
  onSelectFromStart: (id: number) => void;
  canTranslate: boolean;
  onTranslate: (opts: RowTranslateOptions) => void;
  onOpenNote: () => void;
  canDelete: boolean;
  onDelete: () => void;
  onClear: () => void;
}

/** Bar shown while rows are checked: range expansion, quick re-translations, delete, clear. */
export const SelectionBar: React.FC<SelectionBarProps> = ({
  count,
  bounds,
  onSelectToEnd,
  onSelectFromStart,
  canTranslate,
  onTranslate,
  onOpenNote,
  canDelete,
  onDelete,
  onClear,
}) => {
  const t = useT();
  return (
    <div
      className="flex flex-wrap items-center gap-2 px-4 py-2 bg-indigo-950/40 border-y border-indigo-500/30 text-xs"
      data-testid="rows-bar"
    >
      <span className="text-indigo-200 font-medium">{t('table.selected', { count })}</span>

      {/* Fast 1-click range expansion: from checked row to end, or from start to checked row */}
      {bounds?.canExpandToEnd && bounds.firstSeg.id && (
        <button
          type="button"
          onClick={() => onSelectToEnd(bounds.firstSeg.id!)}
          data-testid="select-checked-to-end"
          className="flex items-center gap-1 px-2 py-1 rounded border border-indigo-400/40 bg-indigo-500/15 text-indigo-200 hover:bg-indigo-500/25 cursor-pointer"
        >
          <ChevronsDown className="w-3 h-3" />
          {t('table.selectFromToEnd', {
            index: bounds.firstSeg.index,
            count: bounds.toEndCount,
          })}
        </button>
      )}
      {bounds?.canExpandFromStart && bounds.lastSeg.id && (
        <button
          type="button"
          onClick={() => onSelectFromStart(bounds.lastSeg.id!)}
          data-testid="select-start-to-checked"
          className="flex items-center gap-1 px-2 py-1 rounded border border-line text-zinc-300 hover:border-indigo-500/60 hover:text-white cursor-pointer"
        >
          <ChevronsUp className="w-3 h-3" />
          {t('table.selectFromStartTo', {
            index: bounds.lastSeg.index,
            count: bounds.fromStartCount,
          })}
        </button>
      )}

      {canTranslate && (
        <>
          <button
            onClick={() => onTranslate({ addressee: 'female' })}
            className="px-2 py-1 rounded border border-line text-zinc-200 hover:border-indigo-500/60"
            data-testid="rows-to-woman"
          >
            {t('table.toWoman')}
          </button>
          <button
            onClick={() => onTranslate({ addressee: 'male' })}
            className="px-2 py-1 rounded border border-line text-zinc-200 hover:border-indigo-500/60"
          >
            {t('table.toMan')}
          </button>
          <button
            onClick={() => onTranslate({ speaker: 'female' })}
            className="px-2 py-1 rounded border border-line text-zinc-200 hover:border-indigo-500/60"
          >
            {t('table.speakerWoman')}
          </button>
          <button
            onClick={() => onTranslate({ addressee: 'group' })}
            className="px-2 py-1 rounded border border-line text-zinc-200 hover:border-indigo-500/60"
          >
            {t('table.toGroup')}
          </button>
          <button
            onClick={onOpenNote}
            data-testid="rows-note"
            className="px-2 py-1 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-500/10"
          >
            {t('table.retranslateRows')}
          </button>
        </>
      )}
      {canDelete && (
        <button
          onClick={onDelete}
          data-testid="rows-delete"
          className="flex items-center gap-1 px-2 py-1 rounded border border-rose-500/40 text-rose-300 hover:bg-rose-500/15"
        >
          <Trash2 className="w-3 h-3" />
          {t('table.deleteSelectedRows', { count })}
        </button>
      )}
      <button onClick={onClear} className="ms-auto text-zinc-400 hover:text-white">
        {t('table.clearSelection')}
      </button>
    </div>
  );
};
