import React, { useEffect, useState } from 'react';
import { api } from '../../services/api';
import type { Glossary, GlossaryCharacter } from '../../types';
import { useT } from '../../i18n';
import { Plus, Trash2 } from 'lucide-react';

interface Props {
  glossary: Glossary | null | undefined;
  profile: string | null | undefined;
  canTranslate: boolean;
  onClose: () => void;
  onSave: (glossary: Glossary, thenTranslate: boolean, profile: string) => Promise<void>;
}

type Term = { source: string; translation: string; notes?: string };

const inputClass =
  'w-full bg-sunken border border-line rounded px-2 py-1 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500';

/** Edit the characters (gender, spelling) and terms the translator was given. */
export const GlossaryDialog: React.FC<Props> = ({ glossary, profile: initialProfile, canTranslate, onClose, onSave }) => {
  const t = useT();
  const [characters, setCharacters] = useState<GlossaryCharacter[]>(() => glossary?.characters ?? []);
  const [terms, setTerms] = useState<Term[]>(() => glossary?.terms ?? []);
  const [register, setRegister] = useState(glossary?.register_notes ?? '');
  const [saving, setSaving] = useState(false);
  const [profile, setProfile] = useState(initialProfile ?? '');
  const [profiles, setProfiles] = useState<string[]>([]);
  useEffect(() => {
    api.listGlossaryProfiles().then(setProfiles).catch(() => setProfiles([]));
  }, []);

  const setChar = (i: number, patch: Partial<GlossaryCharacter>) =>
    setCharacters((list) => list.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const setTerm = (i: number, patch: Partial<Term>) =>
    setTerms((list) => list.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  const submit = async (thenTranslate: boolean) => {
    setSaving(true);
    try {
      await onSave(
        { summary: glossary?.summary ?? '', characters, terms, register_notes: register },
        thenTranslate,
        profile
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl max-h-[85vh] overflow-y-auto bg-surface border border-line rounded-xl p-5 space-y-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-sm font-semibold text-zinc-100">{t('glossary.title')}</h2>
        <p className="text-xs text-zinc-400 leading-relaxed">{t('glossary.help')}</p>

        <section className="space-y-2">
          <h3 className="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold">{t('glossary.characters')}</h3>
          {characters.length === 0 && <p className="text-xs text-zinc-500">{t('glossary.empty')}</p>}
          {characters.map((c, i) => (
            <div key={i} className="grid grid-cols-[1fr_1fr_7rem_1.5fr_auto] gap-2 items-center">
              <input
                value={c.name}
                onChange={(e) => setChar(i, { name: e.target.value })}
                placeholder={t('glossary.name')}
                className={inputClass}
                dir="auto"
              />
              <input
                value={c.target_name ?? ''}
                onChange={(e) => setChar(i, { target_name: e.target.value })}
                placeholder={t('glossary.targetName')}
                className={inputClass}
                dir="auto"
              />
              <select
                value={c.gender || 'unknown'}
                onChange={(e) => setChar(i, { gender: e.target.value })}
                className={inputClass}
                aria-label={t('glossary.gender')}
              >
                <option value="unknown">{t('glossary.unknown')}</option>
                <option value="male">{t('glossary.male')}</option>
                <option value="female">{t('glossary.female')}</option>
                <option value="mixed group">{t('glossary.group')}</option>
              </select>
              <input
                value={c.notes ?? ''}
                onChange={(e) => setChar(i, { notes: e.target.value })}
                placeholder={t('glossary.notes')}
                className={inputClass}
                dir="auto"
              />
              <button
                onClick={() => setCharacters((l) => l.filter((_, j) => j !== i))}
                title={t('glossary.remove')}
                className="p-1 text-zinc-500 hover:text-rose-400"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
          <button
            onClick={() => setCharacters((l) => [...l, { name: '', target_name: '', gender: 'unknown', notes: '' }])}
            className="flex items-center gap-1 text-xs text-indigo-300 hover:text-indigo-200"
          >
            <Plus className="w-3 h-3" />
            {t('glossary.add')}
          </button>
        </section>

        <section className="space-y-2">
          <h3 className="text-[11px] uppercase tracking-wider text-zinc-500 font-semibold">{t('glossary.terms')}</h3>
          {terms.map((term, i) => (
            <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2 items-center">
              <input
                value={term.source}
                onChange={(e) => setTerm(i, { source: e.target.value })}
                placeholder={t('glossary.source')}
                className={inputClass}
                dir="auto"
              />
              <input
                value={term.translation}
                onChange={(e) => setTerm(i, { translation: e.target.value })}
                placeholder={t('glossary.translation')}
                className={inputClass}
                dir="auto"
              />
              <button
                onClick={() => setTerms((l) => l.filter((_, j) => j !== i))}
                title={t('glossary.remove')}
                className="p-1 text-zinc-500 hover:text-rose-400"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
          <button
            onClick={() => setTerms((l) => [...l, { source: '', translation: '' }])}
            className="flex items-center gap-1 text-xs text-indigo-300 hover:text-indigo-200"
          >
            <Plus className="w-3 h-3" />
            {t('glossary.add')}
          </button>
        </section>

        <label className="block space-y-1 text-[11px] text-zinc-400">
          <span>{t('glossary.profile')}</span>
          <input
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
            list="glossary-profiles"
            placeholder={t('glossary.profilePlaceholder')}
            className={inputClass}
            dir="auto"
            data-testid="glossary-profile"
          />
          <datalist id="glossary-profiles">
            {profiles.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
          <span className="block text-zinc-500">{t('glossary.profileHelp')}</span>
        </label>

        <label className="block space-y-1 text-[11px] text-zinc-400">
          <span>{t('glossary.registerNotes')}</span>
          <input value={register} onChange={(e) => setRegister(e.target.value)} className={inputClass} dir="auto" />
        </label>

        <div className="flex justify-end gap-2 pt-1">
          <button onClick={onClose} className="px-3 py-1.5 rounded-lg text-xs text-zinc-300 border border-line hover:text-white">
            {t('glossary.close')}
          </button>
          <button
            onClick={() => void submit(false)}
            disabled={saving}
            className="px-3 py-1.5 rounded-lg text-xs text-zinc-200 border border-zinc-700 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50"
            data-testid="glossary-save"
          >
            {t('glossary.save')}
          </button>
          <button
            onClick={() => void submit(true)}
            disabled={saving || !canTranslate}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white"
          >
            {t('glossary.saveAndTranslate')}
          </button>
        </div>
      </div>
    </div>
  );
};
