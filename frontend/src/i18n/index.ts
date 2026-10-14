import { useCallback } from 'react';
import { useUiStore } from '../store/useUiStore';
import type { Language } from '../store/useUiStore';
import { en } from './en';
import { he } from './he';

export type Messages = typeof en;

// "a.b.c" paths of every leaf string, so t('typo.key') fails the TypeScript build
type Leaves<T, P extends string = ''> = {
  [K in keyof T & string]: T[K] extends string ? `${P}${K}` : Leaves<T[K], `${P}${K}.`>;
}[keyof T & string];
export type MessageKey = Leaves<Messages>;

const dictionaries: Record<Language, Messages> = { en, he };

export function translate(language: Language, key: MessageKey, vars?: Record<string, string | number>): string {
  const lookup = (dict: unknown) =>
    key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], dict);
  let text = (lookup(dictionaries[language]) ?? lookup(en) ?? key) as string;
  if (vars) {
    for (const [name, value] of Object.entries(vars)) text = text.split(`{${name}}`).join(String(value));
  }
  return text;
}

/** const t = useT(); t('sidebar.newProject') */
export function useT() {
  const language = useUiStore((s) => s.language);
  return useCallback(
    (key: MessageKey, vars?: Record<string, string | number>) => translate(language, key, vars),
    [language]
  );
}

export function formatUsd(value: number | null | undefined, language: Language): string {
  if (value === null || value === undefined) return '—';
  // AI costs are often fractions of a cent: show up to 4 decimals below $1
  return new Intl.NumberFormat(language === 'he' ? 'he-IL' : 'en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: Math.abs(value) < 1 ? 4 : 2,
  }).format(value);
}

/** A price per 1M tokens: keeps Google's exact rates such as $1.875 (Flex = half of $3.75). */
export function formatRate(value: number, language: Language): string {
  return new Intl.NumberFormat(language === 'he' ? 'he-IL' : 'en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(value);
}

export function formatNumber(value: number, language: Language): string {
  return new Intl.NumberFormat(language === 'he' ? 'he-IL' : 'en-US').format(Math.round(value));
}
