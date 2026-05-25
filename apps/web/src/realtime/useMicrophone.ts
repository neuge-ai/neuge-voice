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

const CHANNELS = 1;
const SPEECH_THRESHOLD = 0.035;
const SPEECH_START_MS = 150;
const SPEECH_END_MS = 700;
const INTERRUPTION_MS = 250;

export function useMicrophone(options: UseMicrophoneOptions = {}) {
  const [state, setState] = useState<MicState>("idle");
  const [vadState, setVadState] = useState<VadState>("silent");
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [captureSettings, setCaptureSettings] = useState<MicCaptureSettings | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationRef = useRef<number | null>(null);
  const speakingRef = useRef(false);
  const speechSegmentIdRef = useRef<string | null>(null);
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

  const beginSpeech = useCallback(
    (rms: number) => {
      const speechSegmentId = crypto.randomUUID();
      speechSegmentIdRef.current = speechSegmentId;
      speakingRef.current = true;
      interruptionSentRef.current = false;
      setVadState("speaking");
      emit({ event: "speech_started", metadata: { level: rms, speech_segment_id: speechSegmentId } });
    },
    [emit],
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
    sourceRef.current?.disconnect();
    analyserRef.current?.disconnect();
    sourceRef.current = null;
    analyserRef.current = null;
    void audioContextRef.current?.close();
    audioContextRef.current = null;
    speakingRef.current = false;
    speechSegmentIdRef.current = null;
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

      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.25;

      source.connect(analyser);

      audioContextRef.current = audioContext;
      sourceRef.current = source;
      analyserRef.current = analyser;
      animationRef.current = requestAnimationFrame(analyse);
    },
    [analyse],
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
      return stream;
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Microphone permission failed.");
      setState("error");
      return null;
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
