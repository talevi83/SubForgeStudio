import { create } from 'zustand';

export type Language = 'en' | 'he';
export type Theme = 'dark' | 'light';
export type View = 'studio' | 'settings' | 'help';

const STORAGE_KEY = 'subforge.ui';

function load(): { language?: Language; theme?: Theme } {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
  } catch {
    return {};
  }
}

function save(language: Language, theme: Theme) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ language, theme }));
  } catch {
    /* storage unavailable — preferences just won't persist */
  }
}

/** Reflect language/theme on <html> so CSS (data-theme) and layout direction follow. */
export function applyToDocument(language: Language, theme: Theme) {
  const root = document.documentElement;
  root.lang = language;
  root.dir = language === 'he' ? 'rtl' : 'ltr';
  root.dataset.theme = theme;
}

const saved = load();
const browserLanguage: Language =
  typeof navigator !== 'undefined' && navigator.language?.toLowerCase().startsWith('he') ? 'he' : 'en';

interface UiState {
  language: Language;
  theme: Theme;
  view: View;
  setLanguage: (language: Language) => void;
  setTheme: (theme: Theme) => void;
  setView: (view: View) => void;
}

export const useUiStore = create<UiState>((set, get) => ({
  language: saved.language || browserLanguage,
  theme: saved.theme || 'dark',
  view: 'studio',
  setLanguage: (language) => {
    save(language, get().theme);
    applyToDocument(language, get().theme);
    set({ language });
  },
  setTheme: (theme) => {
    save(get().language, theme);
    applyToDocument(get().language, theme);
    set({ theme });
  },
  setView: (view) => set({ view }),
}));
