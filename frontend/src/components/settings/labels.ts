import type { MessageKey } from '../../i18n';
import type { CatalogModel, ThinkingLevel } from '../../types';

type T = (key: MessageKey, vars?: Record<string, string | number>) => string;

/** "Low", or "Model default (dynamic)" for the default option. */
export function thinkingLabel(t: T, level: ThinkingLevel | string, model?: CatalogModel): string {
  if (level === 'default') {
    const def = model?.default_level;
    const isDynamic = model?.family === '2.5' && def !== 'off';
    const shown = isDynamic ? 'dynamic' : def || 'dynamic';
    return t('settings.thinkingDefault', { level: t(`settings.thinkingLevels.${shown}` as MessageKey) });
  }
  return t(`settings.thinkingLevels.${level}` as MessageKey);
}

export function modelLabel(catalog: CatalogModel[], id: string): string {
  return catalog.find((m) => m.id === id)?.label || id;
}
