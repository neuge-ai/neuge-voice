import React, { useEffect, useState, useRef } from "react";
import { useAgentSession } from "../hooks/useAgentSession";
import { SideNav } from "../components/dashboard/SideNav";
import { VoiceOrb } from "../components/dashboard/VoiceOrb";
import { ActivityLog } from "../components/dashboard/ActivityLog";
import { StatusBadge } from "../components/dashboard/StatusBadge";
import { MicControls } from "../components/dashboard/MicControls";

export function ProductDashboard() {
  const {
    mic,
    assistantSpeaking,
    events,
    voiceEvents,
    handleStartMic,
    handleStopSpeech,
    handleTestSpeech,
  } = useAgentSession();

  const [isLogOpen, setIsLogOpen] = useState(window.innerWidth >= 768);
  const [isNavOpen, setIsNavOpen] = useState(false);
  const userWantsLogOpen = useRef(true);

  const toggleLog = () => {
    const nextState = !isLogOpen;
    setIsLogOpen(nextState);
    userWantsLogOpen.current = nextState;
  };

  useEffect(() => {
    const mediaQuery = window.matchMedia('(min-width: 768px)');
    
    const handleMediaChange = (e: MediaQueryListEvent) => {
      const isDesktop = e.matches;
      if (isDesktop) {
        setIsLogOpen(userWantsLogOpen.current);
      } else {
        setIsLogOpen(false);
        setIsNavOpen(false);
      }
    };

    if (mediaQuery.addEventListener) {
      mediaQuery.addEventListener('change', handleMediaChange);
      return () => mediaQuery.removeEventListener('change', handleMediaChange);
    } else {
      // Fallback
      mediaQuery.addListener(handleMediaChange);
      return () => mediaQuery.removeListener(handleMediaChange);
    }
  }, []);

  // Status mapping
  let statusText = 'Idle';
  let statusColor = 'bg-onSurfaceVariant';
  if (assistantSpeaking) {
    statusText = 'Assistant';
    statusColor = 'bg-primary';
  } else if (mic.vadState === 'speaking') {
    statusText = 'Processing ...';
    statusColor = 'bg-cyanCore';
  } else if (mic.state === 'listening') {
    statusText = 'Attentive';
    statusColor = 'bg-cyanCore';
  }

  return (
    <div className="h-screen w-full overflow-hidden flex text-sm antialiased relative font-body-md text-onSurface bg-surface">
      {/* Background Dots */}
      <div className="absolute inset-0 bg-[size:24px_24px] bg-theme-dots opacity-30 pointer-events-none z-0 mask-image-radial-gradient"></div>

      {/* Mobile Header */}
      <header className="fixed top-0 left-0 w-full z-50 flex justify-between items-center px-6 py-4 glass-panel border-x-0 border-t-0 md:hidden">
        <button className="text-onSurfaceVariant hover:text-white transition-colors p-2 rounded-full" onClick={() => setIsNavOpen(true)}>
          <span className="material-symbols-outlined" style={{ fontVariationSettings: '"FILL" 0' }}>menu</span>
        </button>
        <h1 className="font-feature-title text-[16px] font-bold text-onSurface">Neuge</h1>
        <button className="text-onSurfaceVariant hover:text-white transition-colors p-2 rounded-full" onClick={toggleLog}>
          <span className="material-symbols-outlined" style={{ fontVariationSettings: '"FILL" 0' }}>history</span>
        </button>
      </header>

      {/* Sidebar Navigation */}
      <SideNav events={events} isOpen={isNavOpen} onClose={() => setIsNavOpen(false)} />

      {/* Main Stage */}
      <main className="flex-1 flex flex-col relative z-10 pt-[72px] md:pt-0">
        <StatusBadge statusText={statusText} statusColor={statusColor} onClick={toggleLog} />

        {/* Central Orb Container */}
        <div className="flex-1 flex items-center justify-center relative">
          <div className={`absolute w-[300px] h-[300px] md:w-[600px] md:h-[600px] rounded-full blur-[100px] pointer-events-none transition-colors duration-500 ${
            assistantSpeaking ? 'bg-primary/30' :
            mic.state === 'listening' ? 'bg-cyanGlow/20' :
            mic.vadState === 'speaking' ? 'bg-cyanGlow/40' :
            'bg-primary/10'
          }`}></div>
          <VoiceOrb isSpeaking={assistantSpeaking} isListening={mic.state === "listening"} isUserSpeaking={mic.vadState === "speaking"} userAudioLevel={mic.level} />
        </div>

        <MicControls
          mic={mic}
          assistantSpeaking={assistantSpeaking}
          onStartMic={handleStartMic}
          onStopSpeech={handleStopSpeech}
          onTestSpeech={handleTestSpeech}
        />
      </main>

      {/* Activity Log Overlay */}
      <ActivityLog isOpen={isLogOpen} onClose={() => setIsLogOpen(false)} events={events} voiceEvents={voiceEvents} isAgentListening={mic.state === 'listening'} />
    </div>
  );
}
