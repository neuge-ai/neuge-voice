import { type AgentEvent } from "../../api/agentApi";
import { useActiveTools } from "../../hooks/useActiveTools";

export function SideNav({ events = [], isOpen = false, onClose = () => {} }: { events?: AgentEvent[], isOpen?: boolean, onClose?: () => void }) {
  const { activeTools } = useActiveTools();

  const formatElapsed = (ms: number) => {
    const totalSeconds = Math.floor(ms / 1000);
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  };

  return (
    <nav className={`fixed inset-0 md:inset-auto md:left-0 md:top-0 h-full w-full md:w-[320px] flex flex-col transition-transform duration-300 md:translate-x-0 z-[70] md:z-40 ${isOpen ? 'translate-x-0' : '-translate-x-full'} glass-panel max-md:!bg-surface-dim/80 max-md:!backdrop-blur-3xl shadow-[8px_0_32px_rgba(0,0,0,0.3)] md:shadow-2xl border-y-0 border-l-0`}>
      
      {/* Mobile-only Header */}
      <div className="flex md:hidden justify-between items-center p-6 pt-12 glass-panel border-x-0 border-t-0 rounded-none shadow-none mb-4">
        <h2 className="text-[14px] font-label-caps text-primary tracking-[0.2em] uppercase font-bold">ACTIVE TOOLS</h2>
        <button 
          className="text-on-surface-variant hover:text-white p-2 rounded-full hover:bg-white/5 transition-colors" 
          onClick={onClose}
        >
          <span className="material-symbols-outlined">close</span>
        </button>
      </div>

      {/* Desktop-only Title */}
      <div className="hidden md:block font-headline-md text-headline-md tracking-[0.15em] mt-8 mb-6 font-semibold bg-gradient-to-r from-white to-slate-200 bg-clip-text text-transparent uppercase pl-8">NextGen Voice</div>
      
      <div className="flex flex-col gap-4 flex-grow px-6">
        <a className="glass-elevated p-4 rounded-xl flex items-center gap-4 transition-all group hover:bg-white/[0.05]" href="#">
          <div className="w-10 h-10 rounded-full glass-recessed flex items-center justify-center text-primary group-hover:text-primary-fixed transition-colors">
            <span className="material-symbols-outlined text-[20px]" style={{ fontVariationSettings: '"FILL" 1' }}>analytics</span>
          </div>
          <div>
            <div className="font-body-md text-body-md text-white font-semibold">Overview</div>
            <div className="font-metadata-sm text-[10px] text-slate-300 uppercase tracking-[0.15em] font-mono mt-1">Dashboard Active</div>
          </div>
        </a>
      </div>

      <div className="mt-auto mb-6 flex flex-col gap-3 z-10 relative px-6">
        <div className="font-label-caps text-label-caps text-slate-200 font-bold tracking-[0.2em] uppercase mb-4 px-2">Active Tools</div>
        
        <div className="flex flex-col gap-3">
          {activeTools.length === 0 ? (
             <div className="text-slate-300 text-sm italic px-2">No active tools...</div>
          ) : (
            activeTools.map((tool) => (
              <div key={tool.id} className="glass-recessed p-4 rounded-xl flex items-center justify-between group hover:bg-white/[0.02] transition-all border border-white/5">
                <div className="flex items-center gap-4">
                  <div className="w-8 h-8 rounded-full flex items-center justify-center text-slate-200 group-hover:text-secondary transition-colors">
                    <span className="material-symbols-outlined text-[18px]">
                      {tool.type === 'timer' ? 'timer' : tool.type === 'activity' ? 'directions_run' : 'routine'}
                    </span>
                  </div>
                  <div>
                    <div className="font-body-md text-sm text-white font-semibold tracking-wide truncate max-w-[140px]" title={tool.title || "Background Task"}>{tool.title || "Background Task"}</div>
                    <div className="font-metadata-sm text-[10px] text-slate-300 mt-0.5 uppercase tracking-wider">{tool.type}</div>
                  </div>
                </div>
                <div className="text-secondary font-mono text-[10px] font-bold pl-2 tracking-widest">{formatElapsed(tool.elapsed_ms)}</div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="flex md:flex-row flex-col gap-4 md:gap-6 mt-2 pt-6 pb-8 md:pb-8 border-t border-white/10 md:border-0 z-10 relative px-6">
        <a className="flex items-center space-x-3 text-slate-300 hover:text-white transition-colors w-full md:w-auto p-3 md:p-0 rounded-lg hover:bg-white/5 md:hover:bg-transparent group" href="#">
          <span className="material-symbols-outlined text-[16px]">help</span>
          <span className="font-label-caps text-[10px] md:text-label-caps tracking-widest uppercase font-bold">Help</span>
        </a>
        <a className="flex items-center space-x-3 text-slate-300 hover:text-white transition-colors w-full md:w-auto p-3 md:p-0 rounded-lg hover:bg-white/5 md:hover:bg-transparent group" href="#">
          <span className="material-symbols-outlined text-[16px]">rate_review</span>
          <span className="font-label-caps text-[10px] md:text-label-caps tracking-widest uppercase font-bold">Feedback</span>
        </a>
      </div>
    </nav>
  );
}
