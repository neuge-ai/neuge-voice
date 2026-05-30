import { useState } from "react";
import { useAgentSession } from "./hooks/useAgentSession";
import { EventLog } from "./components/EventLog";
import { startTask, cancelTask } from "./api/agentApi";

export function App() {
  const {
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
    handleStopSpeech,
    handleTestSpeech,
    sendVoiceEvent,
    setEvents,
  } = useAgentSession();

  // Keep demo task local state in App as it's specific to the dev UI
  const [demoTaskText, setDemoTaskText] = useState("Check this week's weather and average noon/evening temperatures.");

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
          <h1>Neuge Voice Agent</h1>
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
            {voiceEvents.slice(0, 20).map((event, index) => (
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
