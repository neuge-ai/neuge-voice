import React from "react";

export function SideNav() {
  return (
    <nav className="hidden md:flex flex-col h-full py-8 px-6 space-y-8 glass-panel fixed left-0 top-0 w-80 shadow-2xl z-40 border-y-0 border-l-0 border-r border-white/10">
      <div className="font-headline-md text-headline-md text-primary tracking-tight mb-6 font-medium">NextGen Voice</div>
      
      <div className="flex flex-col gap-2 flex-grow">
        <a className="glass-panel p-3 rounded-lg border border-secondary/40 flex items-center gap-3 bg-white/[0.05] transition-all group" href="#">
          <div className="w-8 h-8 rounded-full bg-secondary/10 flex items-center justify-center text-secondary">
            <span className="material-symbols-outlined text-[16px]" style={{ fontVariationSettings: '"FILL" 1, "wght" 400, "GRAD" 0, "opsz" 24' }}>analytics</span>
          </div>
          <div>
            <div className="font-metadata-sm text-metadata-sm text-secondary font-bold">Overview</div>
            <div className="font-metadata-sm text-[10px] text-on-surface-variant/70 uppercase tracking-wider">Dashboard Active</div>
          </div>
        </a>
      </div>

      <div className="mt-auto mb-6 flex flex-col gap-3 z-10 relative">
        <div className="font-label-caps text-label-caps text-on-surface-variant tracking-wider uppercase mb-2">Active Tools</div>
        
        <div className="glass-panel p-3 rounded-lg border border-white/5 group hover:border-primary/30 transition-colors mb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-surface-container flex items-center justify-center text-primary">
                <span className="material-symbols-outlined text-[16px]">sensors</span>
              </div>
              <div>
                <div className="font-metadata-sm text-metadata-sm text-on-surface">Session State</div>
                <div className="font-metadata-sm text-[10px] text-secondary font-mono uppercase tracking-wider">Active</div>
              </div>
            </div>
          </div>
        </div>

        <div className="glass-panel p-3 rounded-lg border border-white/5 flex items-center justify-between group hover:border-secondary/30 transition-colors">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-surface-container flex items-center justify-center text-secondary">
              <span className="material-symbols-outlined text-[16px]">routine</span>
            </div>
            <div>
              <div className="font-metadata-sm text-metadata-sm text-on-surface">Weather Check</div>
              <div className="font-metadata-sm text-[10px] text-on-surface-variant/70">Continuous Monitoring</div>
            </div>
          </div>
          <div className="font-metadata-sm text-secondary font-mono text-xs pulse-colon">00:00</div>
        </div>
      </div>

      <div className="flex gap-6 mt-6 border-t border-white/10 pt-6 z-10 relative">
        <a className="flex items-center gap-2 text-on-surface-variant hover:text-secondary transition-colors" href="#">
          <span className="material-symbols-outlined text-[18px]">help</span>
          <span className="font-label-caps text-label-caps font-bold tracking-wider">Help</span>
        </a>
        <a className="flex items-center gap-2 text-on-surface-variant hover:text-secondary transition-colors" href="#">
          <span className="material-symbols-outlined text-[18px]">rate_review</span>
          <span className="font-label-caps text-label-caps font-bold tracking-wider">Feedback</span>
        </a>
      </div>
    </nav>
  );
}
