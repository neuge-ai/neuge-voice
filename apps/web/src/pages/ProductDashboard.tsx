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
        {/* Top Status */}
        <div className="absolute top-24 md:top-12 w-full flex justify-center z-20 cursor-pointer" onClick={toggleLog}>
          <div className="border border-white/10 bg-white/5 backdrop-blur-md rounded-full px-[14px] py-[10px] flex items-center gap-2.5 hover:bg-white/10 hover:border-white/20 hover:-translate-y-0.5 transition-all duration-300 shadow-[0_4px_12px_rgba(0,0,0,0.5)] group">
             <div className={`w-[7px] h-[7px] rounded-full transition-colors duration-300 ${statusColor === 'bg-onSurfaceVariant' ? 'bg-white/30' : statusColor === 'bg-primary' ? 'bg-primary shadow-[0_0_14px_rgba(168,85,247,0.78)]' : 'bg-[#00fbfb] shadow-[0_0_14px_rgba(0,251,251,0.78)]'}`}></div>
             <span className="font-label-sm text-[11px] tracking-[0.12em] uppercase text-white/70 group-hover:text-white transition-colors duration-300">
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
              <button onClick={handleStartMic} className="flex flex-col items-center gap-2 group relative hover:scale-105 active:scale-95 transition-all duration-300">
                <div className="absolute -top-10 left-1/2 -translate-x-1/2 px-3 py-1 bg-error/90 backdrop-blur-md rounded-lg text-[10px] uppercase font-bold text-white opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap shadow-[0_4px_12px_rgba(0,0,0,0.5)] border border-white/10 z-50 tracking-wider">
                  Permission Required
                </div>
                <div className="relative">
                  <div className="w-12 h-12 rounded-full bg-error/10 flex items-center justify-center border border-error/30 shadow-[0_0_15px_rgba(255,180,171,0.2)] backdrop-blur-md transition-all duration-300 group-hover:shadow-[0_0_25px_rgba(255,180,171,0.4)] group-hover:bg-error/20 group-hover:border-error/50">
                    <svg className="text-error" fill="none" height="20" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="20" xmlns="http://www.w3.org/2000/svg"><line x1="1" x2="23" y1="1" y2="23"></line><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"></path><path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23"></path><line x1="12" x2="12" y1="19" y2="22"></line></svg>
                  </div>
                  <div className="absolute -top-0.5 -right-0.5 w-3 h-3 bg-error rounded-full border-2 border-[var(--color-surface)] shadow-[0_0_8px_rgba(255,180,171,0.8)] z-10"></div>
                </div>
                <span className="font-label-sm text-label-sm text-error font-bold transition-colors">Mic Off</span>
              </button>
            ) : (
              <button onClick={mic.state === "listening" ? mic.stop : handleStartMic} className="flex flex-col items-center gap-2 group hover:scale-105 active:scale-95 transition-all duration-300">
                <div className={`w-12 h-12 rounded-full flex items-center justify-center transition-all duration-300 border backdrop-blur-md ${mic.state === 'listening' ? 'bg-cyanCore/20 border-cyanCore/50 text-cyanCore shadow-[0_0_30px_rgba(4,251,251,0.3)]' : 'bg-white/5 border-white/10 text-onSurface group-hover:bg-white/10 group-hover:border-cyanCore/30 group-active:bg-cyanCore/20 group-hover:text-cyanCore shadow-[0_4px_12px_rgba(0,0,0,0.3)]'}`}>
                  <svg fill="none" height="20" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="20" xmlns="http://www.w3.org/2000/svg"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" x2="12" y1="19" y2="22"></line></svg>
                </div>
                <span className={`font-label-sm text-label-sm transition-colors ${mic.state === 'listening' ? 'text-cyanCore font-bold drop-shadow-[0_0_5px_rgba(4,251,251,0.5)]' : 'text-onSurfaceVariant group-hover:text-white'}`}>{mic.state === 'listening' ? 'Listening' : 'Mic'}</span>
              </button>
            )}

            <button onClick={handleStopSpeech} disabled={!assistantSpeaking} className="flex flex-col items-center gap-2 group hover:scale-105 active:scale-95 transition-all duration-300">
              <div className={`w-12 h-12 rounded-full flex items-center justify-center transition-all duration-300 border backdrop-blur-md ${assistantSpeaking ? 'bg-error/20 border-error/50 text-error shadow-[0_0_30px_rgba(255,180,171,0.3)]' : 'bg-white/5 border-white/10 text-onSurface group-hover:bg-error/20 group-hover:border-error/50 group-hover:text-error opacity-50 group-hover:opacity-100 shadow-[0_4px_12px_rgba(0,0,0,0.3)]'}`}>
                <svg fill="none" height="20" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="20" xmlns="http://www.w3.org/2000/svg"><rect height="18" rx="2" width="18" x="3" y="3"></rect><path d="M9 9h6v6H9z"></path></svg>
              </div>
              <span className={`font-label-sm text-label-sm transition-colors ${assistantSpeaking ? 'text-error font-bold drop-shadow-[0_0_5px_rgba(255,180,171,0.5)]' : 'text-onSurfaceVariant group-hover:text-white opacity-50 group-hover:opacity-100'}`}>Stop</span>
            </button>

            <button onClick={handleTestSpeech} className="flex flex-col items-center gap-2 group hover:scale-105 active:scale-95 transition-all duration-300">
              <div className="w-12 h-12 rounded-full bg-white/5 flex items-center justify-center group-hover:bg-white/10 group-hover:border-primary/50 group-active:bg-primary/20 transition-all duration-300 border border-white/10 text-onSurface group-hover:text-primary shadow-[0_4px_12px_rgba(0,0,0,0.3)] backdrop-blur-md">
                <svg fill="none" height="20" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="20" xmlns="http://www.w3.org/2000/svg"><path d="M10 2v7.31"></path><path d="M14 9.3V1.99"></path><path d="M8.5 2h7"></path><path d="M14 9.3a6.5 6.5 0 1 1-4 0"></path><path d="M5.52 16h12.96"></path></svg>
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
