import { useEffect } from 'react';
import { useJobStore } from '../../../store/useJobStore';
import { isTypingTarget } from '../../../utils/time';

/**
 * Ctrl+Z undo, Ctrl+Y / Ctrl+Shift+Z redo (Cmd on macOS). Uses the physical key, so it also works
 * with a Hebrew keyboard layout. Inside a text field the browser's own text undo is left alone.
 */
export function useHistoryShortcuts(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || isTypingTarget(e.target)) return;
      const isZ = e.code === 'KeyZ' || e.key.toLowerCase() === 'z';
      const isY = e.code === 'KeyY' || e.key.toLowerCase() === 'y';
      if (!isZ && !isY) return;
      const state = useJobStore.getState();
      if (state.insertDraft || document.querySelector('[aria-modal="true"]')) return;
      e.preventDefault();
      if (isZ && !e.shiftKey) void state.undo();
      else void state.redo();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enabled]);
}
