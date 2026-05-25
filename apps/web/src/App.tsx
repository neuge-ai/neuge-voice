import { type Dispatch, type MutableRefObject, type SetStateAction, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  cancelTask,
  connectEventStream,
  getRealtimeSessionConfig,
  getHealthStatus,
  synthesizeSpeech,
  startTask,
  type AgentEvent,
  type HealthStatus,
  type RealtimeSessionConfig,
  type Task,
} from "./api/agentApi";
import { EventLog } from "./components/EventLog";
import { BrowserVoiceTransport, type BrowserVoiceEvent, type VoiceEventInput } from "./realtime/browserVoiceTransport";
import { useMicrophone } from "./realtime/useMicrophone";

// ---------------------------------------------------------------------------
// TTS Playback Queue
// Each queued item is a thunk that returns a Promise which resolves when that
// utterance finishes (or is cancelled).  The queue drains serially: item N+1
// starts only after item N's promise resolves.  Calling flushTtsQueue() cancels
// everything in flight immediately.
// ---------------------------------------------------------------------------
type TtsQueueItem = {
  text: string;
  cancelled: boolean;
  resolveCancel: () => void;
};

class TtsPlaybackQueue {
  private queue: TtsQueueItem[] = [];
  private running = false;

  /** Add a text utterance to the back of the queue and start draining if idle. */
  enqueue(
    text: string,
    play: (item: TtsQueueItem) => Promise<void>,
  ): void {
    const item: TtsQueueItem = { text, cancelled: false, resolveCancel: () => {} };
    this.queue.push(item);
    if (!this.running) {
      void this.drain(play);
    }
  }

  /** Cancel all queued and currently-playing utterances immediately. */
  flush(): void {
    for (const item of this.queue) {
      item.cancelled = true;
      item.resolveCancel();
    }
    this.queue = [];
  }

  private async drain(play: (item: TtsQueueItem) => Promise<void>): Promise<void> {
    this.running = true;
    while (this.queue.length > 0) {
      const item = this.queue.shift()!;
      if (!item.cancelled) {
        await play(item);
      }
    }
    this.running = false;
  }
}

async function createPlayableAudioUrl(audioRef: string): Promise<string> {
  if (!audioRef.startsWith("data:")) {
    return audioRef;
  }
  const response = await fetch(audioRef);
  if (!response.ok) {
    throw new Error(`Could not decode synthesized audio: HTTP ${response.status}`);
  }
  const blob = await response.blob();
  if (!blob.size) {
    throw new Error("Could not decode synthesized audio: empty audio payload.");
  }
  return URL.createObjectURL(blob);
}

function describeMediaError(error: MediaError | null): string {
  if (!error) {
    return "the browser did not provide a media error code.";
  }
  const reasons: Record<number, string> = {
    [MediaError.MEDIA_ERR_ABORTED]: "playback was aborted.",
    [MediaError.MEDIA_ERR_NETWORK]: "the browser could not load the audio.",
    [MediaError.MEDIA_ERR_DECODE]: "the browser could not decode the audio.",
    [MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED]: "the synthesized audio format is not supported by this browser.",
  };
  return reasons[error.code] ?? `media error code ${error.code}.`;
}

function describePlaybackException(exc: unknown): string {
  if (exc instanceof DOMException && exc.name === "NotAllowedError") {
    return "the browser blocked autoplay; click Test speech once after opening the page to unlock audio playback.";
  }
  if (exc instanceof Error) {
    return exc.message;
  }
  return "the browser rejected playback.";
}

function cleanupAudioPlayback(
  audioUrl: string | null,
  audioRef: MutableRefObject<HTMLAudioElement | null>,
  audioObjectUrlRef: MutableRefObject<string | null>,
  setAssistantSpeaking: Dispatch<SetStateAction<boolean>>,
) {
  audioRef.current?.pause();
  audioRef.current = null;
  if (audioUrl && audioObjectUrlRef.current === audioUrl) {
    URL.revokeObjectURL(audioUrl);
    audioObjectUrlRef.current = null;
  }
  setAssistantSpeaking(false);
}

