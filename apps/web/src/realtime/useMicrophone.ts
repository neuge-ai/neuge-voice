import { useCallback, useEffect, useRef, useState } from "react";

import type { VoiceEventInput } from "./browserVoiceTransport";

export type MicState = "idle" | "requesting" | "listening" | "error";
export type VadState = "silent" | "speaking";
export type MicCaptureSettings = Pick<
  MediaTrackSettings,
  "autoGainControl" | "channelCount" | "echoCancellation" | "noiseSuppression" | "sampleRate"
>;

type UseMicrophoneOptions = {
  onVoiceEvent?: (event: VoiceEventInput) => Promise<void> | void;
};

type PcmFrame = {
  sequence: number;
  payload: string;
};

const SAMPLE_RATE = 16000;
const CHANNELS = 1;
const FRAME_MS = 20;
const SPEECH_THRESHOLD = 0.035;
const SPEECH_START_MS = 150;
const SPEECH_END_MS = 700;
const INTERRUPTION_MS = 250;
const PREROLL_MS = 300;
const PREROLL_FRAMES = PREROLL_MS / FRAME_MS;

const WORKLET_SOURCE = `
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.frameSize = 320;
    this.buffer = [];
    this.sequence = 0;
    this.ratio = sampleRate / this.targetSampleRate;
    this.position = 0;
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;

    while (this.position < input.length) {
      const sample = input[Math.floor(this.position)] || 0;
      const clamped = Math.max(-1, Math.min(1, sample));
      this.buffer.push(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff);
      this.position += this.ratio;

      if (this.buffer.length === this.frameSize) {
        const pcm = new Int16Array(this.buffer);
        this.port.postMessage({ sequence: this.sequence++, pcm }, [pcm.buffer]);
        this.buffer = [];
      }
    }

    this.position -= input.length;
    return true;
  }
}

registerProcessor("pcm-capture-processor", PcmCaptureProcessor);
`;

