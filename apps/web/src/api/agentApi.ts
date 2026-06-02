import {
  formatApiError,
  getApiBase,
  requestJson,
  setApiBase,
} from "./client";

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

export type OpenConfigFileResult = {
  opened: boolean;
  path: string;
  method?: string;
  error?: string;
};

export let API_BASE = getApiBase();
export let WS_BASE = API_BASE.replace(/^http/, "ws");

export async function initApiBase() {
  const electronAPI = (window as Window & { electronAPI?: { getBackendPort: () => Promise<number> } }).electronAPI;
  if (electronAPI) {
    try {
      const port = await electronAPI.getBackendPort();
      if (port) {
        API_BASE = `http://127.0.0.1:${port}`;
        WS_BASE = API_BASE.replace(/^http/, "ws");
        setApiBase(API_BASE);
        console.log(`Electron sidecar detected. API Base set to: ${API_BASE}`);
        return;
      }
    } catch (e) {
      console.error("Failed to retrieve port from Electron", e);
    }
  }

  if (import.meta.env.VITE_AGENT_API_BASE) {
    return;
  }

  const { protocol, port } = window.location;
  const isViteDevServer = import.meta.env.DEV && port === "5173";
  if (!isViteDevServer && (protocol === "http:" || protocol === "https:")) {
    API_BASE = window.location.origin;
    WS_BASE = API_BASE.replace(/^http/, "ws");
    setApiBase(API_BASE);
    console.log(`Using page origin as API base: ${API_BASE}`);
  }
}

export async function openConfigFile(): Promise<OpenConfigFileResult> {
  return requestJson<OpenConfigFileResult>("/api/config/open", { method: "POST" });
}

export async function startTask(task: string): Promise<StartTaskResponse> {
  return requestJson<StartTaskResponse>("/tasks/start", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ task }),
  });
}

export async function cancelTask(taskId: string): Promise<void> {
  await requestJson(`/tasks/${taskId}/cancel`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ reason: "Cancelled from browser client." }),
  });
}

export function connectEventStream(onEvent: (event: AgentEvent) => void): WebSocket {
  const socket = new WebSocket(`${WS_BASE}/events`);
  socket.addEventListener("message", (message) => {
    onEvent(JSON.parse(message.data) as AgentEvent);
  });
  return socket;
}

export async function getRealtimeSessionConfig(): Promise<RealtimeSessionConfig> {
  return requestJson<RealtimeSessionConfig>("/realtime/session-config");
}

export async function createRealtimeClientSecret(): Promise<unknown> {
  return requestJson("/realtime/client-secret", { method: "POST" });
}

export async function getHealthStatus(): Promise<HealthStatus> {
  return requestJson<HealthStatus>("/health");
}

export type ActiveTool = {
  id: string;
  type: string;
  title: string;
  started_at: string;
};

export async function getActiveTools(): Promise<{ tools: ActiveTool[] }> {
  return requestJson<{ tools: ActiveTool[] }>("/realtime/active-tools");
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
