/**
 * The desktop app (SubForge Studio.exe) shows the studio in its own window and offers a few
 * calls through pywebview's bridge. In a normal browser this is all absent.
 */
interface DesktopApi {
  set_fullscreen?: (on: boolean) => Promise<boolean>;
}

export const desktopApi = (): DesktopApi | null =>
  (window as unknown as { pywebview?: { api?: DesktopApi } }).pywebview?.api ?? null;