export function useMicrophone(options: UseMicrophoneOptions = {}) {
  const [state, setState] = useState<MicState>("idle");
  const [vadState, setVadState] = useState<VadState>("silent");
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [captureSettings, setCaptureSettings] = useState<MicCaptureSettings | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationRef = useRef<number | null>(null);
  const speakingRef = useRef(false);
  const speechSegmentIdRef = useRef<string | null>(null);
  const prerollRef = useRef<PcmFrame[]>([]);
  const aboveThresholdSinceRef = useRef<number | null>(null);
  const belowThresholdSinceRef = useRef<number | null>(null);
  const interruptionSentRef = useRef(false);
  const onVoiceEventRef = useRef(options.onVoiceEvent);

  useEffect(() => {
    onVoiceEventRef.current = options.onVoiceEvent;
  }, [options.onVoiceEvent]);

  const emit = useCallback((event: VoiceEventInput) => {
    void onVoiceEventRef.current?.(event);
  }, []);

  const emitPcmFrame = useCallback(
    (frame: PcmFrame, speechSegmentId: string) => {
      emit({
        event: "user_turn_audio",
        audio_ref: frame.payload,
        metadata: {
          sample_rate: SAMPLE_RATE,
          channels: CHANNELS,
          encoding: "pcm_s16le",
          chunk_ms: FRAME_MS,
          sequence: frame.sequence,
          speech_segment_id: speechSegmentId,
        },
      });
    },
    [emit],
  );

  const beginSpeech = useCallback(
    (rms: number) => {
      const speechSegmentId = crypto.randomUUID();
      speechSegmentIdRef.current = speechSegmentId;
      speakingRef.current = true;
      interruptionSentRef.current = false;
      setVadState("speaking");
      emit({ event: "speech_started", metadata: { level: rms, speech_segment_id: speechSegmentId } });
      for (const frame of prerollRef.current) {
        emitPcmFrame(frame, speechSegmentId);
      }
      prerollRef.current = [];
    },
    [emit, emitPcmFrame],
  );

  const endSpeech = useCallback(
    (rms: number) => {
      const speechSegmentId = speechSegmentIdRef.current;
      speakingRef.current = false;
      interruptionSentRef.current = false;
      speechSegmentIdRef.current = null;
      setVadState("silent");
      emit({ event: "speech_ended", metadata: { level: rms, speech_segment_id: speechSegmentId } });
    },
    [emit],
  );

  const stopAudioGraph = useCallback(() => {
    if (animationRef.current !== null) {
      cancelAnimationFrame(animationRef.current);
      animationRef.current = null;
    }
    workletRef.current?.disconnect();
    sourceRef.current?.disconnect();
    analyserRef.current?.disconnect();
    workletRef.current = null;
    sourceRef.current = null;
    analyserRef.current = null;
    void audioContextRef.current?.close();
    audioContextRef.current = null;
    speakingRef.current = false;
    speechSegmentIdRef.current = null;
    prerollRef.current = [];
    aboveThresholdSinceRef.current = null;
    belowThresholdSinceRef.current = null;
    interruptionSentRef.current = false;
    setVadState("silent");
    setLevel(0);
  }, []);

  const analyse = useCallback(() => {
    const analyser = analyserRef.current;
    if (!analyser) {
      return;
    }

    const samples = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) {
      const centered = (sample - 128) / 128;
      sum += centered * centered;
    }
    const rms = Math.sqrt(sum / samples.length);
    const now = performance.now();
    setLevel(rms);

    if (rms >= SPEECH_THRESHOLD) {
      belowThresholdSinceRef.current = null;
      aboveThresholdSinceRef.current ??= now;

      if (!speakingRef.current && now - aboveThresholdSinceRef.current >= SPEECH_START_MS) {
        beginSpeech(rms);
      }

      if (speakingRef.current && !interruptionSentRef.current && now - aboveThresholdSinceRef.current >= INTERRUPTION_MS) {
        interruptionSentRef.current = true;
        emit({ event: "interruption", metadata: { level: rms, speech_segment_id: speechSegmentIdRef.current } });
      }
    } else {
      aboveThresholdSinceRef.current = null;
      belowThresholdSinceRef.current ??= now;

      if (speakingRef.current && now - belowThresholdSinceRef.current >= SPEECH_END_MS) {
        endSpeech(rms);
      }
    }

    animationRef.current = requestAnimationFrame(analyse);
  }, [beginSpeech, emit, endSpeech]);

  const startAudioGraph = useCallback(
    async (stream: MediaStream) => {
      const audioContext = new AudioContext();
      const workletUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "text/javascript" }));
      await audioContext.audioWorklet.addModule(workletUrl);
      URL.revokeObjectURL(workletUrl);

      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      const worklet = new AudioWorkletNode(audioContext, "pcm-capture-processor");
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.25;

      source.connect(analyser);
      source.connect(worklet);
      worklet.connect(audioContext.destination);
      worklet.port.onmessage = (message: MessageEvent<{ sequence: number; pcm: Int16Array }>) => {
        const frame = {
          sequence: message.data.sequence,
          payload: int16ToBase64(message.data.pcm),
        };
        if (speakingRef.current && speechSegmentIdRef.current) {
          emitPcmFrame(frame, speechSegmentIdRef.current);
          return;
        }
        prerollRef.current = [...prerollRef.current, frame].slice(-PREROLL_FRAMES);
      };

      audioContextRef.current = audioContext;
      sourceRef.current = source;
      analyserRef.current = analyser;
      workletRef.current = worklet;
      animationRef.current = requestAnimationFrame(analyse);
    },
    [analyse, emitPcmFrame],
  );

  const start = useCallback(async () => {
    setState("requesting");
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: CHANNELS,
        },
      });
      const settings = stream.getAudioTracks()[0]?.getSettings();
      const appliedSettings = settings
        ? {
            autoGainControl: settings.autoGainControl,
            channelCount: settings.channelCount,
            echoCancellation: settings.echoCancellation,
            noiseSuppression: settings.noiseSuppression,
            sampleRate: settings.sampleRate,
          }
        : null;
      setCaptureSettings(appliedSettings);
      streamRef.current = stream;
      await startAudioGraph(stream);
      emit({ event: "idle_state", metadata: { mic: "listening", capture_settings: appliedSettings } });
      setState("listening");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Microphone permission failed.");
      setState("error");
    }
  }, [emit, startAudioGraph]);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setCaptureSettings(null);
    stopAudioGraph();
    emit({ event: "client_idle", metadata: { mic: "stopped" } });
    setState("idle");
  }, [emit, stopAudioGraph]);

  return { state, vadState, level, error, captureSettings, start, stop, stream: streamRef.current };
}

function int16ToBase64(samples: Int16Array): string {
  const bytes = new Uint8Array(samples.buffer);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}
