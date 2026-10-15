import React, { memo, useEffect, useRef, useState } from 'react';
import type { SegmentPatch, SubtitleSegment } from '../../types';
import { CpsBadge } from './CpsBadge';
import { formatTimecode } from '../../utils/time';
import { useT } from '../../i18n';
import { AlertTriangle, GripVertical, Minus, Plus } from 'lucide-react';

const SAVE_DELAY_MS = 600;

interface SubtitleRowProps {
  seg: SubtitleSegment;
  isActive: boolean;
  isSelected: boolean;
  isRtl: boolean;
  translationEnabled: boolean;
  maxCps: number;
  onSelect: (seg: SubtitleSegment) => void;
  onSave: (segmentId: number, patch: SegmentPatch) => void;
  rowRef?: React.Ref<HTMLTableRowElement>;
  /** Row selection (for re-translating a few rows); omitted while the job is running */
  checked?: boolean;
  onCheck?: (segmentId: number, shift: boolean) => void;
  /** "+" under the row number: add a line right after this one; omitted while the job runs */
  onInsertAfter?: (seg: SubtitleSegment) => void;
  /** Drag-to-move support; omitted while the table is filtered or still being generated. */
  dnd?: {
    isDragging: boolean;
    drop: 'before' | 'after' | null;
    onDragStart: (segmentId: number) => void;
    onDragOver: (segmentId: number) => void;
    onDrop: (segmentId: number) => void;
    onDragEnd: () => void;
  };
}

type TextField = 'original_text' | 'translated_text';

/**
 * A text cell that edits a local draft and saves after a short pause (or on blur),
 * instead of sending a request — and re-rendering the whole table — on every keystroke.
 */
const EditableText: React.FC<{
  value: string;
  disabled: boolean;
  dir: 'rtl' | 'ltr' | 'auto';
  placeholder?: string;
  className: string;
  onCommit: (value: string) => void;
}> = ({ value, disabled, dir, placeholder, className, onCommit }) => {
  const [draft, setDraft] = useState(value);
  const dirty = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef(value);
  const draftRef = useRef(value);
  const commitRef = useRef(onCommit);
  useEffect(() => {
    commitRef.current = onCommit;
  });

  // Accept outside changes (Replace All, translation arriving) unless the user is mid-edit
  useEffect(() => {
    latest.current = value;
    if (!dirty.current && draftRef.current !== value) {
      draftRef.current = value;
      setDraft(value); // eslint-disable-line react-hooks/set-state-in-effect
    }
  }, [value]);

  const flush = () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    if (dirty.current) {
      dirty.current = false;
      if (draftRef.current !== latest.current) commitRef.current(draftRef.current);
    }
  };

  // Save pending edits if the row unmounts (e.g. search filter changes)
  useEffect(() => () => flush(), []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <textarea
      rows={2}
      dir={dir}
      value={draft}
      disabled={disabled}
      placeholder={placeholder}
      onChange={(e) => {
        dirty.current = true;
        draftRef.current = e.target.value;
        setDraft(e.target.value);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(flush, SAVE_DELAY_MS);
      }}
      onBlur={flush}
      className={className}
    />
  );
};

const textareaClass =
  'w-full bg-transparent hover:bg-sunken focus:bg-sunken p-1.5 rounded border hover:border-line focus:border-indigo-500/60 focus:outline-none resize-none text-xs leading-relaxed transition-colors disabled:cursor-default';

