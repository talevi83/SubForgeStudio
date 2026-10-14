import { create } from 'zustand';
import { api } from '../services/api';
import type { AppSettings, CatalogModel } from '../types';

interface SettingsState {
  settings: AppSettings | null;
  catalog: CatalogModel[];
  pricingChecked: string;
  pricingSource: string;
  loaded: boolean;
  error: string | null;
  load: () => Promise<void>;
  save: (settings: AppSettings) => Promise<boolean>;
}

/** Server-side AI settings (models, thinking levels) and the priced model catalog. */
export const useSettingsStore = create<SettingsState>((set) => ({
  settings: null,
  catalog: [],
  pricingChecked: '',
  pricingSource: '',
  loaded: false,
  error: null,
  load: async () => {
    try {
      const res = await api.getSettings();
      set({
        settings: res.settings,
        catalog: res.catalog,
        pricingChecked: res.pricing_checked,
        pricingSource: res.pricing_source,
        loaded: true,
        error: null,
      });
    } catch (e: any) {
      set({ error: e.message, loaded: true });
    }
  },
  save: async (settings) => {
    try {
      const res = await api.saveSettings(settings);
      set({ settings: res.settings, catalog: res.catalog, error: null });
      return true;
    } catch (e: any) {
      set({ error: e.message });
      return false;
    }
  },
}));
