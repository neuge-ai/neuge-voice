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
  type TtsResult,
} from "./api/agentApi";
import { EventLog } from "./components/EventLog";
import { BrowserVoiceTransport, type BrowserVoiceEvent, type VoiceEventInput } from "./realtime/browserVoiceTransport";
import { useMicrophone } from "./realtime/useMicrophone";

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

  const stopCurrentSpeech = useCallback(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    if (audioObjectUrlRef.current) {
      URL.revokeObjectURL(audioObjectUrlRef.current);
      audioObjectUrlRef.current = null;
    }
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    utteranceRef.current = null;
    setAssistantSpeaking(false);
  }, []);

  const speakText = useCallback(
    async (text: string) => {
      let result: TtsResult;
      try {
        result = await synthesizeSpeech(text);
      } catch (exc) {
        setAssistantSpeaking(false);
        setError(exc instanceof Error ? exc.message : "Backend TTS synthesis failed.");
        return;
      }

      if (result.provider === "browser_dev") {
        if (!result.text) {
          setAssistantSpeaking(false);
          setError("TTS provider browser_dev returned no text for browser speech synthesis.");
          return;
        }
        playBrowserSpeech(result.text, transport, setAssistantSpeaking, setError, utteranceRef, stopCurrentSpeech);
        return;
      }

      if (!result.audio_ref) {
        setAssistantSpeaking(false);
        setError(`TTS provider ${result.provider} returned no audio; browser speech synthesis is only allowed for browser_dev.`);
        return;
      }

      let audioUrl: string | null = null;
      try {
        stopCurrentSpeech();
        audioUrl = await createPlayableAudioUrl(result.audio_ref);
        audioObjectUrlRef.current = audioUrl;
        const audio = new Audio(audioUrl);
        audioRef.current = audio;
        audio.onplay = () => {
          setError(null);
          setAssistantSpeaking(true);
          void transport.send({ event: "assistant_speech_started", metadata: { source: result.provider } });
        };
        audio.onended = () => {
          cleanupAudioPlayback(audioUrl, audioRef, audioObjectUrlRef, setAssistantSpeaking);
          void transport.send({ event: "assistant_speech_ended", metadata: { source: result.provider } });
        };
        audio.onerror = () => {
          cleanupAudioPlayback(audioUrl, audioRef, audioObjectUrlRef, setAssistantSpeaking);
          setError(`Browser TTS playback failed for ${result.provider}: ${describeMediaError(audio.error)}`);
        };
        await audio.play();
      } catch (exc) {
        cleanupAudioPlayback(audioUrl, audioRef, audioObjectUrlRef, setAssistantSpeaking);
        setError(`Browser TTS playback failed for ${result.provider}: ${describePlaybackException(exc)}`);
      }
    },
    [stopCurrentSpeech, transport],
  );

  const handleOutboundVoiceEvents = useCallback((outbound: BrowserVoiceEvent[]) => {
    if (!outbound.length) {
      return;
    }
    setVoiceEvents((current) => [...outbound, ...current].slice(0, 20));
    for (const event of outbound) {
      if (event.event === "stop_assistant_audio") {
        stopCurrentSpeech();
      }
      if (event.event === "transcript_partial") {
        setPartialTranscript(event.text ?? null);
      }
      if (event.event === "transcript_final") {
        setFinalTranscript(event.text ?? null);
        setPartialTranscript(null);
      }
      if (event.event === "delivery_ready" || event.event === "assistant_response" || event.event === "task_status") {
        if (event.text) {
          void speakText(event.text);
        }
      }
    }
  }, [speakText, stopCurrentSpeech]);

  const sendVoiceEvent = useCallback(
    async (event: VoiceEventInput) => {
      try {
        const outbound = await transport.send(event);
        handleOutboundVoiceEvents(outbound);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : "Could not send voice event.");
      }
    },
    [handleOutboundVoiceEvents, transport],
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
    await mic.start();
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

  function handleTestSpeech() {
    void speakText("Speech output is working.");
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
