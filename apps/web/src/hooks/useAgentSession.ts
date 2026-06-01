import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
} from "../api/agentApi";
import { BrowserVoiceTransport, type BrowserVoiceEvent, type VoiceEventInput } from "../realtime/browserVoiceTransport";
import { useMicrophone } from "../realtime/useMicrophone";

type TtsQueueItem = {
  id: string;
  text: string;
  metadata: Record<string, unknown>;
  cancelled: boolean;
  started: boolean;
  cancel: () => void;
};

class TtsPlaybackQueue {
  private queue: TtsQueueItem[] = [];
  private currentItem: TtsQueueItem | null = null;
  private running = false;

  enqueue(text: string, metadata: Record<string, unknown>, play: (item: TtsQueueItem) => Promise<void>): void {
    const item: TtsQueueItem = {
      id: String(metadata.utterance_id ?? crypto.randomUUID()),
      text,
      metadata,
      cancelled: false,
      started: false,
      cancel: () => {},
    };
    this.queue.push(item);
    if (!this.running) {
      void this.drain(play);
    }
  }

  flush(): TtsQueueItem[] {
    const flushed: TtsQueueItem[] = [];
    if (this.currentItem) {
      this.currentItem.cancelled = true;
      flushed.push(this.currentItem);
      this.currentItem.cancel();
    }
    for (const item of this.queue) {
      item.cancelled = true;
      flushed.push(item);
      item.cancel();
    }
    this.queue = [];
    return flushed;
  }

