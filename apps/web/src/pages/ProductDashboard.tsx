import React, { useEffect, useRef, useState } from "react";
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

  const [isLogOpen, setIsLogOpen] = useState(false);
  const [isNavOpen, setIsNavOpen] = useState(false);
  const [cursorPos, setCursorPos] = useState({ x: -1000, y: -1000 });
  
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      setCursorPos({ x: e.clientX, y: e.clientY });
    };
    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!prefersReducedMotion) {
      document.addEventListener("mousemove", handleMouseMove);
    }
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
    };
  }, []);

  useEffect(() => {
    if (isLogOpen) {
      document.body.classList.add('panel-open');
    } else {
      document.body.classList.remove('panel-open');
    }
  }, [isLogOpen]);


  const islandStatusHtml = mic.state === "listening" 
    ? <>
        <div className="relative flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-secondary opacity-75"></span>
          <span className="relative inline-flex rounded-full h-2 w-2 bg-secondary"></span>
        </div>
        <span className="font-label-caps text-label-caps tracking-[0.2em] uppercase font-bold text-secondary text-xs">LISTENING</span>
      </>
    : <>
        <div className="relative flex h-2 w-2">
          <span className="relative inline-flex rounded-full h-2 w-2 bg-primary"></span>
        </div>
        <span className="font-label-caps text-label-caps tracking-[0.2em] uppercase font-bold text-primary text-xs">READY</span>
      </>;

  return (
    <>
      <div 
        id="cursor-glow" 
        style={{ left: `${cursorPos.x}px`, top: `${cursorPos.y}px`, display: cursorPos.x === -1000 ? 'none' : 'block' }}
      />
      
      {/* Mobile Header */}
      <header className="fixed top-0 left-0 w-full z-50 flex justify-between items-center px-gutter py-4 glass-panel border-x-0 border-t-0 md:hidden">
        <button 
          className="text-on-surface-variant hover:text-white transition-colors p-2 rounded-full" 
          onClick={() => setIsNavOpen(true)}
        >
          <span className="material-symbols-outlined" style={{ fontVariationSettings: '"FILL" 0' }}>menu</span>
        </button>
        <h1 className="font-headline-md-mobile text-[16px] tracking-[0.1em] font-medium bg-gradient-to-r from-white to-slate-200 bg-clip-text text-transparent uppercase">NextGen Voice</h1>
        <button 
          className="text-on-surface-variant hover:text-white transition-colors p-2 rounded-full" 
          onClick={() => setIsLogOpen(true)}
        >
          <span className="material-symbols-outlined" style={{ fontVariationSettings: '"FILL" 0' }}>history</span>
        </button>
      </header>

      {/* Sidebar Navigation */}
      <SideNav events={events} isOpen={isNavOpen} onClose={() => setIsNavOpen(false)} />

      {/* Main Stage Area */}
      <main className="flex-1 h-full w-full relative overflow-hidden flex flex-col md:ml-[320px] pt-[72px] md:pt-0">
        <div className="relative w-full h-full flex flex-col items-center justify-between py-12 transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]" id="stage-container">
          
          {/* Unified Intelligence Island */}
          <div 
            className="fixed top-24 left-1/2 -translate-x-1/2 md:static md:translate-x-0 z-40 md:z-50 flex items-center justify-center min-w-[200px] glass-panel px-6 py-3 rounded-full shadow-[0_8px_32px_rgba(0,0,0,0.3)] cursor-pointer transition-all duration-300 hover:bg-white/[0.05] hover:border-white/20 group" 
            id="thinking-island"
            onClick={() => setIsLogOpen(!isLogOpen)}
          >
            <div className="flex items-center gap-3">
              {islandStatusHtml}
            </div>
          </div>

          {/* Center: Voice Orb Area */}
          <div className="flex-1 flex items-center justify-center relative p-8 w-full h-full">
            <VoiceOrb isSpeaking={assistantSpeaking} isListening={mic.state === "listening"} isUserSpeaking={mic.vadState === "speaking"} userAudioLevel={mic.level} />
          </div>

          {/* Bottom Control Bar */}
          <div className="fixed bottom-10 left-0 right-0 z-50 flex justify-around md:justify-center md:space-x-12 items-center max-w-[340px] md:max-w-none mx-auto md:mx-0 px-8 py-4 md:static rounded-full glass-panel shadow-[0_8px_32px_rgba(0,0,0,0.5)] md:shadow-[0_8px_32px_rgba(0,0,0,0.3)] transition-all duration-300 hover:bg-white/[0.05] hover:border-white/20 w-full md:w-auto">
            {mic.permissionState !== 'granted' ? (
              <button
                onClick={handleStartMic}
                className="flex flex-col items-center justify-center text-error group relative"
              >
                {/* Tooltip */}
                <div className="absolute -top-12 left-1/2 -translate-x-1/2 px-3 py-1 bg-error/90 backdrop-blur-md rounded-lg text-[10px] font-label-caps text-white opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap shadow-xl border border-white/10 z-50">
                    Permission Required
                </div>
                <div className="relative">
                  <div className="w-12 h-12 rounded-full bg-error/10 flex items-center justify-center mb-1 transition-all duration-300 border border-error/30 shadow-[0_0_15px_rgba(255,180,171,0.2)] backdrop-blur-md">
                    <span className="material-symbols-outlined text-error transition-transform duration-300" style={{ fontVariationSettings: '"FILL" 0' }}>mic_off</span>
                  </div>
                  {/* Warning Badge */}
                  <div className="absolute -top-0.5 -right-0.5 w-3 h-3 bg-error rounded-full border-2 border-surface shadow-[0_0_8px_rgba(255,180,171,0.8)] z-10"></div>
                </div>
                <span className="font-label-caps text-label-caps text-error font-bold tracking-widest">Mic Off</span>
              </button>
            ) : (
              <button
                onClick={mic.state === "listening" ? mic.stop : handleStartMic}
                className={`flex flex-col items-center justify-center scale-110 group transition-all duration-300 ${mic.state === 'listening' ? 'text-secondary drop-shadow-[0_0_8px_rgba(76,215,246,0.5)]' : 'text-on-surface-variant opacity-80 hover:opacity-100 hover:text-primary hover:drop-shadow-[0_0_8px_rgba(208,188,255,0.5)]'}`}
              >
                <div className={`w-12 h-12 rounded-full flex items-center justify-center md:mb-1 transition-all duration-300 ${mic.state === 'listening' ? 'bg-secondary/10 group-hover:bg-secondary/20 border border-secondary/30 shadow-[0_0_15px_rgba(76,215,246,0.4)] group-hover:shadow-[0_0_25px_rgba(76,215,246,0.7)]' : 'bg-transparent group-hover:bg-primary/10 border border-transparent group-hover:border-primary/20 group-hover:shadow-[0_0_20px_rgba(208,188,255,0.4)]'}`}>
                  <span className="material-symbols-outlined transition-transform duration-300 group-hover:scale-110" style={{ fontVariationSettings: '"FILL" 1, "wght" 500, "GRAD" 0, "opsz" 24' }}>mic</span>
                </div>
                <span className="hidden md:block font-label-caps text-label-caps font-bold tracking-widest">{mic.state === 'listening' ? 'Listening' : 'Mic'}</span>
              </button>
            )}

            <button
              onClick={handleStopSpeech}
              disabled={!assistantSpeaking}
              className={`flex flex-col items-center justify-center transition-all duration-300 group ${assistantSpeaking ? 'opacity-100 text-error hover:drop-shadow-[0_0_8px_rgba(255,180,171,0.5)]' : 'text-on-surface-variant opacity-60 hover:opacity-100 hover:text-error'}`}
            >
              <div className="w-12 h-12 rounded-full bg-transparent flex items-center justify-center md:mb-1 group-hover:bg-error/10 transition-all duration-300 group-hover:shadow-[0_0_20px_rgba(255,180,171,0.4)] border border-transparent group-hover:border-error/20">
                <span className="material-symbols-outlined transition-transform duration-300 group-hover:scale-110">stop_circle</span>
              </div>
              <span className="hidden md:block font-label-caps text-label-caps font-bold tracking-widest">Stop</span>
            </button>

            <button
              onClick={handleTestSpeech}
              className="flex flex-col items-center justify-center text-on-surface-variant opacity-60 hover:opacity-100 hover:text-primary transition-all duration-300 group hover:drop-shadow-[0_0_8px_rgba(208,188,255,0.5)]"
            >
              <div className="w-12 h-12 rounded-full bg-transparent flex items-center justify-center md:mb-1 group-hover:bg-primary/10 transition-all duration-300 group-hover:shadow-[0_0_20px_rgba(208,188,255,0.4)] border border-transparent group-hover:border-primary/20">
                <span className="material-symbols-outlined transition-transform duration-300 group-hover:scale-110">biotech</span>
              </div>
              <span className="hidden md:block font-label-caps text-label-caps font-bold tracking-widest">Test</span>
            </button>
          </div>
        </div>
      </main>

      {/* Activity Log Overlay */}
      <ActivityLog isOpen={isLogOpen} onClose={() => setIsLogOpen(false)} events={events} voiceEvents={voiceEvents} />
    </>
  );
}
