import React from "react";
import { type AgentEvent } from "../../api/agentApi";
import { type BrowserVoiceEvent } from "../../realtime/browserVoiceTransport";

function formatTime(date: Date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function ActivityLog({ isOpen, onClose, events, voiceEvents }: { isOpen: boolean; onClose: () => void; events: AgentEvent[]; voiceEvents: BrowserVoiceEvent[] }) {
  // Filter for events that actually have text to show in the log
  const chatEvents = voiceEvents.filter(e => e.text && (e.event === 'transcript_final' || e.event === 'assistant_response' || e.event === 'user_turn' || e.event === 'delivery_ready'));

  return (
    <aside className={`fixed inset-0 md:inset-auto md:right-0 md:top-0 w-full md:w-[400px] lg:w-[480px] h-full glass-panel max-md:!bg-surface-dim/80 max-md:!backdrop-blur-3xl p-6 pt-12 md:pt-24 flex flex-col z-[60] md:z-40 transform transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] border-t border-white/10 md:border-y-0 md:border-r-0 shadow-[0_-8px_32px_rgba(0,0,0,0.3)] md:shadow-none ${isOpen ? 'translate-y-0 md:translate-x-0' : 'translate-y-full md:translate-y-0 md:translate-x-full'}`}>
      <div className="flex items-center justify-between md:justify-center mb-8 glass-panel md:bg-transparent md:border-none md:shadow-none border-x-0 border-t-0 p-6 md:p-0 -mx-6 md:mx-0 -mt-12 md:mt-0 pt-12 md:pt-0">
        <h2 className="text-[14px] font-label-caps text-white tracking-[0.2em] uppercase font-bold text-center">ACTIVITY LOG</h2>
        <button 
          className="md:hidden text-on-surface-variant hover:text-white p-2 rounded-full hover:bg-white/5 transition-colors" 
          onClick={onClose}
        >
          <span className="material-symbols-outlined">close</span>
        </button>
      </div>
      
      <div className="flex-grow overflow-y-auto pr-2 space-y-6 scrollbar-hide flex flex-col-reverse pb-24 md:pb-0">
        {chatEvents.map((event, idx) => {
          const timeStr = `[${formatTime(new Date(event.created_at || Date.now()))}]`;
          
          const isUser = event.event === 'transcript_final' || event.event === 'user_turn';
          
          if (isUser) {
            return (
              <div key={idx} className="flex flex-col items-end message-reveal">
                <div className="flex items-center gap-2 mb-1 px-1">
                  <span className="font-metadata-sm text-metadata-sm text-slate-300 font-mono">{timeStr}</span>
                  <span className="font-metadata-sm text-metadata-sm text-primary font-bold">User</span>
                </div>
                <div className="glass-elevated rounded-2xl rounded-tr-sm p-4 border border-primary/30 max-w-[85%] bg-primary/10">
                  <p className="font-body-md text-body-md text-white font-medium">"{event.text}"</p>
                </div>
              </div>
            );
          } else {
            return (
              <div key={idx} className="flex flex-col items-start message-reveal">
                <div className="flex items-center gap-2 mb-1 px-1">
                  <span className="font-metadata-sm text-metadata-sm text-secondary font-bold">Assistant</span>
                  <span className="font-metadata-sm text-metadata-sm text-slate-300 font-mono">{timeStr}</span>
                </div>
                <div className="glass-recessed rounded-2xl rounded-tl-sm p-4 max-w-[85%]">
                  <p className="font-body-md text-body-md text-white font-medium leading-relaxed">{event.text}</p>
                </div>
              </div>
            );
          }
        })}
      </div>
    </aside>
  );
}
