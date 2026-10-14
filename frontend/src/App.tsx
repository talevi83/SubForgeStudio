import React, { useEffect } from 'react';
import { useJobStore } from './store/useJobStore';
import { useUiStore, applyToDocument } from './store/useUiStore';
import { useSettingsStore } from './store/useSettingsStore';
import { useT } from './i18n';
import { Sidebar } from './components/layout/Sidebar';
import { Header } from './components/layout/Header';
import { MediaDropzone } from './components/ingest/MediaDropzone';
import { StreamSelector } from './components/ingest/StreamSelector';
import { PipelineConfig } from './components/ingest/PipelineConfig';
import { LivePipelineBar } from './components/monitor/LivePipelineBar';
import { VideoSyncPlayer } from './components/studio/VideoSyncPlayer';
import { SubtitleTable } from './components/studio/SubtitleTable';
import { ExportToolbar } from './components/studio/ExportToolbar';
import { SettingsScreen } from './components/settings/SettingsScreen';

const ErrorToast: React.FC = () => {
  const error = useJobStore((s) => s.error);
  const clearError = useJobStore((s) => s.clearError);
  const t = useT();
  if (!error) return null;
  return (
    <div className="fixed bottom-4 end-4 z-50 max-w-md p-3 pe-8 rounded-lg bg-rose-950/90 border border-rose-500/40 text-rose-200 text-xs shadow-xl">
      {error}
      <button onClick={clearError} className="absolute top-2 end-2 text-rose-300 hover:text-rose-200" aria-label={t('app.dismiss')}>
        ✕
      </button>
    </div>
  );
};

export const App: React.FC = () => {
  const activeJob = useJobStore((s) => s.activeJob);
  const view = useUiStore((s) => s.view);
  const language = useUiStore((s) => s.language);
  const theme = useUiStore((s) => s.theme);
  const loadSettings = useSettingsStore((s) => s.load);
  const t = useT();

  useEffect(() => {
    applyToDocument(language, theme);
  }, [language, theme]);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  return (
    <div className="flex h-screen w-screen bg-app text-zinc-100 overflow-hidden font-sans">
      <ErrorToast />
      <Sidebar />

      <div className="flex-1 flex flex-col min-w-0 h-screen overflow-hidden">
        <Header />

        {view === 'settings' ? (
          <div className="flex-1 overflow-y-auto p-8">
            <SettingsScreen />
          </div>
        ) : activeJob ? (
          <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
            <LivePipelineBar />
            <div className="flex-1 p-4 grid grid-cols-1 lg:grid-cols-12 gap-4 min-h-0 overflow-hidden">
              <div className="lg:col-span-5 h-full min-h-0">
                <VideoSyncPlayer key={activeJob.id} />
              </div>
              <div className="lg:col-span-7 h-full min-h-0">
                <SubtitleTable />
              </div>
            </div>
            <ExportToolbar />
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto p-8">
            <div className="max-w-4xl mx-auto space-y-6">
              <div className="text-center space-y-1 py-4">
                <h1 className="text-2xl font-bold tracking-tight text-zinc-50">{t('app.ingestTitle')}</h1>
                <p className="text-xs text-zinc-400">{t('app.ingestSubtitle')}</p>
              </div>
              <MediaDropzone />
              <StreamSelector />
              <PipelineConfig />
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default App;
