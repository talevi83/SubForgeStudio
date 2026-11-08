/**
 * The app used to be called SubForge Studio and kept its preferences under `subforge.*`.
 * Read the `subbuild.*` key; if it is missing, adopt the old value once so users keep
 * their language, theme and remembered pipeline settings after the rename.
 */
export function getItemWithLegacy(key: string): string | null {
  const current = localStorage.getItem(key);
  if (current !== null) return current;
  const legacy = localStorage.getItem(key.replace(/^subbuild\./, 'subforge.'));
  if (legacy !== null) {
    try {
      localStorage.setItem(key, legacy);
    } catch {
      /* storage full or unavailable — the value is still used for this session */
    }
  }
  return legacy;
}
