import React from "react";
import type { DynamicSecret } from "../../api/types";

type SecretsPanelProps = {
  secrets: DynamicSecret[];
  secretInputs: Record<string, string>;
  onSecretInputChange: (key: string, value: string) => void;
  onSaveSecret: (keyName: string) => void;
};

export function SecretsPanel({
  secrets,
  secretInputs,
  onSecretInputChange,
  onSaveSecret,
}: SecretsPanelProps) {
  return (
    <>
      <h2 className="font-feature-title text-body-md text-on-surface-muted mb-4 mt-8">API Keys & Secrets</h2>
      <div className="glass-panel rounded-xl flex flex-col relative z-10">
        {secrets.length === 0 && (
          <div className="p-6 text-on-surface-muted text-center font-body-md">
            No API keys required for current configuration.
          </div>
        )}
        {secrets.map((secret, idx) => (
          <div
            key={secret.id}
            className={`px-6 py-4 flex items-center justify-between hover:bg-white/5 transition-colors ${idx !== secrets.length - 1 ? "border-b border-white/5" : ""} ${idx === 0 ? "rounded-t-xl" : ""} ${idx === secrets.length - 1 ? "rounded-b-xl" : ""}`}
          >
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
              <p className="font-label-sm text-label-sm text-on-surface-muted mt-1">
                Required for {secret.categories.join(" + ")}
              </p>
            </div>
            <div className="flex items-center gap-3 w-1/2 justify-end">
              <input
                type="password"
                placeholder="Enter new key..."
                className="flex-1 max-w-[300px] bg-white/5 hover:bg-white/10 border border-white/10 rounded-full py-2.5 px-5 font-body-md text-body-md text-primary focus:outline-none focus:border-cyanCore/50 focus:shadow-[0_0_10px_rgba(4,251,251,0.2)] transition-all placeholder:text-white/30"
                value={secretInputs[secret.id] || ""}
                onChange={(e) => onSecretInputChange(secret.id, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onSaveSecret(secret.id);
                }}
              />
              <button
                onClick={() => onSaveSecret(secret.id)}
                disabled={!secretInputs[secret.id]}
                className={`px-6 py-2.5 rounded-full font-label-sm text-sm font-bold shadow-lg transition-all duration-300 ${!secretInputs[secret.id] ? "bg-white/10 text-white shadow-none cursor-not-allowed" : "bg-white text-black hover:bg-gray-100 hover:scale-105 hover:shadow-[0_4px_14px_rgba(255,255,255,0.4)] active:scale-95"}`}
              >
                Save
              </button>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
