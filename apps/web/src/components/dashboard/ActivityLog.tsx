import React from "react";
import { type AgentEvent } from "../../api/agentApi";
import { type BrowserVoiceEvent } from "../../realtime/browserVoiceTransport";

function formatTime(date: Date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function ActivityLog({ isOpen, onClose, events, voiceEvents, isAgentListening = false }: { isOpen: boolean; onClose: () => void; events: AgentEvent[]; voiceEvents: BrowserVoiceEvent[]; isAgentListening?: boolean }) {
  // Filter for events that actually have text to show in the log
  const chatEvents = voiceEvents.filter(e => e.text && (e.event === 'transcript_final' || e.event === 'assistant_response' || e.event === 'user_turn' || e.event === 'delivery_ready'));

  return (
    <aside className={`
      fixed inset-0 z-[60] transform transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)]
      ${isOpen ? 'translate-x-0' : 'translate-x-full'}
      md:relative md:inset-auto md:transform-none md:transition-[width] md:overflow-hidden md:z-10
      ${isOpen ? 'md:w-[400px]' : 'md:w-0'}
      h-full
    `}>
      <div className="w-full md:w-[400px] h-full flex flex-col border-l border-white/10 bg-surface md:bg-surface/80 md:backdrop-blur-sm relative">
        <div className="p-6 border-b border-white/10 flex justify-center relative">
          <h2 className="text-xs font-bold text-onSurfaceVariant">Activity Log</h2>
          <button 
            className="md:hidden absolute right-6 top-4 text-onSurfaceVariant hover:text-white p-2 rounded-full hover:bg-white/5 transition-colors" 
            onClick={onClose}
          >
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>
        
        {/* Chat History */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6 flex flex-col-reverse pb-24 md:pb-6">
          {chatEvents.length > 0 && isAgentListening && (
            <div className="flex flex-col items-start gap-1 w-full pt-4">
              <div className="glass-panel px-4 py-2 rounded-2xl rounded-tl-sm flex items-center gap-1.5 w-fit">
                <div className="w-1.5 h-1.5 rounded-full bg-primary/50 animate-pulse-slow" style={{ animationDelay: '0ms' }}></div>
                <div className="w-1.5 h-1.5 rounded-full bg-primary/50 animate-pulse-slow" style={{ animationDelay: '150ms' }}></div>
                <div className="w-1.5 h-1.5 rounded-full bg-primary/50 animate-pulse-slow" style={{ animationDelay: '300ms' }}></div>
              </div>
            </div>
          )}
          
          {chatEvents.map((event, idx) => {
            const timeStr = `[${formatTime(new Date(event.created_at || Date.now()))}]`;
            const isUser = event.event === 'transcript_final' || event.event === 'user_turn';
            
            if (isUser) {
              return (
                <div key={idx} className="flex flex-col items-end gap-1 w-full">
                  <div className="text-[10px] text-onSurfaceVariant font-mono px-1">
                    <span className="text-onSurfaceVariant opacity-50">{timeStr}</span> <span className="text-secondary font-bold">User</span>
                  </div>
                  <div className="bg-surfaceContainerHigh border border-white/10 text-onSurface px-4 py-3 rounded-2xl rounded-tr-sm max-w-[85%] leading-relaxed shadow-sm">
                    "{event.text}"
                  </div>
                </div>
              );
            } else {
              return (
                <div key={idx} className="flex flex-col items-start gap-1 w-full">
                  <div className="text-[10px] text-onSurfaceVariant font-mono px-1">
                    <span className="text-primary font-bold">Assistant</span> <span className="text-onSurfaceVariant opacity-50">{timeStr}</span>
                  </div>
                  <div className="glass-panel text-onSurface px-4 py-3 rounded-2xl rounded-tl-sm max-w-[90%] leading-relaxed">
                    {event.text}
                  </div>
                </div>
              );
            }
          })}
        </div>
      </div>
    </aside>
  );
}
