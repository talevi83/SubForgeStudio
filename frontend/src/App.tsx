import React from 'react';
import { useJobStore } from './store/useJobStore';
import { Sidebar } from './components/layout/Sidebar';
import { Header } from './components/layout/Header';
import { MediaDropzone } from './components/ingest/MediaDropzone';
import { StreamSelector } from './components/ingest/StreamSelector';
import { PipelineConfig } from './components/ingest/PipelineConfig';
import { LivePipelineBar } from './components/monitor/LivePipelineBar';
import { VideoSyncPlayer } from './components/studio/VideoSyncPlayer';
import { SubtitleTable } from './components/studio/SubtitleTable';
import { ExportToolbar } from './components/studio/ExportToolbar';

export const App: React.FC = () => {
  const { activeJob } = useJobStore();

  return (
    <div className="flex h-screen w-screen bg-[#09090b] text-zinc-100 overflow-hidden font-sans">
      {/* Sidebar Navigation */}
      <Sidebar />

      {/* Main Studio Viewport */}
      <div className="flex-1 flex flex-col min-w-0 h-screen overflow-hidden">
        <Header />

        {activeJob ? (
          /* Active Job Studio & Live Monitor Workspace */
          <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
            {/* Top Pipeline Stepper & Live Speed/ETA */}
            <LivePipelineBar />

            {/* Split Screen Studio (Video 40% / Table 60%) */}
            <div className="flex-1 p-4 grid grid-cols-1 lg:grid-cols-12 gap-4 min-h-0 overflow-hidden">
              {/* Left Pane — Video Sync Player (40% on desktop) */}
              <div className="lg:col-span-5 h-full min-h-0">
                <VideoSyncPlayer />
              </div>

              {/* Right Pane — Interactive Subtitle Editor (60% on desktop) */}
              <div className="lg:col-span-7 h-full min-h-0">
                <SubtitleTable />
              </div>
            </div>

            {/* Export Toolbar */}
            <ExportToolbar />
          </div>
        ) : (
          /* New Ingest & Pipeline Setup Screen */
          <div className="flex-1 overflow-y-auto p-8">
            <div className="max-w-4xl mx-auto space-y-6">
              <div className="text-center space-y-1 py-4">
                <h1 className="text-2xl font-bold tracking-tight text-white">
                  Audiovisual Subtitling & Translation Studio
                </h1>
                <p className="text-xs text-zinc-400">
                  Broadcast-quality transcription with faster-whisper and Gemini RTL Hebrew translation
                </p>
              </div>

              {/* 1. Media Ingestion Dropzone */}
              <MediaDropzone />

              {/* 2. Media Stream Inspector */}
              <StreamSelector />

              {/* 3. Pipeline Configuration */}
              <PipelineConfig />
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default App;
