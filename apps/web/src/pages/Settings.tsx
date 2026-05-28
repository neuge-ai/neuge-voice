import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";

export function Settings() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState("mcp");
  const [mcpTools, setMcpTools] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [cursorPos, setCursorPos] = useState({ x: -1000, y: -1000 });

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      setCursorPos({ x: e.clientX, y: e.clientY });
    };
    document.addEventListener("mousemove", handleMouseMove);
    return () => document.removeEventListener("mousemove", handleMouseMove);
  }, []);

  const fetchMcpTools = async () => {
    try {
      setLoading(true);
      const res = await fetch("/api/codex/mcp-tools");
      const data = await res.json();
      setMcpTools(data.servers || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "mcp") {
      fetchMcpTools();
    }
  }, [activeTab]);

  return (
    <div className="bg-background text-on-surface font-body-md h-screen w-screen overflow-hidden flex flex-col md:flex-row relative">
      <div 
        className="fixed w-[600px] h-[600px] rounded-full pointer-events-none z-[1] mix-blend-screen transition-opacity duration-300 will-change-transform" 
        style={{ 
          background: "radial-gradient(circle, rgba(4,251,251,0.04) 0%, rgba(160,120,255,0.02) 40%, transparent 70%)",
          left: `${cursorPos.x}px`,
          top: `${cursorPos.y}px`,
          transform: "translate(-50%, -50%)",
          opacity: cursorPos.x === -1000 ? 0 : 1
        }}
      ></div>

      {/* Mobile Nav */}
      <div className="md:hidden flex justify-between items-center px-4 py-4 border-b border-white/10 bg-surface/80 backdrop-blur-md z-50">
        <button onClick={() => navigate("/")} className="flex items-center gap-2 text-primary">
          <span className="material-symbols-outlined">arrow_back</span>
          <span className="font-feature-title text-feature-title">Settings</span>
        </button>
      </div>

      {/* Sidebar */}
      <nav className="hidden md:flex flex-col w-64 h-full bg-surface-dim border-r border-white/10 shadow-2xl z-40 p-4 pt-12 flex-shrink-0 relative">
        <button onClick={() => navigate("/")} className="flex items-center gap-2 text-on-surface-muted hover:text-primary transition-colors mb-8 group">
          <span className="material-symbols-outlined text-sm group-hover:-translate-x-1 transition-transform">arrow_back</span>
          <span className="font-label-sm text-label-sm text-on-surface">Back to App</span>
        </button>
        <div className="space-y-2 flex-1">
          <button 
            className="nav-btn w-full flex items-center justify-start gap-4 px-4 py-3 rounded-lg transition-all text-left group text-on-surface-muted hover:text-primary hover:bg-white/5 border border-transparent data-[state=active]:bg-white/10 data-[state=active]:text-primary data-[state=active]:border-white/10"
            data-state={activeTab === "general" ? "active" : undefined}
            onClick={() => setActiveTab("general")}
          >
            <span className="font-body-md text-body-md">General</span>
          </button>
          <button 
            className="nav-btn w-full flex items-center justify-start gap-4 px-4 py-3 rounded-lg transition-all text-left group text-on-surface-muted hover:text-primary hover:bg-white/5 border border-transparent data-[state=active]:bg-white/10 data-[state=active]:text-primary data-[state=active]:border-white/10"
            data-state={activeTab === "config" ? "active" : undefined}
            onClick={() => setActiveTab("config")}
          >
            <span className="font-body-md text-body-md">Configuration</span>
          </button>
          <button 
            className="nav-btn w-full flex items-center justify-start gap-4 px-4 py-3 rounded-lg transition-all text-left group text-on-surface-muted hover:text-primary hover:bg-white/5 border border-transparent data-[state=active]:bg-white/10 data-[state=active]:text-primary data-[state=active]:border-white/10"
            data-state={activeTab === "mcp" ? "active" : undefined}
            onClick={() => setActiveTab("mcp")}
          >
            <span className="font-body-md text-body-md">MCP Servers</span>
          </button>
        </div>
      </nav>

      {/* Main Content */}
      <main className="flex-1 overflow-y-auto relative z-10 p-6 md:p-12 lg:p-margin-desktop bg-[size:40px_40px] bg-blueprint-pattern">
        <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
          <div className="absolute top-[10%] left-[20%] w-[80%] h-[60%] bg-aether-purple/5 blur-[120px] rounded-full animate-orb-morph-slow mix-blend-screen"></div>
          <div className="absolute bottom-[10%] right-[10%] w-[70%] h-[50%] bg-secondary-fixed/5 blur-[120px] rounded-full animate-orb-morph-slow mix-blend-screen" style={{ animationDelay: "-5s", animationDuration: "20s" }}></div>
        </div>

        {activeTab === "general" && (
          <div className="max-w-4xl mx-auto animate-fade-in relative z-10">
            <header className="mb-12">
              <h1 className="font-headline-lg text-headline-lg text-primary mb-2">General</h1>
              <p className="font-body-md text-body-md text-on-surface-muted">Basic application settings</p>
            </header>
            <div className="glass-panel rounded-xl overflow-hidden flex flex-col">
              <div className="p-6 border-b border-white/5 flex items-center justify-between hover:bg-white/5 transition-colors">
                <div>
                  <h3 className="font-feature-title text-body-md text-primary mb-1">Theme</h3>
                  <p className="font-label-sm text-label-sm text-on-surface-muted">Choose the application appearance</p>
                </div>
                <select className="appearance-none bg-white/5 border border-white/10 rounded-lg py-2 pl-4 pr-10 font-body-md text-body-md text-primary focus:outline-none cursor-pointer">
                  <option value="dark">Dark Mode</option>
                </select>
              </div>
            </div>
          </div>
        )}

        {activeTab === "config" && (
          <div className="max-w-4xl mx-auto animate-fade-in relative z-10">
            <header className="mb-12">
              <h1 className="font-headline-lg text-headline-lg text-primary mb-2">Configuration</h1>
              <p className="font-body-md text-body-md text-on-surface-muted">Configure approval policy and sandbox settings</p>
            </header>
            <div className="glass-panel rounded-xl overflow-hidden flex flex-col">
              <div className="p-6 flex items-center justify-between hover:bg-white/5 transition-colors">
                <div>
                  <h3 className="font-feature-title text-body-md text-primary mb-1">Sandbox permissions</h3>
                  <p className="font-label-sm text-label-sm text-on-surface-muted">Choose how much the agent can do</p>
                </div>
                <select className="appearance-none bg-white/5 border border-white/10 rounded-lg py-2 pl-4 pr-10 font-body-md text-body-md text-primary focus:outline-none cursor-pointer">
                  <option value="full">Full Access</option>
                </select>
              </div>
            </div>
          </div>
        )}

        {activeTab === "mcp" && (
          <div className="max-w-5xl mx-auto animate-fade-in relative z-10">
            <header className="mb-12 flex flex-col md:flex-row md:items-end justify-between gap-6 border-b border-white/10 pb-6">
              <div>
                <h1 className="font-headline-lg text-headline-lg text-primary mb-2">MCP Servers</h1>
                <p className="font-body-md text-body-md text-on-surface-muted">Manage external tools available to the AI core</p>
              </div>
              <button className="px-6 py-3 rounded-[10px] bg-white/5 hover:bg-white/10 border border-white/10 hover:border-aether-purple/50 text-primary font-label-sm text-label-sm flex items-center gap-2 transition-all hover:scale-95 active:scale-90">
                <span className="material-symbols-outlined text-[18px]">add</span>
                Add New Server
              </button>
            </header>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {mcpTools.length === 0 && !loading && (
                <div className="text-on-surface-muted col-span-2 text-center py-12">No active MCP tools found. Click "Add New Server".</div>
              )}
              {loading && (
                <div className="text-on-surface-muted col-span-2 text-center py-12">Loading...</div>
              )}
              {mcpTools.map((tool) => (
                <div key={tool.name} className="glass-panel p-6 rounded-xl flex flex-col gap-4 group hover:bg-white/[0.05] transition-all duration-300 relative overflow-hidden">
                  <div className="flex justify-between items-start z-10">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg flex items-center justify-center border border-white/20 text-primary bg-white/10">
                        <span className="material-symbols-outlined">integration_instructions</span>
                      </div>
                      <div>
                        <h3 className="font-feature-title text-feature-title text-primary text-lg">{tool.name}</h3>
                        <span className="inline-block px-2 py-0.5 mt-1 rounded font-label-mono text-[10px] uppercase bg-white/10 text-primary border border-white/20">Active</span>
                      </div>
                    </div>
                  </div>
                  <p className="font-body-md text-body-md text-on-surface-muted flex-1 z-10 text-sm mt-2">
                    {tool.command} {tool.args?.join(" ")}
                  </p>
                  <div className="flex items-center gap-4 mt-4 pt-4 border-t border-white/5 z-10">
                    <button 
                      className="text-error/70 hover:text-error transition-colors flex items-center gap-1 font-label-sm text-label-sm px-3 py-1.5 rounded-md bg-white/5 hover:bg-error/10 border border-white/5 ml-auto"
                      onClick={async () => {
                        await fetch(`/api/codex/mcp-tools/${tool.name}`, { method: 'DELETE' });
                        fetchMcpTools();
                      }}
                    >
                      <span className="material-symbols-outlined text-[16px]">delete</span>
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
