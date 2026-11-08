/**
 * The desktop app (SubForge Studio.exe) shows the studio in its own window and offers a few
 * calls through pywebview's bridge. In a normal browser this is all absent.
 */
interface DesktopApi {
  set_fullscreen?: (on: boolean) => Promise<boolean>;
  /** Clipboard text; null when the app can't read it (then the page's own API is tried) */
  read_clipboard?: () => Promise<string | null>;
}

export const desktopApi = (): DesktopApi | null =>
  (window as unknown as { pywebview?: { api?: DesktopApi } }).pywebview?.api ?? null;

/**
 * Text on the clipboard, or null if it can't be read. The desktop window asks the app (WebView2
 * may refuse the page's clipboard permission); a browser uses navigator.clipboard, which may ask
 * the user once.
 */
export async function readClipboardText(): Promise<string | null> {
  try {
    const fromApp = await desktopApi()?.read_clipboard?.();
    if (typeof fromApp === 'string') return fromApp;
  } catch {
    /* fall back to the browser API */
  }
  try {
    return navigator.clipboard?.readText ? await navigator.clipboard.readText() : null;
  } catch {
    return null;
  }
}
