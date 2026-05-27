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

  return (
    <div className="h-screen w-screen flex flex-col md:flex-row relative selection:bg-primary-container selection:text-on-primary-container mesh-bg font-geist text-on-surface bg-background">
      <div 
        id="cursor-glow" 
        style={{ left: `${cursorPos.x}px`, top: `${cursorPos.y}px`, display: cursorPos.x === -1000 ? 'none' : 'block' }}
      />
      
      <button 
        onClick={() => setIsLogOpen(!isLogOpen)}
        className="fixed top-8 right-8 z-50 w-12 h-12 rounded-full glass-panel flex items-center justify-center text-on-surface-variant hover:text-primary transition-all shadow-lg hover:shadow-primary/20"
      >
        <span className="material-symbols-outlined">{isLogOpen ? "close" : "chat"}</span>
      </button>

      <header className="flex md:hidden justify-between items-center px-gutter py-4 w-full z-50 fixed top-0 glass-panel border-x-0 border-t-0 border-b">
        <div className="font-headline-md-mobile text-headline-md-mobile text-primary tracking-tight font-medium">NextGen Voice</div>
        <div className="flex gap-4">
          <span className="material-symbols-outlined text-on-surface-variant hover:text-secondary transition-colors cursor-pointer">settings</span>
        </div>
      </header>

      <SideNav />

      <main className="flex-grow flex flex-col md:flex-row relative md:ml-80 pt-20 md:pt-0 h-full z-10 w-full">
        <div className="flex-1 flex items-center justify-center relative p-8 w-full">
          <VoiceOrb isSpeaking={assistantSpeaking} isListening={mic.state === "listening"} isUserSpeaking={mic.vadState === "speaking"} userAudioLevel={mic.level} />
        </div>

        <div className="absolute bottom-12 left-1/2 -translate-x-1/2 z-50 flex space-x-12 items-center glass-panel px-8 py-4 rounded-full shadow-[0_8px_32px_rgba(0,0,0,0.3)] transition-all duration-300 hover:bg-white/[0.05] hover:border-white/20">
          <button 
            onClick={mic.state === "listening" ? mic.stop : handleStartMic}
            className={`flex flex-col items-center justify-center scale-110 group transition-all duration-300 ${mic.state === 'listening' ? 'text-secondary drop-shadow-[0_0_8px_rgba(76,215,246,0.5)]' : 'text-on-surface-variant opacity-80 hover:opacity-100 hover:text-primary hover:drop-shadow-[0_0_8px_rgba(208,188,255,0.5)]'}`}
          >
            <div className={`w-12 h-12 rounded-full flex items-center justify-center mb-1 transition-all duration-300 ${mic.state === 'listening' ? 'bg-secondary/10 group-hover:bg-secondary/20 border border-secondary/30 shadow-[0_0_15px_rgba(76,215,246,0.4)] group-hover:shadow-[0_0_25px_rgba(76,215,246,0.7)]' : 'bg-transparent group-hover:bg-primary/10 border border-transparent group-hover:border-primary/20 group-hover:shadow-[0_0_20px_rgba(208,188,255,0.4)]'}`}>
              <span className="material-symbols-outlined transition-transform duration-300 group-hover:scale-110" style={{ fontVariationSettings: '"FILL" 1, "wght" 500, "GRAD" 0, "opsz" 24' }}>mic</span>
            </div>
            <span className="font-label-caps text-label-caps font-bold tracking-widest">{mic.state === 'listening' ? 'Listening' : 'Mic'}</span>
          </button>
          
          <button 
            onClick={handleStopSpeech}
            disabled={!assistantSpeaking}
            className={`flex flex-col items-center justify-center transition-all duration-300 group ${assistantSpeaking ? 'opacity-100 text-error hover:drop-shadow-[0_0_8px_rgba(255,180,171,0.5)]' : 'text-on-surface-variant opacity-60 hover:opacity-100 hover:text-error'}`}
          >
            <div className="w-12 h-12 rounded-full bg-transparent flex items-center justify-center mb-1 group-hover:bg-error/10 transition-all duration-300 group-hover:shadow-[0_0_20px_rgba(255,180,171,0.4)] border border-transparent group-hover:border-error/20">
              <span className="material-symbols-outlined transition-transform duration-300 group-hover:scale-110">stop_circle</span>
            </div>
            <span className="font-label-caps text-label-caps font-bold tracking-widest">Stop</span>
          </button>
          
          <button 
            onClick={handleTestSpeech}
            className="flex flex-col items-center justify-center text-on-surface-variant opacity-60 hover:opacity-100 hover:text-primary transition-all duration-300 group hover:drop-shadow-[0_0_8px_rgba(208,188,255,0.5)]"
          >
            <div className="w-12 h-12 rounded-full bg-transparent flex items-center justify-center mb-1 group-hover:bg-primary/10 transition-all duration-300 group-hover:shadow-[0_0_20px_rgba(208,188,255,0.4)] border border-transparent group-hover:border-primary/20">
              <span className="material-symbols-outlined transition-transform duration-300 group-hover:scale-110">biotech</span>
            </div>
            <span className="font-label-caps text-label-caps font-bold tracking-widest">Test</span>
          </button>
        </div>
      </main>

      <ActivityLog isOpen={isLogOpen} onClose={() => setIsLogOpen(false)} events={events} voiceEvents={voiceEvents} />
    </div>
  );
}
