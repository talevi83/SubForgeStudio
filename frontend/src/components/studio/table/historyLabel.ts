import type { MessageKey } from '../../../i18n';
import type { HistoryEntry } from '../../../types';

type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string;

const ACTION_KEYS: Record<string, MessageKey> = {
  original: 'history.original',
  edit: 'history.edit',
  edit_timing: 'history.edit_timing',
  bulk_edit: 'history.bulk_edit',
  insert: 'history.insert',
  delete: 'history.delete',
  delete_many: 'history.delete_many',
  move: 'history.move',
  retime: 'history.retime',
  external: 'history.external',
};

/** The user-facing label of a history step ("Edit line #12", "Delete 3 lines", ...). */
export function historyLabel(t: Translate, entry: HistoryEntry): string {
  const d = entry.details || {};
  const vars: Record<string, string | number> = {};
  for (const k of ['index', 'count', 'from', 'to'] as const) {
    if (d[k] !== undefined) vars[k] = d[k] as number;
  }
  if (entry.action === 'translate') {
    if (d.scope === 'rows') return t('history.translate_rows', vars);
    return t(d.scope === 'failed' ? 'history.translate_failed' : 'history.translate_all');
  }
  return t(ACTION_KEYS[entry.action] ?? 'history.unknown', vars);
}
