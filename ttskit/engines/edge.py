"""Edge engine — Microsoft Edge's neural voices over their public endpoint.

Free, no API key, ~400 voices in 90+ languages, and the only backend here that
reports word/sentence timings, which is what makes subtitle generation work.
It needs network access; use the Kokoro engine when you need offline.
"""

from __future__ import annotations

import asyncio
import os
import ssl
from collections.abc import AsyncIterator

import edge_tts

from . import Boundary, Engine, SynthResult, VoiceInfo

#: Edge returns 24 kHz mono MP3 at 48 kbps.
SAMPLE_RATE = 24000

_BOUNDARY_MODES = {
    "word": "WordBoundary",
    "sentence": "SentenceBoundary",
    "WordBoundary": "WordBoundary",
    "SentenceBoundary": "SentenceBoundary",
}


def _install_ca_bundle() -> None:
    """Honour ``SSL_CERT_FILE``/``TTSKIT_CA_BUNDLE`` for TLS-inspecting proxies.

    edge-tts pins its SSL context to certifi's bundle, which fails behind a
    corporate MITM proxy even though the connection is legitimate. If the user
    has pointed the standard variables at a trusted bundle, use it.
    """
    bundle = os.environ.get("TTSKIT_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if not bundle or not os.path.exists(bundle):
        return
    try:
        edge_tts.communicate._SSL_CTX = ssl.create_default_context(cafile=bundle)
    except Exception:  # pragma: no cover - defensive; never break synthesis
        pass


_install_ca_bundle()


class EdgeEngine(Engine):
    name = "edge"
    native_format = "mp3"
    supports_boundaries = True
    online = True

    def __init__(self, *, proxy: str | None = None, connect_timeout: int = 15,
                 receive_timeout: int = 60) -> None:
        self.proxy = proxy or os.environ.get("TTSKIT_PROXY") or None
        self.connect_timeout = connect_timeout
        self.receive_timeout = receive_timeout

    def _communicate(
        self, text: str, voice: str, rate: str, volume: str, pitch: str, boundary: str
    ) -> edge_tts.Communicate:
        return edge_tts.Communicate(
            text,
            voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
            boundary=_BOUNDARY_MODES.get(boundary, "SentenceBoundary"),
            proxy=self.proxy,
            connect_timeout=self.connect_timeout,
            receive_timeout=self.receive_timeout,
        )

    async def synthesize(
        self,
        text: str,
        *,
        voice: str,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        boundary: str = "sentence",
    ) -> SynthResult:
        comm = self._communicate(text, voice, rate, volume, pitch, boundary)
        audio = bytearray()
        marks: list[Boundary] = []
        kind = "word" if _BOUNDARY_MODES.get(boundary) == "WordBoundary" else "sentence"

        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                # Edge reports offsets in 100-nanosecond ticks.
                marks.append(
                    Boundary(
                        text=chunk["text"],
                        start_ms=chunk["offset"] / 10_000,
                        duration_ms=chunk["duration"] / 10_000,
                        kind=kind,
                    )
                )

        return SynthResult(
            audio=bytes(audio),
            fmt="mp3",
            sample_rate=SAMPLE_RATE,
            boundaries=marks,
        )

    async def stream(
        self,
        text: str,
        *,
        voice: str,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
    ) -> AsyncIterator[bytes]:
        comm = self._communicate(text, voice, rate, volume, pitch, "sentence")
        async for chunk in comm.stream():
            if chunk["type"] == "audio" and chunk.get("data"):
                yield chunk["data"]

    async def list_voices(self) -> list[VoiceInfo]:
        raw = await edge_tts.list_voices(proxy=self.proxy)
        voices = []
        for item in raw:
            tag = item.get("VoiceTag") or {}
            traits = ", ".join(tag.get("VoicePersonalities", []) or [])
            voices.append(
                VoiceInfo(
                    name=item["ShortName"],
                    locale=item.get("Locale", ""),
                    gender=item.get("Gender", ""),
                    description=traits,
                )
            )
        return sorted(voices, key=lambda v: (v.locale, v.name))


async def _demo() -> None:  # pragma: no cover - manual smoke test
    engine = EdgeEngine()
    result = await engine.synthesize("Edge engine online.", voice="en-US-JennyNeural")
    print(len(result.audio), "bytes")


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
