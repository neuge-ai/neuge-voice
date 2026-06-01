import React, { useState, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { API_BASE, openConfigFile } from "../api/agentApi";

// Custom premium select component
function CustomSelect({ value, onChange, options, loadingText = "Loading..." }: { value: string, onChange: (v: string) => void, options: {value: string, label: string}[], loadingText?: string }) {
  const [isOpen, setIsOpen] = useState(false);
  const selectedOption = options.find(o => o.value === value);

  return (
    <div className="relative w-64">
      <div 
        className="bg-white/5 hover:bg-white/10 border border-white/10 rounded-[12px] py-1.5 px-3 font-label-sm text-sm text-primary cursor-pointer flex justify-between items-center transition-colors shadow-sm"
        onClick={() => setIsOpen(!isOpen)}
      >
        <span className="truncate">{selectedOption ? selectedOption.label : (options.length === 0 ? loadingText : value)}</span>
        <span className="material-symbols-outlined text-[18px] opacity-70 transition-transform duration-300" style={{ transform: isOpen ? 'rotate(180deg)' : 'rotate(0)' }}>expand_more</span>
      </div>
      {isOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setIsOpen(false)}></div>
          <div className="absolute top-full left-0 mt-2 w-full glass-panel border border-white/10 rounded-[12px] overflow-hidden z-50 shadow-2xl animate-fade-in max-h-[130px] overflow-y-auto">
            {options.map((opt) => (
              <div 
                key={opt.value}
                className={`py-1.5 px-3 cursor-pointer font-label-sm text-sm hover:bg-white/10 transition-colors ${value === opt.value ? 'bg-primary/10 text-primary' : 'text-onSurface'}`}
                onClick={() => {
                  onChange(opt.value);
                  setIsOpen(false);
                }}
              >
                {opt.label}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export function Settings() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") || "general";

  const setActiveTab = (tab: string) => {
    setSearchParams({ tab });
  };
  const [mcpTools, setMcpTools] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [cursorPos, setCursorPos] = useState({ x: -1000, y: -1000 });
  const [settings, setSettings] = useState<any>({});
  const [configStatus, setConfigStatus] = useState<any>({});
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({});
  const [configOpenError, setConfigOpenError] = useState<string | null>(null);

  const getRequiredSecrets = () => {
    if (!configStatus?.active_config || !configStatus?.providers_schema) return [];
    
    const keys: { id: string, label: string, categories: string[], is_configured: boolean }[] = [];
    const addKey = (id: string, label: string, category: string, is_configured: boolean) => {
      const existing = keys.find(k => k.id === id);
      if (!existing) {
        keys.push({ id, label, categories: [category], is_configured });
      } else {
        if (!existing.categories.includes(category)) {
          existing.categories.push(category);
        }
      }
    };

    const categories = ['stt', 'tts', 'llm'];
    categories.forEach(category => {
      const activeProviderId = configStatus.active_config[category];
      const schemaList = configStatus.providers_schema[category] || [];
      const provider = schemaList.find((p: any) => p.id === activeProviderId);
      if (provider && provider.required_keys) {
        provider.required_keys.forEach((reqKey: any) => {
          let label = reqKey.id.split('_').map((w: string) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
          label = label.replace(/Nvidia/i, "NVIDIA").replace(/Api/i, "API").replace(/Groq/i, "Groq").replace(/Openai/i, "OpenAI");
          addKey(reqKey.id, label, category.toUpperCase(), reqKey.is_configured);
        });
      }
    });

    if (settings.codex_model && (settings.codex_model.includes("gpt") || settings.codex_model.includes("o1") || settings.codex_model.includes("o3"))) {
      const openaiSchema = configStatus.providers_schema?.llm?.find((p: any) => p.id === 'openai');
      const isConfigured = openaiSchema?.required_keys?.find((k: any) => k.id === 'openai_api_key')?.is_configured || false;
      addKey("openai_api_key", "OpenAI API Key", "background Codex Agent", isConfigured);
    }

    return keys;
  };

  const dynamicSecrets = getRequiredSecrets();

  const handleOpenConfig = async () => {
    setConfigOpenError(null);
    try {
      const result = await openConfigFile();
      if (!result.opened) {
        setConfigOpenError(result.path);
      }
    } catch (e) {
      const fallbackPath = configStatus?.config_path;
      if (fallbackPath) {
        setConfigOpenError(fallbackPath);
      } else {
        setConfigOpenError(
          e instanceof Error ? e.message : "Could not reach the local backend.",
        );
      }
      console.error(e);
    }
  };

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
      const res = await fetch(`${API_BASE}/api/codex/mcp-tools`);
      const data = await res.json();
      setMcpTools(data.servers || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };
  const fetchSettings = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/settings`);
      const data = await res.json();
      setSettings(data);
    } catch (e) {
      console.error(e);
    }
  };

  const fetchConfigStatus = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/config/status`);
      const data = await res.json();
      setConfigStatus(data);
    } catch (e) {
      console.error(e);
    }
  };

  const handleSettingChange = async (key: string, value: string) => {
    setSettings((prev: any) => ({ ...prev, [key]: value }));
    try {
      await fetch(`${API_BASE}/api/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: value })
      });
      // Refresh config status after setting change so dynamic secrets update
      fetchConfigStatus();
    } catch (e) {
      console.error("Failed to save setting:", e);
    }
  };

  const handleSaveSecret = async (keyName: string) => {
    const value = secretInputs[keyName];
    if (!value) return;

    try {
      await fetch(`${API_BASE}/api/settings/secrets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [keyName]: value })
      });
      setSecretInputs(prev => ({ ...prev, [keyName]: "" }));
      fetchConfigStatus();
    } catch (e) {
      console.error("Failed to save secret:", e);
    }
  };


  useEffect(() => {
    if (activeTab === "mcp") {
      fetchMcpTools();
    } else if (activeTab === "config") {
      fetchSettings();
      fetchConfigStatus();
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
      <nav className="hidden md:flex flex-col w-72 h-full bg-surface/80 backdrop-blur-md border-r border-white/10 shadow-2xl z-40 p-4 pt-12 relative">
        <button onClick={() => navigate("/")} className="flex items-center gap-2 text-on-surface-muted hover:text-primary transition-colors mb-8 group">
          <span className="material-symbols-outlined text-sm group-hover:-translate-x-1 transition-transform">arrow_back</span>
          <span className="font-label-sm text-label-sm text-on-surface">Back to Home</span>
        </button>
        <div className="space-y-2 flex-1">
          <button 
            className="nav-btn w-full flex items-center justify-start gap-3 px-3 py-2.5 rounded-[12px] transition-all text-left group text-on-surface-muted hover:text-primary hover:bg-white/5 border border-transparent data-[state=active]:bg-white/10 data-[state=active]:text-primary data-[state=active]:border-white/10"
            data-state={activeTab === "general" ? "active" : undefined}
            onClick={() => setActiveTab("general")}
          >
            <span className="font-label-sm text-[14px] tracking-wide">General</span>
          </button>
          <button 
            className="nav-btn w-full flex items-center justify-start gap-3 px-3 py-2.5 rounded-[12px] transition-all text-left group text-on-surface-muted hover:text-primary hover:bg-white/5 border border-transparent data-[state=active]:bg-white/10 data-[state=active]:text-primary data-[state=active]:border-white/10"
            data-state={activeTab === "config" ? "active" : undefined}
            onClick={() => setActiveTab("config")}
          >
            <span className="font-label-sm text-[14px] tracking-wide">Configuration</span>
          </button>
          <button 
            className="nav-btn w-full flex items-center justify-start gap-3 px-3 py-2.5 rounded-[12px] transition-all text-left group text-on-surface-muted hover:text-primary hover:bg-white/5 border border-transparent data-[state=active]:bg-white/10 data-[state=active]:text-primary data-[state=active]:border-white/10"
            data-state={activeTab === "mcp" ? "active" : undefined}
            onClick={() => setActiveTab("mcp")}
          >
            <span className="font-label-sm text-[14px] tracking-wide">MCP Servers</span>
          </button>
        </div>
      </nav>

      {/* Main Content */}
      <main className="flex-1 overflow-y-auto relative z-10 bg-background">
        {/* Background Dots */}
        <div className="absolute inset-0 bg-[size:24px_24px] bg-theme-dots opacity-30 pointer-events-none z-0 mask-image-radial-gradient"></div>
        <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
          <div className="absolute top-[10%] left-[20%] w-[80%] h-[60%] bg-aether-purple/10 blur-[120px] rounded-full animate-orb-morph-slow mix-blend-screen"></div>
          <div className="absolute bottom-[10%] right-[10%] w-[70%] h-[50%] bg-cyanCore/10 blur-[120px] rounded-full animate-orb-morph-slow mix-blend-screen" style={{ animationDelay: "-5s", animationDuration: "20s" }}></div>
        </div>

        <div className="p-6 md:p-12 lg:p-margin-desktop relative z-10">

        {activeTab === "general" && (
          <div className="max-w-4xl mx-auto animate-fade-in relative z-10">
            <header className="mb-12">
              <h1 className="font-headline-lg text-headline-lg text-primary mb-2">General</h1>
              <p className="font-body-md text-body-md text-on-surface-muted">Basic application settings</p>
            </header>
            <div className="glass-panel rounded-xl flex flex-col">
              <div className="px-6 py-3 border-b border-white/5 flex items-center justify-between hover:bg-white/5 transition-colors relative z-30 rounded-xl">
                <div>
                  <h3 className="font-feature-title text-body-md text-primary mb-1">Theme</h3>
                  <p className="font-label-sm text-label-sm text-on-surface-muted">Choose the application appearance</p>
                </div>
                <CustomSelect 
                  value="dark" 
                  onChange={() => {}} 
                  options={[{ value: "dark", label: "Dark Mode" }]} 
                />
              </div>
            </div>
          </div>
        )}

        {activeTab === "config" && (
          <div className="max-w-4xl mx-auto animate-fade-in relative z-10">
            <header className="mb-12">
              <h1 className="font-headline-lg text-headline-lg text-primary mb-2">Configuration</h1>
              <p className="font-body-md text-body-md text-on-surface-muted">Manage providers and API keys.</p>
            </header>
            
            <div className="glass-panel rounded-xl flex flex-col mb-8 relative z-20">
              <div 
                className="px-6 py-4 flex items-center justify-between hover:bg-white/5 transition-colors cursor-pointer group border-b border-white/5 rounded-t-xl relative z-40" 
                onClick={handleOpenConfig}
              >
                <div className="font-body-md text-body-md text-primary">User config</div>
                <div className="flex items-center gap-1 text-on-surface-muted group-hover:text-white transition-colors text-sm">
                  Open config.toml <span className="material-symbols-outlined text-[14px]">north_east</span>
                </div>
              </div>

              {configOpenError && (
                <div className="px-6 py-4 border-b border-white/5 bg-white/5">
                  <p className="font-label-sm text-label-sm text-on-surface-muted mb-2">
                    Could not open automatically — copy the path and open manually:
                  </p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 text-sm text-primary bg-black/20 rounded px-3 py-2 truncate">{configOpenError}</code>
                    <button
                      type="button"
                      className="text-sm text-on-surface-muted hover:text-white transition-colors shrink-0"
                      onClick={() => navigator.clipboard.writeText(configOpenError)}
                    >
                      Copy
                    </button>
                  </div>
                </div>
              )}

              {/* Voice Routing Provider */}
              <div className="px-6 py-4 flex items-center justify-between hover:bg-white/5 transition-colors border-b border-white/5 relative z-30">
                <div>
                  <h3 className="font-feature-title text-body-md text-primary mb-1">Chat Model Provider</h3>
                  <p className="font-label-sm text-label-sm text-on-surface-muted">For interaction model</p>
                </div>
                <CustomSelect 
                  value={settings.router_model === "default" ? "default" : (configStatus?.active_config?.llm || "groq")}
                  onChange={(val) => {
                    if (val === "default") {
                      handleSettingChange("router_model", "default");
                      return;
                    }
                    let defaultModel = "groq/qwen/qwen3-32b";
                    if (val === "openai") defaultModel = "openai/gpt-4o";
                    else if (val === "anthropic") defaultModel = "anthropic/claude-3-5-sonnet-20240620";
                    handleSettingChange("router_model", defaultModel);
                  }}
                  options={[
                    ...(configStatus?.default_config?.llm && configStatus?.providers_schema?.llm ? [{
                      value: "default",
                      label: `Default (${configStatus.providers_schema.llm.find((p: any) => p.id === configStatus.default_config?.llm)?.name || configStatus.default_config?.llm})`
                    }] : []),
                    ...(configStatus?.providers_schema?.llm?.map((p: any) => ({ value: p.id, label: p.name })) || [])
                  ]}
                />
              </div>

              {/* TTS Provider */}
              <div className="px-6 py-4 flex items-center justify-between hover:bg-white/5 transition-colors border-b border-white/5 relative z-20">
                <div>
                  <h3 className="font-feature-title text-body-md text-primary mb-1">TTS Provider</h3>
                  <p className="font-label-sm text-label-sm text-on-surface-muted">Text-to-Speech engine</p>
                </div>
                <CustomSelect 
                  value={settings.tts_provider || "browser_dev"}
                  onChange={(val) => handleSettingChange("tts_provider", val)}
                  options={[
                    ...(configStatus?.default_config?.tts && configStatus?.providers_schema?.tts ? [{
                      value: "default",
                      label: `Default (${configStatus.providers_schema.tts.find((p: any) => p.id === configStatus.default_config?.tts)?.name || configStatus.default_config?.tts})`
                    }] : []),
                    ...(configStatus?.providers_schema?.tts?.map((p: any) => ({ value: p.id, label: p.name })) || [])
                  ]}
                />
              </div>

              {/* STT Provider */}
              <div className="px-6 py-4 flex items-center justify-between hover:bg-white/5 transition-colors rounded-b-xl relative z-10">
                <div>
                  <h3 className="font-feature-title text-body-md text-primary mb-1">STT Provider</h3>
                  <p className="font-label-sm text-label-sm text-on-surface-muted">Speech-to-Text engine</p>
                </div>
                <CustomSelect 
                  value={settings.stt_provider || "fake"}
                  onChange={(val) => handleSettingChange("stt_provider", val)}
                  options={[
                    ...(configStatus?.default_config?.stt && configStatus?.providers_schema?.stt ? [{
                      value: "default",
                      label: `Default (${configStatus.providers_schema.stt.find((p: any) => p.id === configStatus.default_config?.stt)?.name || configStatus.default_config?.stt})`
                    }] : []),
                    ...(configStatus?.providers_schema?.stt?.map((p: any) => ({ value: p.id, label: p.name })) || [])
                  ]}
                />
              </div>
            </div>

            <h2 className="font-feature-title text-body-md text-on-surface-muted mb-4 mt-8">API Keys & Secrets</h2>
            <div className="glass-panel rounded-xl flex flex-col relative z-10">
              {dynamicSecrets.length === 0 && (
                <div className="p-6 text-on-surface-muted text-center font-body-md">No API keys required for current configuration.</div>
              )}
              {dynamicSecrets.map((secret, idx) => (
                <div key={secret.id} className={`px-6 py-4 flex items-center justify-between hover:bg-white/5 transition-colors ${idx !== dynamicSecrets.length - 1 ? 'border-b border-white/5' : ''} ${idx === 0 ? 'rounded-t-xl' : ''} ${idx === dynamicSecrets.length - 1 ? 'rounded-b-xl' : ''}`}>
                  <div className="flex flex-col gap-1 w-1/3">
                    <div className="flex items-center gap-2">
                      <h3 className="font-feature-title text-body-md text-primary">{secret.label}</h3>
                      {secret.is_configured ? (
                        <span className="flex items-center gap-1 text-[10px] uppercase font-bold text-[#4ade80] bg-[#4ade80]/10 px-2 py-0.5 rounded border border-[#4ade80]/20">
                          <span className="w-1.5 h-1.5 rounded-full bg-[#4ade80]"></span> Configured
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-[10px] uppercase font-bold text-error bg-error/10 px-2 py-0.5 rounded border border-error/20">
                          <span className="w-1.5 h-1.5 rounded-full bg-error"></span> Missing
                        </span>
                      )}
                    </div>
                    <p className="font-label-sm text-label-sm text-on-surface-muted mt-1">Required for {secret.categories.join(" + ")}</p>
                  </div>
                  <div className="flex items-center gap-3 w-1/2 justify-end">
                    <input 
                      type="password" 
                      placeholder="Enter new key..." 
                      className="flex-1 max-w-[300px] bg-white/5 hover:bg-white/10 border border-white/10 rounded-full py-2.5 px-5 font-body-md text-body-md text-primary focus:outline-none focus:border-cyanCore/50 focus:shadow-[0_0_10px_rgba(4,251,251,0.2)] transition-all placeholder:text-white/30"
                      value={secretInputs[secret.id] || ""}
                      onChange={(e) => setSecretInputs(prev => ({ ...prev, [secret.id]: e.target.value }))}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleSaveSecret(secret.id);
                      }}
                    />
                    <button 
                      onClick={() => handleSaveSecret(secret.id)}
                      disabled={!secretInputs[secret.id]}
                      className={`px-6 py-2.5 rounded-full font-label-sm text-sm font-bold shadow-lg transition-all duration-300 ${!secretInputs[secret.id] ? 'bg-white/10 text-white shadow-none cursor-not-allowed' : 'bg-white text-black hover:bg-gray-100 hover:scale-105 hover:shadow-[0_4px_14px_rgba(255,255,255,0.4)] active:scale-95'}`}
                    >
                      Save
                    </button>
                  </div>
                </div>
              ))}
            </div>

          </div>
        )}

        {activeTab === "mcp" && (
          <div className="max-w-5xl mx-auto animate-fade-in relative z-10">
            <header className="mb-12 flex flex-col md:flex-row md:items-end justify-between gap-6 border-b border-white/10 pb-6">
              <div>
                <h1 className="font-headline-lg text-headline-lg text-primary mb-2">MCP Servers</h1>
                <p className="font-body-md text-body-md text-on-surface-muted">Manage external tools available to Neuge</p>
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
                <div key={tool.name} className="glass-panel px-6 py-5 rounded-xl flex flex-col gap-4 group hover:bg-white/[0.05] transition-all duration-300 relative overflow-hidden">
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
        </div>
      </main>
    </div>
  );
}