  private async drain(play: (item: TtsQueueItem) => Promise<void>): Promise<void> {
    this.running = true;
    try {
      while (this.queue.length > 0) {
        const item = this.queue.shift()!;
        this.currentItem = item;
        try {
          if (!item.cancelled) {
            await play(item);
          }
        } finally {
          if (this.currentItem === item) {
            this.currentItem = null;
          }
        }
      }
    } finally {
      this.running = false;
    }
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

function describePlaybackException(exc: unknown): string {
  if (exc instanceof DOMException && exc.name === "NotAllowedError") {
    return "the browser blocked autoplay; click Test speech once after opening the page to unlock audio playback.";
  }
  if (exc instanceof Error) {
    return exc.message;
  }
  return "the browser rejected playback.";
}

export function useAgentSession() {
  const transport = useMemo(() => new BrowserVoiceTransport(), []);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioObjectUrlRef = useRef<string | null>(null);
  const ttsQueue = useRef<TtsPlaybackQueue>(new TtsPlaybackQueue());
  const ttsPausedRef = useRef(false);
  
  const [assistantSpeaking, setAssistantSpeaking] = useState(false);
  const [backendConnected, setBackendConnected] = useState(false);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [voiceEvents, setVoiceEvents] = useState<BrowserVoiceEvent[]>([]);
  const [realtimeConfig, setRealtimeConfig] = useState<RealtimeSessionConfig | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [partialTranscript, setPartialTranscript] = useState<string | null>(null);
  const [finalTranscript, setFinalTranscript] = useState<string | null>(null);
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [error, setError] = useState<string | null>(null);

  const flushTtsQueue = useCallback(() => {
    const flushed = ttsQueue.current.flush();
    for (const item of flushed) {
      if (item.metadata.durable === true && !item.started) {
        void transport.send({
          event: "assistant_speech_ended",
          metadata: {
            ...item.metadata,
            source: "flush",
            utterance_id: item.id,
            completed: false,
            reason: "interrupted_before_playback",
          },
        });
      }
    }
    setAssistantSpeaking(false);
    ttsPausedRef.current = false;
  }, [transport]);

  const stopCurrentSpeech = useCallback(() => {
    flushTtsQueue();
  }, [flushTtsQueue]);

  const pauseTts = useCallback(() => {
    ttsPausedRef.current = true;
    if (audioRef.current) {
      audioRef.current.pause();
    }
    if ("speechSynthesis" in window) {
      window.speechSynthesis.pause();
    }
  }, []);

  const resumeTts = useCallback(() => {
    // HTML Audio resume is reliable; speechSynthesis.resume() is best-effort only.
    // After stop_assistant_audio (flush), playback cannot resume — only pause/resume pairs are recoverable.
    ttsPausedRef.current = false;
    if (audioRef.current) {
      if (transport.audioContext.state === 'suspended') {
        transport.audioContext.resume().catch(e => console.warn("Could not resume AudioContext:", e));
      }
      audioRef.current.play().catch(e => setError(describePlaybackException(e)));
    }
    if ("speechSynthesis" in window) {
      window.speechSynthesis.resume();
    }
  }, [transport]);

  const playTtsItem = useCallback(
    async (item: TtsQueueItem): Promise<void> => {
      const speechMetadata = (completed: boolean, reason?: string): Record<string, unknown> => ({
        ...item.metadata,
        source: "tts_queue",
        utterance_id: item.id,
        completed,
        ...(reason ? { reason } : {}),
      });
      const reportInterrupted = (reason: string) => {
        if (item.metadata.durable === true) {
          void transport.send({ event: "assistant_speech_ended", metadata: speechMetadata(false, reason) });
        }
      };

      if (item.cancelled) {
        reportInterrupted("interrupted_before_playback");
        return;
      }

      let result: Awaited<ReturnType<typeof synthesizeSpeech>>;
      const abortController = new AbortController();
      const cancelledBeforePlayback = new Promise<"cancelled">((resolve) => {
        let cancelResolved = false;
        item.cancel = () => {
          if (cancelResolved) return;
          cancelResolved = true;
          item.cancelled = true;
          abortController.abort();
          resolve("cancelled");
        };
      });
      try {
        const synthesis = await Promise.race([
          synthesizeSpeech(item.text, abortController.signal),
          cancelledBeforePlayback,
        ]);
        if (synthesis === "cancelled") {
          reportInterrupted("interrupted_before_playback");
          return;
        }
        result = synthesis;
      } catch (exc) {
        if (item.cancelled || abortController.signal.aborted) return;
        setError(exc instanceof Error ? exc.message : "TTS failed");
        return;
      }

      if (item.cancelled) {
        reportInterrupted("interrupted_before_playback");
        return;
      }

      await new Promise<void>((resolve) => {
        let resolved = false;
        let completedNaturally = false;
        const resolveOnce = () => {
          if (resolved) return;
          resolved = true;
          resolve();
        };

        item.cancel = () => {
          item.cancelled = true;
          reportInterrupted(item.started ? "interrupted" : "interrupted_before_playback");
          resolveOnce();
        };

        if (item.cancelled) {
          reportInterrupted("interrupted_before_playback");
          resolveOnce();
          return;
        }

        if (result.audio_ref) {
          void (async () => {
            try {
              const url = await createPlayableAudioUrl(result.audio_ref!);
              if (item.cancelled) { URL.revokeObjectURL(url); resolveOnce(); return; }
              audioObjectUrlRef.current = url;
              const a = new Audio(url);
              a.crossOrigin = "anonymous";
              audioRef.current = a;
              const source = transport.audioContext.createMediaElementSource(a);
              source.connect(transport.audioContext.destination);
              a.onplay = () => {
                item.started = true;
                setAssistantSpeaking(true);
                void transport.send({ event: "assistant_speech_started", metadata: speechMetadata(false) });
              };
              const finish = () => {
                if (resolved) return;
                setAssistantSpeaking(false);
                audioRef.current = null;
                if (audioObjectUrlRef.current === url) {
                  URL.revokeObjectURL(url);
                  audioObjectUrlRef.current = null;
                }
                void transport.send({ event: "assistant_speech_ended", metadata: speechMetadata(completedNaturally, completedNaturally ? undefined : "interrupted") });
                resolveOnce();
              };
              item.cancel = () => {
                if (resolved) return;
                item.cancelled = true;
                a.pause();
                a.removeAttribute("src");
                a.load();
                finish();
              };
              a.onended = () => {
                completedNaturally = !item.cancelled;
                finish();
              };
              a.onerror = finish;
              const playWhenReady = async () => {
                while (ttsPausedRef.current && !item.cancelled) {
                  await new Promise(r => setTimeout(r, 50));
                }
                if (item.cancelled) return;
                a.play().catch((exc) => {
                  if (exc instanceof DOMException && exc.name === 'AbortError') {
                    console.warn("Audio play() was aborted by pause(). Ignoring.", exc);
                    return;
                  }
                  setError(describePlaybackException(exc));
                  finish();
                });
              };
              void playWhenReady();
            } catch (exc) {
              setError(exc instanceof Error ? exc.message : "Audio decode failed");
              resolveOnce();
            }
          })();
        } else if (result.text) {
          if (!("speechSynthesis" in window)) {
            setError("Browser speech synthesis not supported.");
            resolveOnce();
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
            item.started = true;
            setAssistantSpeaking(true);
            void transport.send({ event: "assistant_speech_started", metadata: { ...speechMetadata(false), source: "speech_synthesis" } });
          };
          const finish = () => {
            if (resolved) return;
            setAssistantSpeaking(false);
            utteranceRef.current = null;
            void transport.send({ event: "assistant_speech_ended", metadata: { ...speechMetadata(completedNaturally, completedNaturally ? undefined : "interrupted"), source: "speech_synthesis" } });
            resolveOnce();
          };
          item.cancel = () => {
            if (resolved) return;
            item.cancelled = true;
            window.speechSynthesis.cancel();
            finish();
          };
          utterance.onend = () => {
            completedNaturally = !item.cancelled;
            finish();
          };
          utterance.onerror = (e) => {
            setError(`Browser speech synthesis failed: ${e.error || "unknown"}`);
            finish();
          };
          const playWhenReady = async () => {
            while (ttsPausedRef.current && !item.cancelled) {
              await new Promise(r => setTimeout(r, 50));
            }
            if (item.cancelled) return;
            window.speechSynthesis.speak(utterance);
          };
          void playWhenReady();
          window.setTimeout(() => {
            if (utteranceRef.current === utterance && !window.speechSynthesis.speaking) {
              window.speechSynthesis.pause();
              window.speechSynthesis.resume();
            }
          }, 250);
        } else {
          resolveOnce();
        }
      });
    },
    [transport],
  );

  const handleOutboundVoiceEvents = useCallback(
    (outbound: BrowserVoiceEvent[]) => {
      if (!outbound.length) return;
      setVoiceEvents((current) => [...[...outbound].reverse(), ...current].slice(0, 20));

      for (const event of outbound) {
        if (event.event === "stop_assistant_audio") {
          flushTtsQueue();
        }
        if (event.event === "pause_assistant_audio") {
          pauseTts();
        }
        if (event.event === "resume_assistant_audio") {
          resumeTts();
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

        if (
          (event.event === "assistant_response" ||
            event.event === "delivery_ready") &&
          event.text
        ) {
          ttsQueue.current.enqueue(
            event.text,
            {
              ...(event.metadata ?? {}),
              event_type: event.event,
              task_id: event.task_id,
              durable: event.metadata?.durable === true || event.event === "delivery_ready",
            },
            playTtsItem,
          );
        }
      }
    },
    [flushTtsQueue, playTtsItem],
  );

  const sendVoiceEvent = useCallback(
    async (event: VoiceEventInput) => {
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
      }
    }, 500);
    return () => window.clearInterval(interval);
  }, [handleOutboundVoiceEvents, transport]);

  async function handleStartMic() {
    const stream = await mic.start();
    if (stream) {
      try {
        await transport.audioContext.resume();
        await transport.connectWebRTC(stream);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to connect WebRTC");
      }
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

  return {
    transport,
    mic,
    assistantSpeaking,
    backendConnected,
    events,
    voiceEvents,
    realtimeConfig,
    health,
    partialTranscript,
    finalTranscript,
    activeTask,
    setActiveTask,
    error,
    setError,
    handleStartMic,
    handleStopSpeech: stopCurrentSpeech,
    handleTestSpeech,
    sendVoiceEvent,
    setEvents,
  };
}