function playBrowserSpeech(
  text: string,
  transport: BrowserVoiceTransport,
  setAssistantSpeaking: Dispatch<SetStateAction<boolean>>,
  setError: Dispatch<SetStateAction<string | null>>,
  utteranceRef: MutableRefObject<SpeechSynthesisUtterance | null>,
  stopCurrentSpeech: () => void,
) {
  if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
    setAssistantSpeaking(false);
    setError("This browser does not support local speech synthesis playback.");
    return;
  }
  stopCurrentSpeech();
  const utterance = new SpeechSynthesisUtterance(text);
  const voices = window.speechSynthesis.getVoices();
  utterance.voice = voices.find((voice) => voice.lang.startsWith("en")) ?? voices[0] ?? null;
  utterance.rate = 1;
  utterance.pitch = 1;
  utterance.volume = 1;
  utteranceRef.current = utterance;
  utterance.onstart = () => {
    setError(null);
    setAssistantSpeaking(true);
    void transport.send({ event: "assistant_speech_started", metadata: { source: "speech_synthesis" } });
  };
  utterance.onend = () => {
    setAssistantSpeaking(false);
    utteranceRef.current = null;
    void transport.send({ event: "assistant_speech_ended", metadata: { source: "speech_synthesis" } });
  };
  utterance.onerror = (speechError) => {
    setAssistantSpeaking(false);
    utteranceRef.current = null;
    setError(`Browser speech synthesis failed: ${speechError.error || "unknown speech synthesis error"}.`);
  };
  window.speechSynthesis.speak(utterance);
  window.setTimeout(() => {
    if (utteranceRef.current === utterance && !window.speechSynthesis.speaking) {
      window.speechSynthesis.pause();
      window.speechSynthesis.resume();
    }
  }, 250);
}

