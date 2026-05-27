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
    <div className={`fixed right-0 top-0 w-full md:w-[400px] lg:w-[480px] h-full glass-panel p-6 flex flex-col z-40 transform transition-transform duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] pt-24 shadow-[-20px_0_40px_rgba(0,0,0,0.3)] border-y-0 border-r-0 ${isOpen ? 'translate-x-0' : 'translate-x-full'}`}>
      <div className="flex items-center justify-center mb-8">
        <h2 className="font-label-caps text-label-caps text-on-surface-variant tracking-[0.2em] uppercase font-bold text-center">Activity Log</h2>
      </div>
      
      <div className="flex-grow overflow-y-auto pr-2 space-y-6 scrollbar-hide flex flex-col-reverse pb-24 md:pb-0">
        {chatEvents.map((event, idx) => {
          const timeStr = `[${formatTime(new Date(event.created_at || Date.now()))}]`;
          
          const isUser = event.event === 'transcript_final' || event.event === 'user_turn';
          
          if (isUser) {
            return (
              <div key={idx} className="flex flex-col items-end message-reveal">
                <div className="flex items-center gap-2 mb-1 px-1">
                  <span className="font-metadata-sm text-metadata-sm text-on-surface-variant/50 font-mono">{timeStr}</span>
                  <span className="font-metadata-sm text-metadata-sm text-primary font-bold">User</span>
                </div>
                <div className="rounded-2xl rounded-tr-sm p-4 bg-gradient-to-br from-primary/20 to-secondary/20 border border-primary/20 backdrop-blur-md max-w-[85%]">
                  <p className="font-body-md text-body-md text-on-surface">"{event.text}"</p>
                </div>
              </div>
            );
          } else {
            return (
              <div key={idx} className="flex flex-col items-start message-reveal">
                <div className="flex items-center gap-2 mb-1 px-1">
                  <span className="font-metadata-sm text-metadata-sm text-secondary font-bold">Assistant</span>
                  <span className="font-metadata-sm text-metadata-sm text-on-surface-variant/50 font-mono">{timeStr}</span>
                </div>
                <div className="glass-panel rounded-2xl rounded-tl-sm p-4 bg-surface-container/60 border border-white/5 max-w-[85%]">
                  <p className="font-body-md text-body-md text-on-surface leading-relaxed">{event.text}</p>
                </div>
              </div>
            );
          }
        })}
      </div>
    </div>
  );
}
