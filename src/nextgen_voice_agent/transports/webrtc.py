import asyncio
import logging

import av
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack

from nextgen_voice_agent.models.voice import VoiceTransportKind
from nextgen_voice_agent.voice.session_context import VoiceSessionContext
from nextgen_voice_agent.voice.stt import AudioFrame
from nextgen_voice_agent.voice.voice_logging import log_voice_event

logger = logging.getLogger(__name__)

WEBRTC_EVENT_QUEUE_MAX = 256


class WebRTCSessionManager:
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.connections: dict[str, RTCPeerConnection] = {}
        self.session_queues: dict[str, asyncio.Queue] = {}
        self._polling_tasks: dict[str, asyncio.Task] = {}
        self.orchestrator.event_listeners.add(self.on_orchestrator_event)

    async def shutdown(self) -> None:
        self.orchestrator.event_listeners.discard(self.on_orchestrator_event)
        session_ids = list(self.connections)
        for session_id in session_ids:
            await self._cleanup_session(session_id, reason="app_shutdown")
        for session_id, task in list(self._polling_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._polling_tasks.pop(session_id, None)
        self.session_queues.clear()

    def on_orchestrator_event(self, session_id: str, event):
        queue = self.session_queues.get(session_id)
        if queue is None:
            return
        if queue.qsize() >= WEBRTC_EVENT_QUEUE_MAX:
            try:
                queue.get_nowait()
                logger.warning(
                    "WebRTC event queue full; dropped oldest event",
                    extra={"session_id": session_id},
                )
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(event)

    async def create_connection(self, session_id: str, offer_sdp: str, offer_type: str) -> RTCSessionDescription:
        await self.orchestrator.ensure_session(
            VoiceSessionContext(session_id=session_id, transport=VoiceTransportKind.BROWSER)
        )
        pc = RTCPeerConnection()
        self.connections[session_id] = pc
        self.session_queues[session_id] = asyncio.Queue(maxsize=WEBRTC_EVENT_QUEUE_MAX)

        @pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                asyncio.create_task(self._consume_audio_track(session_id, track))

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            if pc.connectionState in ["failed", "closed"]:
                await self._cleanup_session(session_id, reason=pc.connectionState)

        offer = RTCSessionDescription(sdp=offer_sdp, type=offer_type)
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        self._polling_tasks[session_id] = asyncio.create_task(self._poll_outbound_events(session_id))
        log_voice_event(
            "webrtc_connection_opened",
            session_id=session_id,
            transport=VoiceTransportKind.BROWSER.value,
        )
        return pc.localDescription

    async def _cleanup_session(self, session_id: str, *, reason: str) -> None:
        log_voice_event(
            "webrtc_connection_closed",
            session_id=session_id,
            transport=VoiceTransportKind.BROWSER.value,
            extra={"reason": reason},
        )
        pc = self.connections.pop(session_id, None)
        if pc is not None:
            await pc.close()
        self.session_queues.pop(session_id, None)
        task = self._polling_tasks.pop(session_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _poll_outbound_events(self, session_id: str) -> None:
        queue = self.session_queues.get(session_id)
        if not queue:
            return
        try:
            while True:
                await queue.get()
        except asyncio.CancelledError:
            pass

    async def _consume_audio_track(self, session_id: str, track: MediaStreamTrack) -> None:
        sequence = 0
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        try:
            while True:
                frame = await track.recv()
                resampled_frames = resampler.resample(frame)
                for resampled in resampled_frames:
                    pcm_bytes = resampled.to_ndarray().tobytes()
                    segment_id = self.orchestrator.get_active_speech_segment_id(session_id)
                    audio_frame = AudioFrame(
                        payload=pcm_bytes,
                        sample_rate=16000,
                        channels=1,
                        encoding="pcm_s16le",
                        sequence=sequence,
                        speech_segment_id=segment_id,
                    )
                    await self.orchestrator.ingest_audio_frame(session_id, audio_frame)
                    sequence += 1
        except Exception:
            logger.exception(
                "Error reading WebRTC audio track",
                extra={"session_id": session_id},
            )
