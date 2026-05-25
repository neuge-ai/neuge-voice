export type BrowserVoiceEventType =
  | "session_started"
  | "session_stopped"
  | "speech_started"
  | "speech_ended"
  | "user_turn"
  | "user_turn_audio"
  | "interruption"
  | "assistant_response"
  | "assistant_speech_started"
  | "assistant_speech_ended"
  | "stop_assistant_audio"
  | "delivery_ready"
  | "task_status"
  | "transcript_partial"
  | "transcript_final"
  | "stt_error"
  | "tts_started"
  | "tts_ended"
  | "idle_state"
  | "client_idle"
  | "error";

export type BrowserVoiceEvent = {
  event: BrowserVoiceEventType;
  transport: "browser";
  session_id: string;
  text?: string;
  audio_ref?: string;
  task_id?: string;
  metadata?: Record<string, unknown>;
};

export type VoiceEventInput = Omit<BrowserVoiceEvent, "transport" | "session_id">;

export class BrowserVoiceTransport {
  private readonly apiBase: string;
  private readonly sessionIdValue: string;

  constructor(apiBase = import.meta.env.VITE_AGENT_API_BASE ?? "http://127.0.0.1:8000") {
    this.apiBase = apiBase;
    this.sessionIdValue = crypto.randomUUID();
  }

  get sessionId(): string {
    return this.sessionIdValue;
  }

  async send(event: VoiceEventInput): Promise<BrowserVoiceEvent[]> {
    const response = await fetch(`${this.apiBase}/voice/events`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(this.withSession(event)),
    });

    if (!response.ok) {
      throw new Error(await formatVoiceApiError(response, event.event));
    }

    const payload = (await response.json()) as { outbound?: BrowserVoiceEvent[] };
    return payload.outbound ?? [];
  }

  async poll(): Promise<BrowserVoiceEvent[]> {
    const response = await fetch(`${this.apiBase}/voice/sessions/${this.sessionIdValue}/events`);
    if (!response.ok) {
      throw new Error(await formatVoiceApiError(response, "poll"));
    }
    return response.json() as Promise<BrowserVoiceEvent[]>;
  }

  private withSession(event: VoiceEventInput): BrowserVoiceEvent {
    return {
      ...event,
      transport: "browser",
      session_id: this.sessionIdValue,
      metadata: event.metadata ?? {},
    };
  }
}

async function formatVoiceApiError(response: Response, eventName: string): Promise<string> {
  const raw = await response.text();
  try {
    const payload = JSON.parse(raw) as { detail?: Array<{ loc?: string[]; msg?: string; input?: string }> | string };
    const firstDetail = Array.isArray(payload.detail) ? payload.detail[0] : undefined;
    if (response.status === 422 && firstDetail?.loc?.includes("event")) {
      return `Backend rejected voice event "${eventName}". Restart the FastAPI backend and make sure this UI is pointed at the updated server.`;
    }
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
  } catch {
    // Fall through to raw response text.
  }
  return raw || `Voice request failed with HTTP ${response.status}.`;
}