export function App() {
  const transport = useMemo(() => new BrowserVoiceTransport(), []);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioObjectUrlRef = useRef<string | null>(null);
  // Single serial TTS queue — survives re-renders because it lives in a ref.
  const ttsQueue = useRef<TtsPlaybackQueue>(new TtsPlaybackQueue());
  const [assistantSpeaking, setAssistantSpeaking] = useState(false);
  const [backendConnected, setBackendConnected] = useState(false);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [voiceEvents, setVoiceEvents] = useState<BrowserVoiceEvent[]>([]);
  const [realtimeConfig, setRealtimeConfig] = useState<RealtimeSessionConfig | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [partialTranscript, setPartialTranscript] = useState<string | null>(null);
  const [finalTranscript, setFinalTranscript] = useState<string | null>(null);
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [demoTaskText, setDemoTaskText] = useState("Check this week's weather and average noon/evening temperatures.");
  const [error, setError] = useState<string | null>(null);

  /** Cancel every queued/in-flight TTS utterance immediately. */
  const flushTtsQueue = useCallback(() => {
    ttsQueue.current.flush();
    // Stop any <audio> element currently playing.
    audioRef.current?.pause();
    audioRef.current = null;
    if (audioObjectUrlRef.current) {
      URL.revokeObjectURL(audioObjectUrlRef.current);
      audioObjectUrlRef.current = null;
    }
    // Stop any browser speech synthesis utterance.
    if (utteranceRef.current) {
      window.speechSynthesis?.cancel();
      utteranceRef.current = null;
    }
    setAssistantSpeaking(false);
  }, []);

  const stopCurrentSpeech = useCallback(() => {
    flushTtsQueue();
  }, [flushTtsQueue]);

  /**
   * Play one TTS item from the queue.  Resolves when the audio finishes
   * OR when the item is cancelled (by flushTtsQueue).
   */
  const playTtsItem = useCallback(
    async (item: TtsQueueItem): Promise<void> => {
      if (item.cancelled) return;

      let result: Awaited<ReturnType<typeof synthesizeSpeech>>;
      try {
        result = await synthesizeSpeech(item.text);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : "TTS failed");
        return;
      }

      if (item.cancelled) return;

      await new Promise<void>((resolve) => {
        // Let the cancel mechanism resolve this promise early.
        item.resolveCancel = resolve;

        if (item.cancelled) {
          resolve();
          return;
        }

        if (result.audio_ref) {
          void (async () => {
            try {
              const url = await createPlayableAudioUrl(result.audio_ref!);
              if (item.cancelled) { URL.revokeObjectURL(url); resolve(); return; }
              audioObjectUrlRef.current = url;
              const a = new Audio(url);
              audioRef.current = a;
              a.onplay = () => {
                setAssistantSpeaking(true);
                void transport.send({ event: "assistant_speech_started", metadata: { source: "tts_queue" } });
              };
              const finish = () => {
                setAssistantSpeaking(false);
                audioRef.current = null;
                if (audioObjectUrlRef.current === url) {
                  URL.revokeObjectURL(url);
                  audioObjectUrlRef.current = null;
                }
                void transport.send({ event: "assistant_speech_ended", metadata: { source: "tts_queue" } });
                resolve();
              };
              a.onended = finish;
              a.onerror = finish;
              a.play().catch((exc) => {
                setError(describePlaybackException(exc));
                finish();
              });
            } catch (exc) {
              setError(exc instanceof Error ? exc.message : "Audio decode failed");
              resolve();
            }
          })();
        } else if (result.text) {
          // Browser speech synthesis fallback
          if (!("speechSynthesis" in window)) {
            setError("Browser speech synthesis not supported.");
            resolve();
            return;
          }
          const utterance = new SpeechSynthesisUtterance(result.text);
          const voices = window.speechSynthesis.getVoices();
          utterance.voice = voices.find((v) => v.lang.startsWith("en")) ?? voices[0] ?? null;
          utterance.rate = 1;
          utterance.pitch = 1;
          utterance.volume = 1;
          utteranceRef.current = utterance;
          utterance.onstart = () => {
            setAssistantSpeaking(true);
            void transport.send({ event: "assistant_speech_started", metadata: { source: "speech_synthesis" } });
          };
          const finish = () => {
            setAssistantSpeaking(false);
            utteranceRef.current = null;
            void transport.send({ event: "assistant_speech_ended", metadata: { source: "speech_synthesis" } });
            resolve();
          };
          utterance.onend = finish;
          utterance.onerror = (e) => {
            setError(`Browser speech synthesis failed: ${e.error || "unknown"}`);
            finish();
          };
          window.speechSynthesis.speak(utterance);
          // Chromium quirk: kick synthesis if it stalls.
          window.setTimeout(() => {
            if (utteranceRef.current === utterance && !window.speechSynthesis.speaking) {
              window.speechSynthesis.pause();
              window.speechSynthesis.resume();
            }
          }, 250);
        } else {
          resolve();
        }
      });
    },
    [transport],
  );

  const handleOutboundVoiceEvents = useCallback(
    (outbound: BrowserVoiceEvent[]) => {
      if (!outbound.length) return;
      setVoiceEvents((current) => [...outbound, ...current].slice(0, 20));

      for (const event of outbound) {
        // ── Interrupt: flush the entire TTS queue immediately ──────────────────
        if (
          event.event === "stop_assistant_audio" ||
          event.event === "speech_started"
        ) {
          flushTtsQueue();
        }

        if (event.event === "transcript_partial") {
          setPartialTranscript(event.text ?? null);
        }
        if (event.event === "transcript_final") {
          setFinalTranscript(event.text ?? null);
          setPartialTranscript(null);
        }
        if (event.event === "assistant_speech_started") {
          setAssistantSpeaking(true);
        }
        if (event.event === "assistant_speech_ended") {
          setAssistantSpeaking(false);
        }

        // ── Enqueue TTS utterances ─────────────────────────────────────────────
        // We ALWAYS use the local HTTP TTS path (playTtsItem → /tts/synthesize →
        // <audio> element), even when WebRTC is active for mic input.
        // Reason: WebRTC mic input gives us native AEC on the INPUT side.
        // For OUTPUT, the reliable, already-working HTTP+audio path is used.
        // The backend's WebRTC TTS output path (_poll_outbound_events) is too
        // fragile — any ElevenLabs error silently kills the loop with no recovery.
        if (
          (event.event === "assistant_response" ||
            event.event === "delivery_ready" ||
            event.event === "task_status") &&
          event.text
        ) {
          ttsQueue.current.enqueue(event.text, playTtsItem);
        }
      }
    },
    [flushTtsQueue, playTtsItem],
  );

  const sendVoiceEvent = useCallback(
    async (event: VoiceEventInput) => {
      // Flush the TTS queue the moment the user starts speaking — don't wait
      // for the server round-trip.  This gives the earliest possible cancellation.
      if (event.event === "speech_started" || event.event === "interruption") {
        flushTtsQueue();
      }
      try {
        const outbound = await transport.send(event);
        handleOutboundVoiceEvents(outbound);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : "Could not send voice event.");
      }
    },
    [flushTtsQueue, handleOutboundVoiceEvents, transport],
  );

  const mic = useMicrophone({ onVoiceEvent: sendVoiceEvent });

  useEffect(() => {
    const socket = connectEventStream((event) => {
      setEvents((current) => [event, ...current].slice(0, 30));
      if (event.event === "task_completed" || event.event === "task_cancelled") {
        setActiveTask(null);
      }
    });

    socket.addEventListener("open", () => setBackendConnected(true));
    socket.addEventListener("close", () => setBackendConnected(false));
    socket.addEventListener("error", () => setBackendConnected(false));
    return () => socket.close();
  }, []);

  useEffect(() => {
    void getRealtimeSessionConfig()
      .then(setRealtimeConfig)
      .catch(() => setRealtimeConfig(null));
    void getHealthStatus()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    const interval = window.setInterval(async () => {
      try {
        handleOutboundVoiceEvents(await transport.poll());
      } catch {
        // The backend event socket is the primary liveness indicator.
      }
    }, 500);
    return () => window.clearInterval(interval);
  }, [handleOutboundVoiceEvents, transport]);

  async function handleStartMic() {
    const stream = await mic.start();
    if (stream) {
      try {
        await transport.connectWebRTC(stream);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to connect WebRTC");
      }
    }
  }

  async function handleDemoTask() {
    setError(null);
    try {
      const response = await startTask(demoTaskText);
      setActiveTask(response.task);
      setEvents((current) => [
        {
          event: "local_acknowledgement",
          task_id: response.task.task_id,
          generation: response.task.generation,
          acknowledgement: response.acknowledgement,
        },
        ...current,
      ]);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not start task.");
    }
  }

  async function handleVoiceTextTurn() {
    setError(null);
    await sendVoiceEvent({ event: "user_turn", text: demoTaskText, metadata: { source: "typed_voice_debug" } });
  }

  async function handleCancel() {
    if (!activeTask) {
      return;
    }
    setError(null);
    try {
      await cancelTask(activeTask.task_id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not cancel task.");
    }
  }

  async function handleTestSpeech() {
    setError(null);
    try {
      await sendVoiceEvent({ event: "user_turn", text: "Please say 'This is a test of the text to speech system.'", metadata: { source: "test_speech_button" } });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not send test speech event.");
    }
  }

  function handleStopSpeech() {
    stopCurrentSpeech();
  }

  const captureSettingsText = mic.captureSettings
    ? [
        `AEC ${formatCaptureSetting(mic.captureSettings.echoCancellation)}`,
        `NS ${formatCaptureSetting(mic.captureSettings.noiseSuppression)}`,
        `AGC ${formatCaptureSetting(mic.captureSettings.autoGainControl)}`,
        `ch ${mic.captureSettings.channelCount ?? "?"}`,
        mic.captureSettings.sampleRate ? `${mic.captureSettings.sampleRate} Hz` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>NextGen Voice Agent</h1>
          <p>Basic browser voice surface for the Python agent backend.</p>
        </div>
        <div className="status-strip">
          <span data-state={backendConnected ? "ok" : "bad"}>{backendConnected ? "Backend online" : "Backend offline"}</span>
          <span data-state={mic.state === "listening" ? "ok" : "idle"}>Mic {mic.state}</span>
          <span data-state={mic.vadState === "speaking" ? "ok" : "idle"}>VAD {mic.vadState}</span>
          <span data-state={assistantSpeaking ? "ok" : "idle"}>Assistant {assistantSpeaking ? "speaking" : "idle"}</span>
          <span data-state={realtimeConfig ? "ok" : "idle"}>
            Realtime config {realtimeConfig?.model ?? "unknown"}
          </span>
          <span data-state={health?.stt_provider ? "ok" : "idle"}>STT {health?.stt_provider ?? "unknown"}</span>
          <span data-state={health?.effective_asr_mode ? "ok" : "idle"}>ASR {health?.effective_asr_mode ?? "unknown"}</span>
          <span data-state={health?.tts_provider ? "ok" : "idle"}>TTS {health?.tts_provider ?? "unknown"}</span>
        </div>
      </header>

      <section className="workspace">
        <div className="panel controls">
          <h2>Voice</h2>
          <div className="button-row">
            <button type="button" onClick={handleStartMic} disabled={mic.state === "listening"}>
              Start mic
            </button>
            <button type="button" onClick={mic.stop} disabled={mic.state !== "listening"}>
              Stop mic
            </button>
            <button type="button" onClick={handleTestSpeech}>
              Test speech
            </button>
            <button type="button" onClick={handleStopSpeech} disabled={!assistantSpeaking}>
              Stop speech
            </button>
          </div>
          {mic.error && <p className="error">{mic.error}</p>}
          <div className="meter" aria-label="Microphone level">
            <span style={{ width: `${Math.min(100, Math.round(mic.level * 500))}%` }} />
          </div>
          <p className="note">Session {transport.sessionId}</p>
          {captureSettingsText && <p className="note">Capture: {captureSettingsText}</p>}
          {partialTranscript && <p className="note">Partial: {partialTranscript}</p>}
          {finalTranscript && <p className="note">Final: {finalTranscript}</p>}
          <ul className="voice-events">
            {voiceEvents.slice(0, 5).map((event, index) => (
              <li key={`${event.event}-${index}`}>
                <span>{event.event}</span>
                {event.text && <p>{event.text}</p>}
              </li>
            ))}
          </ul>
        </div>

        <div className="panel controls">
          <h2>Demo Task</h2>
          <textarea value={demoTaskText} onChange={(event) => setDemoTaskText(event.target.value)} />
          <div className="button-row">
            <button type="button" onClick={handleDemoTask} disabled={!backendConnected || Boolean(activeTask)}>
              Start task
            </button>
            <button type="button" onClick={handleVoiceTextTurn} disabled={!backendConnected}>
              Send voice turn
            </button>
            <button type="button" onClick={handleCancel} disabled={!activeTask}>
              Cancel task
            </button>
          </div>
          {activeTask && (
            <div className="task-card">
              <strong>{activeTask.status}</strong>
              <span>{activeTask.user_visible_status}</span>
              <code>{activeTask.task_id}</code>
            </div>
          )}
          {error && <p className="error">{error}</p>}
        </div>

        <EventLog events={events} />
      </section>
    </main>
  );
}

function formatCaptureSetting(value: boolean | string | undefined): string {
  if (value === undefined) {
    return "?";
  }
  return String(value);
}
