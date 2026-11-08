import React, { useEffect, useRef } from 'react';
import { ClipboardPaste } from 'lucide-react';
import { useT } from '../../i18n';

interface PasteContextMenuProps {
  /** Viewport coordinates of the right click */
  x: number;
  y: number;
  onPaste: () => void;
  onClose: () => void;
}

/** A one-item right-click menu ("Paste") at the pointer; closes on outside click, Escape, scroll or resize. */
export const PasteContextMenu: React.FC<PasteContextMenuProps> = ({ x, y, onPaste, onClose }) => {
  const t = useT();
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('pointerdown', onPointerDown, true);
    window.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onClose, true);
    window.addEventListener('resize', onClose);
    window.addEventListener('blur', onClose);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown, true);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onClose, true);
      window.removeEventListener('resize', onClose);
      window.removeEventListener('blur', onClose);
    };
  }, [onClose]);

  // Keep the menu inside the window near the right/bottom edges
  const left = Math.min(x, window.innerWidth - 140);
  const top = Math.min(y, window.innerHeight - 48);

  return (
    <div
      ref={ref}
      role="menu"
      data-testid="paste-menu"
      style={{ left, top }}
      className="fixed z-50 min-w-[120px] py-1 rounded-lg border border-line bg-surface shadow-xl shadow-black/40"
      onContextMenu={(e) => e.preventDefault()}
    >
      <button
        type="button"
        role="menuitem"
        autoFocus
        onClick={onPaste}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-zinc-200 hover:bg-indigo-600/30 focus:bg-indigo-600/30 focus:outline-none cursor-pointer text-start"
      >
        <ClipboardPaste className="w-3.5 h-3.5 text-zinc-400" />
        {t('common.paste')}
      </button>
    </div>
  );
};
