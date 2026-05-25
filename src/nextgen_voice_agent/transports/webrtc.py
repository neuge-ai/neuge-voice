import asyncio
import base64
import io
import logging
import uuid
import av

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack

from nextgen_voice_agent.models.voice import VoiceEvent, VoiceEventType, VoiceTransportKind
from nextgen_voice_agent.server.dependencies import get_tts_provider
from nextgen_voice_agent.voice.tts import TtsRequest

logger = logging.getLogger(__name__)

class AudioOutputTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self._queue = asyncio.Queue()
        self._timestamp = 0

    async def add_wav_bytes(self, wav_bytes: bytes):
        import av
        import io
        container = av.open(io.BytesIO(wav_bytes))
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            # Do not set pts to None here, we will handle it in recv
            await self._queue.put(frame)

    async def recv(self):
        import fractions
        frame = await self._queue.get()
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, frame.sample_rate)
        self._timestamp += frame.samples
        return frame


class WebRTCSessionManager:
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.connections: dict[str, RTCPeerConnection] = {}
        self.output_tracks: dict[str, AudioOutputTrack] = {}
        self.session_queues: dict[str, asyncio.Queue] = {}
        self._polling_tasks: dict[str, asyncio.Task] = {}
        self.orchestrator.event_listeners.add(self.on_orchestrator_event)
        
    def on_orchestrator_event(self, session_id: str, event):
        if session_id in self.session_queues:
            self.session_queues[session_id].put_nowait(event)
        
    async def get_tts_provider_lazy(self):
        from nextgen_voice_agent.config import get_settings
        from nextgen_voice_agent.voice.tts import create_tts_provider
        return create_tts_provider(get_settings())

    async def create_connection(self, session_id: str, offer_sdp: str, offer_type: str) -> RTCSessionDescription:
        pc = RTCPeerConnection()
        self.connections[session_id] = pc
        
        self.session_queues[session_id] = asyncio.Queue()
        output_track = AudioOutputTrack()
        self.output_tracks[session_id] = output_track
        pc.addTrack(output_track)

        @pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                asyncio.create_task(self._consume_audio_track(session_id, track))

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            if pc.connectionState in ["failed", "closed"]:
                if session_id in self.connections:
                    del self.connections[session_id]
                if session_id in self.output_tracks:
                    del self.output_tracks[session_id]
                if session_id in self.session_queues:
                    del self.session_queues[session_id]
                if session_id in self._polling_tasks:
                    self._polling_tasks[session_id].cancel()
                    del self._polling_tasks[session_id]

        offer = RTCSessionDescription(sdp=offer_sdp, type=offer_type)
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        
        # Start a loop to pump outbound events from the orchestrator and synthesize TTS
        self._polling_tasks[session_id] = asyncio.create_task(self._poll_outbound_events(session_id))
        
        return pc.localDescription

    async def _poll_outbound_events(self, session_id: str):
        # The frontend now always handles TTS synthesis via the HTTP /tts/synthesize
        # endpoint.  This loop only needs to keep the session queue drained so it
        # doesn't grow unbounded.  We intentionally skip audio synthesis here to
        # avoid double-synthesizing and wasting ElevenLabs API credits.
        queue = self.session_queues.get(session_id)
        if not queue:
            return
        try:
            while True:
                await queue.get()  # drain – discard; frontend handles TTS
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"WebRTC poll error: {e}")

    async def _consume_audio_track(self, session_id: str, track: MediaStreamTrack):
        sequence = 0
        speech_segment_id = str(uuid.uuid4())
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        try:
            while True:
                frame = await track.recv()
                # Ensure it is s16 mono 16000 for our stt
                resampled_frames = resampler.resample(frame)
                for resampled in resampled_frames:
                    pcm_bytes = resampled.to_ndarray().tobytes()
                    audio_ref = base64.b64encode(pcm_bytes).decode('ascii')
                    
                    active_segment_id = "default"
                    state = self.orchestrator.sessions.get(session_id)
                    if state and state.active_utterance:
                        active_segment_id = state.active_utterance.speech_segment_id
                        
                    event = VoiceEvent(
                        event=VoiceEventType.USER_TURN_AUDIO,
                        transport=VoiceTransportKind.BROWSER,
                        session_id=session_id,
                        audio_ref=audio_ref,
                        metadata={
                            "sample_rate": 16000,
                            "channels": 1,
                            "encoding": "pcm_s16le",
                            "sequence": sequence,
                            "speech_segment_id": active_segment_id
                        }
                    )
                    await self.orchestrator.handle_voice_event(session_id, event)
                    sequence += 1
        except Exception as e:
            logger.error(f"Error reading from track: {e}")
