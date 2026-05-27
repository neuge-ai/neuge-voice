import { useState, useEffect } from "react";
import { getActiveTools, ActiveTool } from "../api/agentApi";

export type RunningTool = ActiveTool & { elapsed_ms: number };

export function useActiveTools(pollingIntervalMs = 200) {
  const [activeTools, setActiveTools] = useState<RunningTool[]>([]);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function poll() {
      if (!isMounted) return;
      try {
        const data = await getActiveTools();
        if (!isMounted) return;

        const now = Date.now();
        const toolsWithElapsed = data.tools.map((tool) => {
          const startedAtTime = new Date(tool.started_at).getTime();
          const elapsed = Math.max(0, now - startedAtTime);
          return { ...tool, elapsed_ms: elapsed };
        });

        setActiveTools(toolsWithElapsed);
        setError(null);
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err : new Error(String(err)));
        }
      }
    }

    // Initial fetch immediately
    void poll();
    
    // Set up polling interval
    const intervalId = setInterval(poll, pollingIntervalMs);
    
    return () => {
      isMounted = false;
      clearInterval(intervalId);
    };
  }, [pollingIntervalMs]);

  return { activeTools, error };
}
