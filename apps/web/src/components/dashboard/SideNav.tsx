import { useState, useEffect } from "react";
import { type AgentEvent, API_BASE } from "../../api/agentApi";
import { useActiveTools } from "../../hooks/useActiveTools";
import { Link } from "react-router-dom";

export function SideNav({ events = [], isOpen = false, onClose = () => {} }: { events?: AgentEvent[], isOpen?: boolean, onClose?: () => void }) {
  const { activeTools } = useActiveTools();
  const [healthState, setHealthState] = useState<'online' | 'config_missing' | 'offline' | 'loading'>('loading');

  useEffect(() => {
    const checkHealth = async () => {
      try {
        const healthRes = await fetch(`${API_BASE}/health`);
        if (!healthRes.ok) {
          setHealthState('offline');
          return;
        }
        
        const configRes = await fetch(`${API_BASE}/api/config/status`);
        if (!configRes.ok) {
          setHealthState('offline');
          return;
        }

        const configData = await configRes.json();
        if (configData.system_ready) {
          setHealthState('online');
        } else {
          setHealthState('config_missing');
        }
      } catch (err) {
        setHealthState('offline');
      }
    };

    checkHealth();
    const interval = setInterval(checkHealth, 5000);
    return () => clearInterval(interval);
  }, []);

  const formatElapsed = (ms: number) => {
    const totalSeconds = Math.floor(ms / 1000);
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  };

  return (
    <aside className={`w-72 h-full flex flex-col border-r border-white/10 bg-surface/80 backdrop-blur-md z-10 fixed md:relative left-0 top-0 transition-transform duration-300 md:translate-x-0 ${isOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}`}>
      
      {/* Header */}
      <div className="p-6 flex justify-between items-center">
        <h1 className="text-xl font-bold font-feature-title text-onSurface">Neuge</h1>
        <button className="md:hidden text-onSurfaceVariant hover:text-white p-2 rounded-full hover:bg-white/5 transition-colors" onClick={onClose}>
          <span className="material-symbols-outlined">close</span>
        </button>
      </div>

      {/* Navigation */}
      <nav className="px-4 py-2">
        <Link to="/settings" className="glass-panel rounded-xl p-3 flex items-center gap-4 cursor-pointer hover:bg-surfaceContainerHigh transition-colors group">
          <div className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors ${
          healthState === 'offline' ? 'bg-red-500/10 text-red-500' : 
            healthState === 'config_missing' ? 'bg-[#ffb869]/10 text-[#ffb869] shadow-[0_0_15px_rgba(255,184,105,0.2)]' : 
            healthState === 'online' ? 'bg-green-500/10 text-green-500' : 'bg-white/5 text-white/50'
          }`}>
            {healthState === 'online' ? (
              <span className="material-symbols-outlined text-[16px]">monitor_heart</span>
            ) : healthState === 'config_missing' ? (
              <span className="material-symbols-outlined text-[16px]">warning</span>
            ) : healthState === 'offline' ? (
              <span className="material-symbols-outlined text-[16px]">cloud_off</span>
            ) : (
              <span className="material-symbols-outlined text-[16px] animate-spin">sync</span>
            )}
          </div>
          <div>
            <div className="font-bold text-onSurface">Overview</div>
            <div className={`font-label-sm text-label-sm mt-0.5 ${
              healthState === 'offline' ? 'text-red-500' : 
              healthState === 'config_missing' ? 'text-[#ffb869]' : 
              healthState === 'online' ? 'text-green-500' : 'text-onSurfaceVariant'
            }`}>
              {healthState === 'online' ? 'System Ready' : 
               healthState === 'config_missing' ? 'Config Required' : 
               healthState === 'offline' ? 'Backend Offline' : 'Checking Status...'}
            </div>
          </div>
        </Link>
      </nav>

      {/* Active Tools */}
      <div className="flex-1 px-4 py-6 overflow-y-auto mt-8">
        <h2 className="font-label-sm text-label-sm text-onSurfaceVariant mb-4 px-2">Active Tools</h2>
        <div className="space-y-3">
          {activeTools.length === 0 ? (
            <div className="text-onSurfaceVariant text-sm italic px-2">No active tools...</div>
          ) : (
            activeTools.map((tool) => (
              <div key={tool.id} className="glass-panel rounded-xl p-4 hover:bg-surfaceContainer transition-colors cursor-pointer group">
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-3">
                    <svg className="text-onSurfaceVariant group-hover:text-primary transition-colors" fill="none" height="16" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="16" xmlns="http://www.w3.org/2000/svg"><path d="M12 20v-6M6 20V10M18 20V4"></path></svg>
                    <span className="font-bold text-onSurface truncate max-w-[140px]">{tool.title || "Background Task"}</span>
                  </div>
                  <span className="text-xs text-secondary font-mono">{formatElapsed(tool.elapsed_ms)}</span>
                </div>
                <div className="mt-3 text-xs text-onSurfaceVariant flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-primary">
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-primary"></span>
                    </span>
                    <span className="font-label-sm text-label-sm">ACTIVE</span>
                  </div>
                  <span className="font-label-sm text-label-sm text-onSurfaceVariant uppercase">{tool.type}</span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Footer Settings */}
      <div className="p-6 border-t border-white/10 flex justify-between items-center text-onSurfaceVariant">
        <Link to="/settings" className="flex-1 flex items-center gap-2 px-3 py-2 -ml-3 mr-2 rounded-lg hover:bg-white/5 hover:text-onSurface transition-colors font-label-sm text-label-sm">
          <svg fill="none" height="16" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="16" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
          Settings
        </Link>
        
        <div className="flex items-center gap-1 -mr-2">
          <button className="p-2 rounded-lg hover:bg-white/5 hover:text-onSurface transition-colors flex items-center justify-center" title="Help">
            <svg fill="none" height="16" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="16" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><path d="M12 17h.01"></path></svg>
          </button>
          <button className="p-2 rounded-lg hover:bg-white/5 hover:text-onSurface transition-colors flex items-center justify-center" title="Feedback">
            <svg fill="none" height="16" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="16" xmlns="http://www.w3.org/2000/svg"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
          </button>
        </div>
      </div>
    </aside>
  );
}
