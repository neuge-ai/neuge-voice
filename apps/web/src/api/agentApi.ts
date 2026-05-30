export type Task = {
  task_id: string;
  generation: number;
  status: string;
  original_request: string;
  user_visible_status: string;
};

export type StartTaskResponse = {
  task: Task;
  acknowledgement: string;
};

export type AgentEvent = {
  event: string;
  task_id: string;
  generation: number;
  message?: string;
  acknowledgement?: string;
  result?: {
    spoken_answer: string;
    technical_summary: string;
    sources_or_tools_used: string[];
  };
};

export type RealtimeSessionConfig = {
  model: string;
  voice: string;
  modalities: string[];
  tools: Array<{ name: string; description: string }>;
};

export type HealthStatus = {
  status: string;
  runtime: string;
  stt_provider?: string;
  tts_provider?: string;
  requested_asr_mode?: string;
  effective_asr_mode?: string;
};

export type TtsResult = {
  provider: string;
  audio_ref?: string | null;
  audio_mime_type?: string | null;
  text?: string | null;
  metadata?: Record<string, unknown>;
};

export let API_BASE = import.meta.env.VITE_AGENT_API_BASE ?? "http://127.0.0.1:8000";
export let WS_BASE = API_BASE.replace(/^http/, "ws");

export async function initApiBase() {
  const electronAPI = (window as any).electronAPI;
  if (electronAPI) {
    try {
      const port = await electronAPI.getBackendPort();
      if (port) {
        API_BASE = `http://127.0.0.1:${port}`;
        WS_BASE = API_BASE.replace(/^http/, "ws");
        console.log(`Electron sidecar detected. API Base set to: ${API_BASE}`);
      }
    } catch (e) {
      console.error("Failed to retrieve port from Electron", e);
    }
  }
}

export async function startTask(task: string): Promise<StartTaskResponse> {
  const response = await fetch(`${API_BASE}/tasks/start`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ task }),
  });

  if (!response.ok) {
    throw new Error(await response.text());
  }

  return response.json();
}

export async function cancelTask(taskId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/cancel`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ reason: "Cancelled from browser client." }),
  });

  if (!response.ok) {
    throw new Error(await response.text());
  }
}

export function connectEventStream(onEvent: (event: AgentEvent) => void): WebSocket {
  const socket = new WebSocket(`${WS_BASE}/events`);
  socket.addEventListener("message", (message) => {
    onEvent(JSON.parse(message.data) as AgentEvent);
  });
  return socket;
}

export async function getRealtimeSessionConfig(): Promise<RealtimeSessionConfig> {
  const response = await fetch(`${API_BASE}/realtime/session-config`);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function createRealtimeClientSecret(): Promise<unknown> {
  const response = await fetch(`${API_BASE}/realtime/client-secret`, { method: "POST" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function getHealthStatus(): Promise<HealthStatus> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export type ActiveTool = {
  id: string;
  type: string;
  title: string;
  started_at: string;
};

export async function getActiveTools(): Promise<{ tools: ActiveTool[] }> {
  const response = await fetch(`${API_BASE}/realtime/active-tools`);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

export async function synthesizeSpeech(text: string, signal?: AbortSignal): Promise<TtsResult> {
  const response = await fetch(`${API_BASE}/tts/synthesize`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
    signal,
  });
  if (!response.ok) {
    throw new Error(await formatApiError(response, "Backend TTS synthesis failed"));
  }
  return response.json();
}

async function formatApiError(response: Response, fallback: string): Promise<string> {
  const raw = await response.text();
  if (!raw) {
    return `${fallback}: HTTP ${response.status}`;
  }
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (parsed.detail && typeof parsed.detail === "object") {
      const detail = parsed.detail as { provider?: string; stage?: string; message?: string };
      const source = [detail.provider, detail.stage].filter(Boolean).join("/");
      return `${fallback}${source ? ` (${source})` : ""}: ${detail.message ?? raw}`;
    }
    if (typeof parsed.detail === "string") {
      return `${fallback}: ${parsed.detail}`;
    }
  } catch {
    // Fall through to the raw server response.
  }
  return `${fallback}: ${raw}`;
}
