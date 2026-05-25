import pytest

from nextgen_voice_agent.models.voice import VoiceEvent, VoiceEventType, VoiceTransportKind
from nextgen_voice_agent.transports.browser import BrowserVoiceTransport
from nextgen_voice_agent.transports.phone import PhoneVoiceTransport
from nextgen_voice_agent.voice.vad import NoopVadEngine


@pytest.mark.asyncio
async def test_browser_transport_round_trips_voice_event() -> None:
    transport = BrowserVoiceTransport()
    event = VoiceEvent(
        event=VoiceEventType.USER_TURN,
        transport=VoiceTransportKind.BROWSER,
        session_id="browser-session",
        text="Hello",
    )

    await transport.receive(event)
    outbound = transport.events()
    received = await anext(outbound)

    assert received == event


@pytest.mark.asyncio
async def test_phone_transport_scaffold_round_trips_voice_event() -> None:
    transport = PhoneVoiceTransport()
    event = VoiceEvent(
        event=VoiceEventType.INTERRUPTION,
        transport=VoiceTransportKind.PHONE,
        session_id="phone-session",
    )

    await transport.receive(event)
    outbound = transport.events()
    received = await anext(outbound)

    assert received == event


@pytest.mark.asyncio
async def test_phone_transport_has_server_side_vad_boundary() -> None:
    transport = PhoneVoiceTransport()

    assert isinstance(transport.vad_engine, NoopVadEngine)
    decision = await transport.vad_engine.process_frame(b"\x00\x00", sample_rate=8000)
    assert decision.speech_detected is False
