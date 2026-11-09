/**
 * The desktop app (SubBuild Studio.exe / launcher.py) shows the studio in its own window and offers
 * native OS integration through pywebview's bridge. In a normal browser this is all absent.
 */
interface DesktopApi {
  set_fullscreen?: (on: boolean) => Promise<boolean>;
  /** Clipboard text; null when the app can't read it (then the page's own API is tried) */
  read_clipboard?: () => Promise<string | null>;
  /** Full disk path for a file just dropped into the window, or null when unavailable */
  resolve_dropped_file?: (filename: string) => Promise<string | null>;
  /** Native OS file picker; returns the chosen path, '' if cancelled, or null if unsupported */
  pick_media_file?: () => Promise<string | null>;
}

interface WebView2Bridge {
  postMessageWithAdditionalObjects?: (message: string, objects: FileList) => void;
}

export const desktopApi = (): DesktopApi | null =>
  (window as unknown as { pywebview?: { api?: DesktopApi } }).pywebview?.api ?? null;

export const isDesktopWindow = (): boolean => Boolean(desktopApi());

/**
 * Resolve the full local disk path of a dragged-and-dropped File when running inside the
 * SubBuild Studio desktop window (WebView2 / Cocoa / Qt). Returns null in a regular web browser.
 *
 * IMPORTANT: Must be invoked synchronously inside the `onDrop` event handler before any `await`,
 * because browsers clear `dataTransfer.files` once the synchronous event dispatch finishes.
 */
export async function resolveDroppedFilePath(
  file: File,
  dataTransfer?: DataTransfer | null
): Promise<string | null> {
  const direct =
    (file as unknown as { pywebviewFullPath?: string; path?: string }).pywebviewFullPath ||
    (file as unknown as { path?: string }).path;
  if (typeof direct === 'string' && direct.trim()) {
    return direct.trim();
  }

  const webviewBridge = (window as unknown as { chrome?: { webview?: WebView2Bridge } }).chrome?.webview;
  const files = dataTransfer?.files;
  if (webviewBridge?.postMessageWithAdditionalObjects && files && files.length > 0) {
    try {
      webviewBridge.postMessageWithAdditionalObjects('FilesDropped', files);
    } catch {
      /* ignore if WebView2 rejects non-filesystem objects */
    }
  }

  const api = desktopApi();
  if (!api?.resolve_dropped_file) return null;
  try {
    const resolved = await api.resolve_dropped_file(file.name);
    return typeof resolved === 'string' && resolved.trim() ? resolved.trim() : null;
  } catch {
    return null;
  }
}

/**
 * Open the native OS file picker in the desktop window and return the chosen local path.
 * Returns '' if the user cancelled the dialog, or null if not running in the desktop window.
 */
export async function pickLocalMediaFile(): Promise<string | null> {
  const api = desktopApi();
  if (!api?.pick_media_file) return null;
  try {
    const picked = await api.pick_media_file();
    return typeof picked === 'string' ? picked : null;
  } catch {
    return null;
  }
}

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
