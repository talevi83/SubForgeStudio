import React from 'react';
import { X } from 'lucide-react';
import { useT } from '../../../i18n';
import type { QAIssue } from '../../../types';

interface QaPanelProps {
  issues: QAIssue[] | null;
  error: string | null;
  canSelect: boolean;
  /** Check the rows that have an issue */
  onSelectRows: () => void;
  /** Scroll to and select the line of one issue */
  onJump: (segmentId: number) => void;
  onClose: () => void;
}

/** Result of the quality check: summary, "select these rows", and a clickable list of issues. */
export const QaPanel: React.FC<QaPanelProps> = ({ issues, error, canSelect, onSelectRows, onJump, onClose }) => {
  const t = useT();
  return (
    <div className="px-4 py-2 border-y border-line bg-zinc-900/60 text-xs" data-testid="qa-panel">
      <div className="flex items-center gap-3">
        <span className={error ? 'text-red-300' : issues && issues.length ? 'text-amber-300' : 'text-emerald-300'}>
          {error ?? (issues && issues.length ? t('table.qaFound', { count: issues.length }) : t('table.qaClean'))}
        </span>
        {canSelect && issues && issues.length > 0 && (
          <button onClick={onSelectRows} className="text-indigo-300 hover:text-white" data-testid="qa-select">
            {t('table.qaSelect')}
          </button>
        )}
        <button onClick={onClose} className="ml-auto text-zinc-500 hover:text-white">
          <X className="w-3 h-3" />
        </button>
      </div>
      {issues && issues.length > 0 && (
        <ul className="mt-1 max-h-32 overflow-y-auto space-y-0.5">
          {issues.map((i, k) => (
            <li key={`${i.segment_id}-${k}`}>
              <button onClick={() => onJump(i.segment_id)} className="text-zinc-300 hover:text-white text-start">
                <span className="text-zinc-500">#{i.index}</span> {i.message}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};
