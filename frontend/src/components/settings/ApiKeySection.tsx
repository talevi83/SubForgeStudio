import React, { useEffect, useState } from 'react';
import { useJobStore } from '../../store/useJobStore';
import { useT } from '../../i18n';
import { api } from '../../services/api';
import {
  Key,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  CheckCircle2,
  AlertTriangle,
  Eye,
  EyeOff,
  Save,
  Loader2,
  Check,
  Trash2,
} from 'lucide-react';

const AI_STUDIO_KEY_URL = 'https://aistudio.google.com/app/apikey';

export const ApiKeySection: React.FC = () => {
  const t = useT();
  const systemInfo = useJobStore((s) => s.systemInfo);
  const fetchSystemInfo = useJobStore((s) => s.fetchSystemInfo);

  const isConfigured = !!systemInfo?.is_gemini_configured;
  const maskedKey = systemInfo?.masked_api_key || '••••••••';

  // Collapse by default if a key is already configured; expand if missing
  const [expanded, setExpanded] = useState(!isConfigured);
  const [initializedCollapse, setInitializedCollapse] = useState(false);

  useEffect(() => {
    if (systemInfo && !initializedCollapse) {
      setExpanded(!systemInfo.is_gemini_configured);
      setInitializedCollapse(true);
    }
  }, [systemInfo, initializedCollapse]);

  const [keyInput, setKeyInput] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async (newKey: string) => {
    setSaving(true);
    setError(null);
    try {
      await api.saveApiKey(newKey.trim());
      await fetchSystemInfo();
      setKeyInput('');
      setJustSaved(true);
      setTimeout(() => setJustSaved(false), 2500);
    } catch (e: any) {
      setError(e.message || 'Failed to save API key');
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      className="bg-surface border border-line rounded-xl shadow-lg overflow-hidden"
      data-testid="settings-apikey-section"
    >
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        data-testid="settings-apikey-toggle"
        className="w-full p-5 flex items-center justify-between gap-3 text-start hover:bg-zinc-800/30 transition-colors cursor-pointer"
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <Key className="w-4 h-4 text-indigo-400 flex-shrink-0" />
          <h2 className="text-sm font-semibold text-zinc-100">{t('settings.apiKey.title')}</h2>
          <span
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border ${
              isConfigured
                ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
                : 'bg-amber-500/15 text-amber-300 border-amber-500/30'
            }`}
          >
            {isConfigured ? (
              <>
                <CheckCircle2 className="w-3 h-3" />
                <span dir="ltr">{t('settings.apiKey.statusActive', { masked: maskedKey })}</span>
              </>
            ) : (
              <>
                <AlertTriangle className="w-3 h-3" />
                <span>{t('settings.apiKey.statusMissing')}</span>
              </>
            )}
          </span>
        </div>

        <div className="flex items-center gap-2 text-zinc-400">
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </div>
      </button>

      {expanded && (
        <div className="px-5 pb-5 pt-1 border-t border-line space-y-4 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
            <p className="text-[11px] text-zinc-400 leading-relaxed">{t('settings.apiKey.subtitle')}</p>
            <a
              href={AI_STUDIO_KEY_URL}
              target="_blank"
              rel="noreferrer"
              data-testid="settings-apikey-link"
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-indigo-500/10 hover:bg-indigo-500/20 border border-indigo-500/30 text-indigo-300 font-medium text-[11px] transition-colors"
            >
              <ExternalLink className="w-3 h-3" />
              <span>{t('settings.apiKey.getLink')}</span>
            </a>
          </div>

          <div className="space-y-2">
            <label className="block text-[11px] font-medium text-zinc-300">
              {t('settings.apiKey.inputLabel')}
            </label>
            <div className="flex flex-wrap sm:flex-nowrap items-center gap-2">
              <div className="relative flex-1 min-w-[220px]">
                <input
                  type={showSecret ? 'text' : 'password'}
                  dir="ltr"
                  value={keyInput}
                  onChange={(e) => setKeyInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && keyInput.trim()) {
                      handleSave(keyInput);
                    }
                  }}
                  placeholder={isConfigured ? maskedKey : t('settings.apiKey.placeholder')}
                  data-testid="settings-apikey-input"
                  className="w-full bg-sunken border border-line rounded-lg ps-3 pe-9 py-2 text-xs text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 font-mono"
                />
                <button
                  type="button"
                  onClick={() => setShowSecret((s) => !s)}
                  title={showSecret ? t('settings.apiKey.hideKey') : t('settings.apiKey.showKey')}
                  className="absolute inset-y-0 end-2 flex items-center text-zinc-400 hover:text-zinc-200 cursor-pointer"
                >
                  {showSecret ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                </button>
              </div>

              <button
                type="button"
                onClick={() => handleSave(keyInput)}
                disabled={saving || !keyInput.trim()}
                data-testid="settings-apikey-save"
                className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-xs font-semibold text-white flex items-center gap-1.5 cursor-pointer transition-colors whitespace-nowrap"
              >
                {saving ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : justSaved ? (
                  <Check className="w-3.5 h-3.5" />
                ) : (
                  <Save className="w-3.5 h-3.5" />
                )}
                <span>{saving ? t('settings.apiKey.saving') : justSaved ? t('settings.apiKey.saved') : t('settings.apiKey.saveBtn')}</span>
              </button>

              {isConfigured && (
                <button
                  type="button"
                  onClick={() => handleSave('')}
                  disabled={saving}
                  title={t('settings.apiKey.clearBtn')}
                  data-testid="settings-apikey-clear"
                  className="px-3 py-2 rounded-lg bg-zinc-800 hover:bg-rose-950/60 hover:text-rose-300 border border-line text-xs text-zinc-400 flex items-center gap-1.5 cursor-pointer transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">{t('settings.apiKey.clearBtn')}</span>
                </button>
              )}
            </div>
            {error && <p className="text-[11px] text-rose-400">{error}</p>}
          </div>
        </div>
      )}
    </section>
  );
};

export const FirstRunApiKeyModal: React.FC = () => {
  const t = useT();
  const systemInfo = useJobStore((s) => s.systemInfo);
  const fetchSystemInfo = useJobStore((s) => s.fetchSystemInfo);

  const [dismissed, setDismissed] = useState(false);
  const [keyInput, setKeyInput] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Only show once systemInfo has loaded and we know no API key is configured
  if (!systemInfo || systemInfo.is_gemini_configured || dismissed) {
    return null;
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!keyInput.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await api.saveApiKey(keyInput.trim());
      await fetchSystemInfo();
      setDismissed(true);
    } catch (err: any) {
      setError(err.message || 'Could not save API key');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4"
      data-testid="first-run-apikey-modal"
    >
      <div className="bg-surface border border-line rounded-2xl max-w-lg w-full p-6 shadow-2xl space-y-5">
        <div className="flex items-start gap-3.5">
          <div className="w-10 h-10 rounded-xl bg-indigo-500/15 border border-indigo-500/30 flex items-center justify-center flex-shrink-0">
            <Key className="w-5 h-5 text-indigo-400" />
          </div>
          <div className="space-y-1">
            <h2 className="text-base font-bold text-zinc-50">{t('settings.apiKey.modalTitle')}</h2>
            <p className="text-xs text-zinc-400 leading-relaxed">{t('settings.apiKey.modalSubtitle')}</p>
          </div>
        </div>

        <div className="p-3.5 rounded-xl bg-sunken border border-line space-y-2.5 text-xs">
          <div className="font-semibold text-zinc-200">{t('settings.apiKey.modalWhereTitle')}</div>
          <p className="text-[11px] text-zinc-400 leading-relaxed">{t('settings.apiKey.modalWhereText')}</p>
          <a
            href={AI_STUDIO_KEY_URL}
            target="_blank"
            rel="noreferrer"
            data-testid="modal-apikey-link"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/40 text-indigo-300 font-semibold text-xs transition-colors"
          >
            <ExternalLink className="w-3.5 h-3.5" />
            <span>{t('settings.apiKey.getLink')}</span>
          </a>
        </div>

        <form onSubmit={handleSave} className="space-y-4">
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-zinc-300">
              {t('settings.apiKey.inputLabel')}
            </label>
            <div className="relative">
              <input
                type={showSecret ? 'text' : 'password'}
                dir="ltr"
                autoFocus
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
                placeholder={t('settings.apiKey.placeholder')}
                data-testid="modal-apikey-input"
                className="w-full bg-sunken border border-line rounded-xl ps-3.5 pe-10 py-2.5 text-xs text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 font-mono"
              />
              <button
                type="button"
                onClick={() => setShowSecret((s) => !s)}
                title={showSecret ? t('settings.apiKey.hideKey') : t('settings.apiKey.showKey')}
                className="absolute inset-y-0 end-3 flex items-center text-zinc-400 hover:text-zinc-200 cursor-pointer"
              >
                {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            {error && <p className="text-[11px] text-rose-400">{error}</p>}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2.5 pt-1">
            <button
              type="button"
              onClick={() => setDismissed(true)}
              data-testid="modal-apikey-skip"
              className="px-3.5 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-medium text-zinc-300 cursor-pointer transition-colors"
            >
              {t('settings.apiKey.modalSkip')}
            </button>
            <button
              type="submit"
              disabled={saving || !keyInput.trim()}
              data-testid="modal-apikey-save"
              className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-xs font-semibold text-white flex items-center gap-1.5 cursor-pointer transition-colors shadow-lg shadow-indigo-600/25"
            >
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              <span>{saving ? t('settings.apiKey.saving') : t('settings.apiKey.modalSaveAndContinue')}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
