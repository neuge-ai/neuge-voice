import React, { useState } from "react";
import type { ActiveConfig, AppSettings, ConfigStatusResponse } from "../../api/types";

type SelectOption = { value: string; label: string };

function CustomSelect({
  value,
  onChange,
  options,
  loadingText = "Loading...",
}: {
  value: string;
  onChange: (v: string) => void;
  options: SelectOption[];
  loadingText?: string;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const selectedOption = options.find((o) => o.value === value);

  return (
    <div className="relative w-64">
      <div
        className="bg-white/5 hover:bg-white/10 border border-white/10 rounded-[12px] py-1.5 px-3 font-label-sm text-sm text-primary cursor-pointer flex justify-between items-center transition-colors shadow-sm"
        onClick={() => setIsOpen(!isOpen)}
      >
        <span className="truncate">
          {selectedOption ? selectedOption.label : options.length === 0 ? loadingText : value}
        </span>
        <span
          className="material-symbols-outlined text-[18px] opacity-70 transition-transform duration-300"
          style={{ transform: isOpen ? "rotate(180deg)" : "rotate(0)" }}
        >
          expand_more
        </span>
      </div>
      {isOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setIsOpen(false)}></div>
          <div className="absolute top-full left-0 mt-2 w-full glass-panel border border-white/10 rounded-[12px] overflow-hidden z-50 shadow-2xl animate-fade-in max-h-[130px] overflow-y-auto">
            {options.map((opt) => (
              <div
                key={opt.value}
                className={`py-1.5 px-3 cursor-pointer font-label-sm text-sm hover:bg-white/10 transition-colors ${value === opt.value ? "bg-primary/10 text-primary" : "text-onSurface"}`}
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

type SettingsProviderSelectProps = {
  settings: AppSettings;
  configStatus: ConfigStatusResponse;
  onSettingChange: (key: string, value: string) => void;
};

function defaultOptionLabel(
  configStatus: ConfigStatusResponse,
  category: keyof ActiveConfig,
): SelectOption[] {
  const defaultId = configStatus.default_config?.[category];
  const schemaList = configStatus.providers_schema?.[category] || [];
  if (!defaultId || schemaList.length === 0) {
    return [];
  }
  const provider = schemaList.find((p) => p.id === defaultId);
  return [
    {
      value: "default",
      label: `Default (${provider?.name || defaultId})`,
    },
  ];
}

export function SettingsProviderSelect({
  settings,
  configStatus,
  onSettingChange,
}: SettingsProviderSelectProps) {
  const llmOptions: SelectOption[] = [
    ...defaultOptionLabel(configStatus, "llm"),
    ...(configStatus.providers_schema?.llm?.map((p) => ({ value: p.id, label: p.name })) || []),
  ];

  const ttsOptions: SelectOption[] = [
    ...defaultOptionLabel(configStatus, "tts"),
    ...(configStatus.providers_schema?.tts?.map((p) => ({ value: p.id, label: p.name })) || []),
  ];

  const sttOptions: SelectOption[] = [
    ...defaultOptionLabel(configStatus, "stt"),
    ...(configStatus.providers_schema?.stt?.map((p) => ({ value: p.id, label: p.name })) || []),
  ];

  return (
    <>
      <div className="px-6 py-4 flex items-center justify-between hover:bg-white/5 transition-colors border-b border-white/5 relative z-30">
        <div>
          <h3 className="font-feature-title text-body-md text-primary mb-1">Chat Model Provider</h3>
          <p className="font-label-sm text-label-sm text-on-surface-muted">For interaction model</p>
        </div>
        <CustomSelect
          value={settings.router_model === "default" ? "default" : configStatus.active_config?.llm || "groq"}
          onChange={(val) => {
            if (val === "default") {
              onSettingChange("router_model", "default");
              return;
            }
            let defaultModel = "groq/qwen/qwen3-32b";
            if (val === "openai") defaultModel = "openai/gpt-4o";
            else if (val === "anthropic") defaultModel = "anthropic/claude-3-5-sonnet-20240620";
            onSettingChange("router_model", defaultModel);
          }}
          options={llmOptions}
        />
      </div>

      <div className="px-6 py-4 flex items-center justify-between hover:bg-white/5 transition-colors border-b border-white/5 relative z-20">
        <div>
          <h3 className="font-feature-title text-body-md text-primary mb-1">TTS Provider</h3>
          <p className="font-label-sm text-label-sm text-on-surface-muted">Text-to-Speech engine</p>
        </div>
        <CustomSelect
          value={settings.tts_provider || "browser_dev"}
          onChange={(val) => onSettingChange("tts_provider", val)}
          options={ttsOptions}
        />
      </div>

      <div className="px-6 py-4 flex items-center justify-between hover:bg-white/5 transition-colors rounded-b-xl relative z-10">
        <div>
          <h3 className="font-feature-title text-body-md text-primary mb-1">STT Provider</h3>
          <p className="font-label-sm text-label-sm text-on-surface-muted">Speech-to-Text engine</p>
        </div>
        <CustomSelect
          value={settings.stt_provider || "fake"}
          onChange={(val) => onSettingChange("stt_provider", val)}
          options={sttOptions}
        />
      </div>
    </>
  );
}
