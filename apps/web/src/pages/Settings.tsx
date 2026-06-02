import React, { useState, useEffect, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { openConfigFile } from "../api/agentApi";
import { requestJson } from "../api/client";
import type {
  AppSettings,
  ConfigStatusResponse,
  DynamicSecret,
  McpServerEntry,
  McpToolsResponse,
} from "../api/types";
import { McpServerList } from "../components/settings/McpServerList";
import { SecretsPanel } from "../components/settings/SecretsPanel";
import { SettingsProviderSelect } from "../components/settings/SettingsProviderSelect";

function CustomSelect({ value, onChange, options }: { value: string, onChange: (v: string) => void, options: {value: string, label: string}[] }) {
  const [isOpen, setIsOpen] = useState(false);
  const selectedOption = options.find(o => o.value === value);

  return (
    <div className="relative w-64">
      <div 
        className="bg-white/5 hover:bg-white/10 border border-white/10 rounded-[12px] py-1.5 px-3 font-label-sm text-sm text-primary cursor-pointer flex justify-between items-center transition-colors shadow-sm"
        onClick={() => setIsOpen(!isOpen)}
      >
        <span className="truncate">{selectedOption ? selectedOption.label : value}</span>
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

function buildDynamicSecrets(settings: AppSettings, configStatus: ConfigStatusResponse): DynamicSecret[] {
  if (!configStatus.active_config || !configStatus.providers_schema) return [];

  const keys: DynamicSecret[] = [];
  const addKey = (id: string, label: string, category: string, is_configured: boolean) => {
    const existing = keys.find((k) => k.id === id);
    if (!existing) {
      keys.push({ id, label, categories: [category], is_configured });
    } else if (!existing.categories.includes(category)) {
      existing.categories.push(category);
    }
  };

  const categories = ["stt", "tts", "llm"] as const;
  categories.forEach((category) => {
    const activeProviderId = configStatus.active_config[category];
    const schemaList = configStatus.providers_schema[category] || [];
    const provider = schemaList.find((p) => p.id === activeProviderId);
    if (provider?.required_keys) {
      provider.required_keys.forEach((reqKey) => {
        let label = reqKey.id.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
        label = label.replace(/Nvidia/i, "NVIDIA").replace(/Api/i, "API").replace(/Groq/i, "Groq").replace(/Openai/i, "OpenAI");
        addKey(reqKey.id, label, category.toUpperCase(), reqKey.is_configured);
      });
    }
  });

  if (settings.codex_model && (settings.codex_model.includes("gpt") || settings.codex_model.includes("o1") || settings.codex_model.includes("o3"))) {
    const openaiSchema = configStatus.providers_schema.llm.find((p) => p.id === "openai");
    const isConfigured = openaiSchema?.required_keys?.find((k) => k.id === "openai_api_key")?.is_configured || false;
    addKey("openai_api_key", "OpenAI API Key", "background Codex Agent", isConfigured);
  }

  return keys;
}

export function Settings() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") || "general";

  const setActiveTab = (tab: string) => {
    setSearchParams({ tab });
  };
  const [mcpTools, setMcpTools] = useState<McpServerEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [cursorPos, setCursorPos] = useState({ x: -1000, y: -1000 });
  const [settings, setSettings] = useState<AppSettings>({});
  const [configStatus, setConfigStatus] = useState<ConfigStatusResponse>({
    system_ready: true,
    missing_capabilities: [],
    active_config: { stt: "fake", tts: "browser_dev", llm: "groq" },
    default_config: { stt: "fake", tts: "browser_dev", llm: "groq" },
    providers_schema: { stt: [], tts: [], llm: [] },
    config_path: "",
  });
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({});
  const [configOpenError, setConfigOpenError] = useState<string | null>(null);

  const dynamicSecrets = useMemo(() => buildDynamicSecrets(settings, configStatus), [settings, configStatus]);

  const handleOpenConfig = async () => {
    setConfigOpenError(null);
    try {
      const result = await openConfigFile();
      if (!result.opened) {
        setConfigOpenError(result.path);
      }
    } catch (e) {
      const fallbackPath = configStatus.config_path;
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
      const data = await requestJson<McpToolsResponse>("/api/codex/mcp-tools");
      setMcpTools(data.servers || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };
  const fetchSettings = async () => {
    try {
      const data = await requestJson<AppSettings>("/api/settings");
      setSettings(data);
    } catch (e) {
      console.error(e);
    }
  };

  const fetchConfigStatus = async () => {
    try {
      const data = await requestJson<ConfigStatusResponse>("/api/config/status");
      setConfigStatus(data);
    } catch (e) {
      console.error(e);
    }
  };

  const handleSettingChange = async (key: string, value: string) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
    try {
      await requestJson("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: value }),
      });
      fetchConfigStatus();
    } catch (e) {
      console.error("Failed to save setting:", e);
    }
  };

  const handleSaveSecret = async (keyName: string) => {
    const value = secretInputs[keyName];
    if (!value) return;

    try {
      await requestJson("/api/settings/secrets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [keyName]: value }),
      });
      setSecretInputs((prev) => ({ ...prev, [keyName]: "" }));
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

            {(configStatus.diagnostics?.length ?? 0) > 0 && (
              <div className="glass-panel rounded-xl mb-6 px-6 py-4 space-y-2">
                {configStatus.diagnostics?.map((item) => (
                  <div
                    key={`${item.code}-${item.message}`}
                    className={`text-sm rounded px-3 py-2 ${
                      item.severity === "error"
                        ? "bg-red-500/10 text-red-200"
                        : item.severity === "warning"
                          ? "bg-amber-500/10 text-amber-100"
                          : "bg-white/5 text-on-surface-muted"
                    }`}
                  >
                    {item.message}
                  </div>
                ))}
              </div>
            )}
            
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

              <SettingsProviderSelect
                settings={settings}
                configStatus={configStatus}
                onSettingChange={handleSettingChange}
              />
            </div>

            <SecretsPanel
              secrets={dynamicSecrets}
              secretInputs={secretInputs}
              onSecretInputChange={(key, value) => setSecretInputs((prev) => ({ ...prev, [key]: value }))}
              onSaveSecret={handleSaveSecret}
            />

          </div>
        )}

        {activeTab === "mcp" && (
          <McpServerList mcpTools={mcpTools} loading={loading} onRefresh={fetchMcpTools} />
        )}
        </div>
      </main>
    </div>
  );
}
