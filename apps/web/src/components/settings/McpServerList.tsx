import React from "react";
import { getApiBase } from "../../api/client";
import type { McpServerEntry } from "../../api/types";

type McpServerListProps = {
  mcpTools: McpServerEntry[];
  loading: boolean;
  onRefresh: () => void;
};

export function McpServerList({ mcpTools, loading, onRefresh }: McpServerListProps) {
  return (
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
          <div className="text-on-surface-muted col-span-2 text-center py-12">
            No active MCP tools found. Click &quot;Add New Server&quot;.
          </div>
        )}
        {loading && (
          <div className="text-on-surface-muted col-span-2 text-center py-12">Loading...</div>
        )}
        {mcpTools.map((tool) => (
          <div
            key={tool.name}
            className="glass-panel px-6 py-5 rounded-xl flex flex-col gap-4 group hover:bg-white/[0.05] transition-all duration-300 relative overflow-hidden"
          >
            <div className="flex justify-between items-start z-10">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg flex items-center justify-center border border-white/20 text-primary bg-white/10">
                  <span className="material-symbols-outlined">integration_instructions</span>
                </div>
                <div>
                  <h3 className="font-feature-title text-feature-title text-primary text-lg">{tool.name}</h3>
                  <span className="inline-block px-2 py-0.5 mt-1 rounded font-label-mono text-[10px] uppercase bg-white/10 text-primary border border-white/20">
                    Active
                  </span>
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
                  await fetch(`${getApiBase()}/api/codex/mcp-tools/${tool.name}`, { method: "DELETE" });
                  onRefresh();
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
  );
}
