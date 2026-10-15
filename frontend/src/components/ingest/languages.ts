export const SOURCE_LANGUAGES = [
  { value: 'auto', label: 'Auto-detect' },
  { value: 'en', label: 'English (en)' },
  { value: 'he', label: 'Hebrew (he)' },
  { value: 'ar', label: 'Arabic (ar)' },
  { value: 'ru', label: 'Russian (ru)' },
  { value: 'es', label: 'Spanish (es)' },
  { value: 'fr', label: 'French (fr)' },
  { value: 'de', label: 'German (de)' },
  { value: 'it', label: 'Italian (it)' },
  { value: 'ja', label: 'Japanese (ja)' },
  { value: 'ko', label: 'Korean (ko)' },
  { value: 'zh', label: 'Chinese (zh)' },
];

export const TARGET_LANGUAGES = [
  { value: 'Hebrew', label: 'Hebrew (עברית)' },
  { value: 'English', label: 'English' },
  { value: 'Arabic', label: 'Arabic (العربية)' },
  { value: 'Russian', label: 'Russian (Русский)' },
  { value: 'Spanish', label: 'Spanish (Español)' },
  { value: 'French', label: 'French (Français)' },
  { value: 'German', label: 'German (Deutsch)' },
  { value: 'Japanese', label: 'Japanese (日本語)' },
];

const LANGUAGE_NAMES: Record<string, string> = {
  en: 'English', he: 'Hebrew', ar: 'Arabic', ru: 'Russian', es: 'Spanish', fr: 'French',
  de: 'German', it: 'Italian', ja: 'Japanese', ko: 'Korean', zh: 'Chinese', pt: 'Portuguese',
};

export const languageLabel = (code?: string | null) =>
  code ? LANGUAGE_NAMES[code] || code.toUpperCase() : '';

/** "Hebrew" -> "he" (names as in TARGET_LANGUAGES); a known code is returned as is. */
export const languageCode = (language?: string | null): string | null => {
  if (!language) return null;
  const lower = language.trim().toLowerCase();
  if (LANGUAGE_NAMES[lower]) return lower;
  const hit = Object.entries(LANGUAGE_NAMES).find(([, name]) => name.toLowerCase() === lower);
  return hit ? hit[0] : null;
};

/** Is the subtitle file in the language that is spoken? With "auto", most videos are English. */
export function sameLanguage(fileLanguage: string | null | undefined, spoken: string): boolean {
  const file = languageCode(fileLanguage);
  if (!file) return false;
  return spoken && spoken !== 'auto' ? spoken === file : file === 'en';
}