const SubtitleRowInner: React.FC<SubtitleRowProps> = ({
  seg,
  isActive,
  isSelected,
  isRtl,
  translationEnabled,
  maxCps,
  onSelect,
  onSave,
  rowRef,
  checked,
  onCheck,
  onInsertAfter,
  dnd,
}) => {
  const t = useT();
  const editable = !!seg.id;

  const commit = (field: TextField) => (value: string) => {
    if (seg.id) onSave(seg.id, { [field]: value });
  };

  const nudge = (field: 'start_time' | 'end_time', delta: number) => {
    if (!seg.id) return;
    const next = Math.max(0, Number((seg[field] + delta).toFixed(3)));
    if (field === 'start_time' && next >= seg.end_time) return;
    if (field === 'end_time' && next <= seg.start_time) return;
    onSave(seg.id, { [field]: next });
  };

  const nudgeButtons = (field: 'start_time' | 'end_time') =>
    editable && (
      <div className="opacity-0 group-hover:opacity-100 flex items-center ms-1">
        <button onClick={() => nudge(field, -0.1)} className="p-0.5 hover:text-white text-zinc-400" title="-100ms">
          <Minus className="w-2.5 h-2.5" />
        </button>
        <button onClick={() => nudge(field, 0.1)} className="p-0.5 hover:text-white text-zinc-400" title="+100ms">
          <Plus className="w-2.5 h-2.5" />
        </button>
      </div>
    );

  const failed = !!seg.translation_failed;

  return (
    <tr
      ref={rowRef}
      onClick={() => onSelect(seg)}
      onDragOver={
        dnd && seg.id
          ? (e) => {
              e.preventDefault();
              dnd.onDragOver(seg.id!);
            }
          : undefined
      }
      onDrop={
        dnd && seg.id
          ? (e) => {
              e.preventDefault();
              dnd.onDrop(seg.id!);
            }
          : undefined
      }
      className={`transition-colors cursor-pointer group ${
        dnd?.isDragging ? 'opacity-40 ' : ''
      }${dnd?.drop === 'before' ? 'shadow-[inset_0_2px_0_0_rgb(99,102,241)] ' : ''}${
        dnd?.drop === 'after' ? 'shadow-[inset_0_-2px_0_0_rgb(99,102,241)] ' : ''
      }${
        isActive
          ? 'bg-indigo-950/40 border-s-2 border-indigo-500'
          : isSelected
          ? 'bg-zinc-800/60'
          : 'hover:bg-zinc-800/30'
      }`}
    >
      <td className="py-2 px-3 text-center text-zinc-500 font-mono text-[11px] select-none align-top">
        {dnd && seg.id && (
          <span
            draggable
            onDragStart={(e) => {
              e.dataTransfer.effectAllowed = 'move';
              e.dataTransfer.setData('text/plain', String(seg.id));
              const tr = (e.currentTarget as HTMLElement).closest('tr');
              if (tr) e.dataTransfer.setDragImage(tr, 16, 16);
              dnd.onDragStart(seg.id!);
            }}
            onDragEnd={dnd.onDragEnd}
            onClick={(e) => e.stopPropagation()}
            title={t('table.dragHandle')}
            className="mx-auto mb-0.5 block w-fit cursor-grab active:cursor-grabbing text-zinc-600 hover:text-zinc-300 opacity-0 group-hover:opacity-100"
          >
            <GripVertical className="w-3.5 h-3.5" />
          </span>
        )}
        {onCheck && seg.id && (
          <input
            type="checkbox"
            checked={!!checked}
            onChange={() => undefined}
            onClick={(e) => {
              e.stopPropagation();
              onCheck(seg.id!, e.shiftKey);
            }}
            title={t('table.selectRow')}
            aria-label={t('table.selectRow')}
            className={`mx-auto mb-0.5 block w-3 h-3 rounded border-zinc-700 text-indigo-600 bg-zinc-800 cursor-pointer ${
              checked ? '' : 'opacity-0 group-hover:opacity-100'
            }`}
          />
        )}
        <div>{seg.index}</div>
        {onInsertAfter && seg.id && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onInsertAfter(seg);
            }}
            title={t('table.insertAfter', { index: seg.index })}
            aria-label={t('table.insertAfter', { index: seg.index })}
            data-testid={`insert-after-${seg.index}`}
            className="mx-auto mt-0.5 flex h-4 w-4 items-center justify-center rounded text-zinc-500 hover:text-white hover:bg-indigo-600 opacity-0 group-hover:opacity-100 focus:opacity-100 cursor-pointer"
          >
            <Plus className="w-3 h-3" />
          </button>
        )}
        {seg.segment_type === 'ON_SCREEN' && (
          <span
            className="mt-1 inline-block whitespace-nowrap px-1 py-0.5 rounded text-[9px] font-sans font-semibold bg-cyan-500/15 text-cyan-300 border border-cyan-500/30"
            title={t('table.onscreenTitle')}
          >
            📺 {t('table.onscreenBadge')}
          </span>
        )}
        {seg.segment_type === 'FOREIGN' && (
          <span
            className="mt-1 inline-block whitespace-nowrap px-1 py-0.5 rounded text-[9px] font-sans font-semibold bg-violet-500/15 text-violet-300 border border-violet-500/30"
            title={t('table.foreignTitle')}
          >
            🌐 {t('table.foreignBadge')}
          </span>
        )}
        {seg.segment_type === 'COMMS' && (
          <span
            className="mt-1 inline-block whitespace-nowrap px-1 py-0.5 rounded text-[9px] font-sans font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
            title={t('table.commsTitle')}
          >
            📻 {t('table.commsBadge')}
          </span>
        )}
      </td>

      <td className="py-2 px-3 font-mono-time text-zinc-400 text-[11px] whitespace-nowrap" onClick={(e) => e.stopPropagation()} dir="ltr">
        <div className="space-y-1">
          <div className="flex items-center gap-1">
            <span className="text-zinc-500 text-[9px]">{t('table.in')}</span>
            <span>{formatTimecode(seg.start_time)}</span>
            {nudgeButtons('start_time')}
          </div>
          <div className="flex items-center gap-1">
            <span className="text-zinc-500 text-[9px]">{t('table.out')}</span>
            <span>{formatTimecode(seg.end_time)}</span>
            {nudgeButtons('end_time')}
          </div>
        </div>
      </td>

      <td className="py-2 px-3" onClick={(e) => e.stopPropagation()}>
        <EditableText
          value={seg.original_text}
          disabled={!editable}
          dir="auto"
          className={`${textareaClass} border-transparent text-zinc-200`}
          onCommit={commit('original_text')}
        />
      </td>

      <td className="py-2 px-3 relative" onClick={(e) => e.stopPropagation()}>
        <EditableText
          value={seg.translated_text || ''}
          disabled={!editable}
          dir={isRtl ? 'rtl' : 'ltr'}
          placeholder={failed ? t('table.failedPlaceholder') : translationEnabled ? t('table.translatingPlaceholder') : '—'}
          className={`${textareaClass} ${
            failed ? 'border-rose-500/50 placeholder-rose-400/70' : 'border-transparent'
          } text-amber-300 font-rtl-hebrew`}
          onCommit={commit('translated_text')}
        />
        {failed && (
          <AlertTriangle className="w-3 h-3 text-rose-400 absolute top-3 end-4" aria-label={t('table.failedTitle')} />
        )}
      </td>

      <td className="py-2 px-3 text-center select-none">
        <CpsBadge cps={seg.cps} max={maxCps} />
      </td>
    </tr>
  );
};

// Re-render a row only when its own data or highlight changes
export const SubtitleRow = memo(SubtitleRowInner);
