import React, { useEffect, useState, useRef } from "react";
import { useAgentSession } from "../hooks/useAgentSession";
import { SideNav } from "../components/dashboard/SideNav";
import { VoiceOrb } from "../components/dashboard/VoiceOrb";
import { ActivityLog } from "../components/dashboard/ActivityLog";

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
    
    const handleMediaChange = (e: any) => {
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
      {/* Background Grid */}
      <div className="absolute inset-0 bg-[size:40px_40px] bg-grid-pattern opacity-50 pointer-events-none z-0"></div>

      {/* Mobile Header */}
      <header className="fixed top-0 left-0 w-full z-50 flex justify-between items-center px-6 py-4 glass-panel border-x-0 border-t-0 md:hidden">
        <button className="text-onSurfaceVariant hover:text-white transition-colors p-2 rounded-full" onClick={() => setIsNavOpen(true)}>
          <span className="material-symbols-outlined" style={{ fontVariationSettings: '"FILL" 0' }}>menu</span>
        </button>
        <h1 className="font-feature-title text-[16px] font-bold text-onSurface">NextGen Voice</h1>
        <button className="text-onSurfaceVariant hover:text-white transition-colors p-2 rounded-full" onClick={toggleLog}>
          <span className="material-symbols-outlined" style={{ fontVariationSettings: '"FILL" 0' }}>history</span>
        </button>
      </header>

      {/* Sidebar Navigation */}
      <SideNav events={events} isOpen={isNavOpen} onClose={() => setIsNavOpen(false)} />

      {/* Main Stage */}
      <main className="flex-1 flex flex-col relative z-10 pt-[72px] md:pt-0">
        {/* Top Status */}
        <div className="absolute top-24 md:top-12 w-full flex justify-center z-20 cursor-pointer" onClick={toggleLog}>
          <div className="glass-panel px-4 py-2 rounded-full flex items-center gap-3 transition-colors duration-300 hover:bg-white/5">
             <div className={`w-2.5 h-2.5 rounded-full animate-pulse transition-colors duration-300 ${statusColor}`}></div>
             <span className="font-label-sm text-label-sm text-onSurface font-bold transition-colors duration-300">
               {statusText}
             </span>
          </div>
        </div>

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

        {/* Bottom Controls */}
        <div className="absolute bottom-12 w-full flex flex-col items-center gap-6 z-20">
          <div className="glass-panel rounded-full px-8 py-4 flex items-center gap-12">
            
            {/* Mic Button */}
            {mic.permissionState !== 'granted' ? (
              <button onClick={handleStartMic} className="flex flex-col items-center gap-2 group relative active:scale-95 transition-transform">
                <div className="absolute -top-10 left-1/2 -translate-x-1/2 px-3 py-1 bg-error/90 backdrop-blur-md rounded-lg text-[10px] uppercase font-bold text-white opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap shadow-xl border border-white/10 z-50 tracking-wider">
                  Permission Required
                </div>
                <div className="relative">
                  <div className="w-10 h-10 rounded-full bg-error/10 flex items-center justify-center border border-error/30 shadow-[0_0_15px_rgba(255,180,171,0.2)] backdrop-blur-md transition-all duration-200">
                    <svg className="text-error" fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="18" xmlns="http://www.w3.org/2000/svg"><line x1="1" x2="23" y1="1" y2="23"></line><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"></path><path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23"></path><line x1="12" x2="12" y1="19" y2="22"></line></svg>
                  </div>
                  <div className="absolute -top-0.5 -right-0.5 w-3 h-3 bg-error rounded-full border-2 border-[var(--color-surface)] shadow-[0_0_8px_rgba(255,180,171,0.8)] z-10"></div>
                </div>
                <span className="font-label-sm text-label-sm text-error font-bold transition-colors">Mic Off</span>
              </button>
            ) : (
              <button onClick={mic.state === "listening" ? mic.stop : handleStartMic} className="flex flex-col items-center gap-2 group active:scale-95 transition-transform">
                <div className={`w-10 h-10 rounded-full bg-surfaceContainer flex items-center justify-center transition-all duration-200 border border-outlineVariant/50 ${mic.state === 'listening' ? 'bg-primary/20 border-primary/50 text-primary' : 'group-hover:bg-surfaceContainerHigh group-hover:border-primary/50 group-active:bg-primary/20 text-onSurface group-hover:text-primary'}`}>
                  <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="18" xmlns="http://www.w3.org/2000/svg"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" x2="12" y1="19" y2="22"></line></svg>
                </div>
                <span className={`font-label-sm text-label-sm transition-colors ${mic.state === 'listening' ? 'text-primary font-bold' : 'text-onSurfaceVariant group-hover:text-primary'}`}>{mic.state === 'listening' ? 'Listening' : 'Mic'}</span>
              </button>
            )}

            <button onClick={handleStopSpeech} disabled={!assistantSpeaking} className="flex flex-col items-center gap-2 group active:scale-95 transition-transform">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center transition-all duration-200 border ${assistantSpeaking ? 'bg-error/20 border-error/50 text-error' : 'bg-surfaceContainer border-outlineVariant/50 text-onSurface group-hover:bg-error/20 group-hover:border-error/50 group-hover:text-error opacity-50 group-hover:opacity-100'}`}>
                <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="18" xmlns="http://www.w3.org/2000/svg"><rect height="18" rx="2" width="18" x="3" y="3"></rect><path d="M9 9h6v6H9z"></path></svg>
              </div>
              <span className={`font-label-sm text-label-sm transition-colors ${assistantSpeaking ? 'text-error font-bold' : 'text-onSurfaceVariant group-hover:text-error opacity-50 group-hover:opacity-100'}`}>Stop</span>
            </button>

            <button onClick={handleTestSpeech} className="flex flex-col items-center gap-2 group active:scale-95 transition-transform">
              <div className="w-10 h-10 rounded-full bg-surfaceContainer flex items-center justify-center group-hover:bg-surfaceContainerHigh group-hover:border-onSurface/50 group-active:bg-onSurface/20 transition-all duration-200 border border-outlineVariant/50 text-onSurface group-hover:text-white">
                <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="18" xmlns="http://www.w3.org/2000/svg"><path d="M10 2v7.31"></path><path d="M14 9.3V1.99"></path><path d="M8.5 2h7"></path><path d="M14 9.3a6.5 6.5 0 1 1-4 0"></path><path d="M5.52 16h12.96"></path></svg>
              </div>
              <span className="font-label-sm text-label-sm text-onSurfaceVariant group-hover:text-white transition-colors">Test</span>
            </button>

          </div>
        </div>
      </main>

      {/* Activity Log Overlay */}
      <ActivityLog isOpen={isLogOpen} onClose={() => setIsLogOpen(false)} events={events} voiceEvents={voiceEvents} isAgentListening={mic.state === 'listening'} />
    </div>
  );
}
