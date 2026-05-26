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
  public peerConnection: RTCPeerConnection | null = null;
  public audioContext: AudioContext;

  constructor(apiBase = import.meta.env.VITE_AGENT_API_BASE ?? "http://127.0.0.1:8000") {
    this.apiBase = apiBase;
    this.sessionIdValue = crypto.randomUUID();
    this.audioContext = new AudioContext();
  }

  get sessionId(): string {
    return this.sessionIdValue;
  }
  
  async connectWebRTC(stream: MediaStream): Promise<void> {
    this.peerConnection = new RTCPeerConnection();
    
    // Add local mic stream
    for (const track of stream.getTracks()) {
      this.peerConnection.addTrack(track, stream);
    }
    
    // Handle remote tracks (TTS from backend)
    this.peerConnection.ontrack = (event) => {
      const remoteStream = event.streams && event.streams.length > 0 ? event.streams[0] : new MediaStream([event.track]);
      const source = this.audioContext.createMediaStreamSource(remoteStream);
      source.connect(this.audioContext.destination);
    };
    
    const offer = await this.peerConnection.createOffer();
    await this.peerConnection.setLocalDescription(offer);
    
    const response = await fetch(`${this.apiBase}/webrtc/offer`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        sdp: offer.sdp,
        type: offer.type,
        session_id: this.sessionIdValue
      }),
    });
    
    if (!response.ok) {
      throw new Error(`WebRTC offer failed: ${response.status}`);
    }
    
    const answer = await response.json();
    await this.peerConnection.setRemoteDescription(answer);
  }

  async send(event: VoiceEventInput): Promise<BrowserVoiceEvent[]> {
    // The previous send method used fetch
    const response = await fetch(`${this.apiBase}/voice/events`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(this.withSession(event)),
    });

    if (!response.ok) {
      throw new Error(`Send event failed: ${response.status}`);
    }

    const payload = (await response.json()) as { outbound?: BrowserVoiceEvent[] };
    return payload.outbound ?? [];
  }

  async poll(): Promise<BrowserVoiceEvent[]> {
    const response = await fetch(`${this.apiBase}/voice/sessions/${this.sessionIdValue}/events`);
    if (!response.ok) {
      throw new Error(`Poll failed: ${response.status}`);
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
