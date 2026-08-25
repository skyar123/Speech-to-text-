"""Pluggable TTS backends.

An engine takes text and returns audio bytes in its own native container,
plus (optionally) timing marks. Everything downstream — the server, the
audiobook pipeline, the subtitle writer — talks only to this interface, so
adding Piper or a paid API later means adding one file here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

DEFAULT_ENGINE = "edge"


@dataclass
class Boundary:
    """A timing mark emitted by the engine, in milliseconds from the start."""

    text: str
    start_ms: float
    duration_ms: float
    kind: str = "word"  # "word" | "sentence"

    @property
    def end_ms(self) -> float:
        return self.start_ms + self.duration_ms

    def shifted(self, offset_ms: float) -> Boundary:
        return Boundary(self.text, self.start_ms + offset_ms, self.duration_ms, self.kind)


@dataclass
class SynthResult:
    """Audio plus whatever timing information the engine could provide."""

    audio: bytes
    fmt: str = "mp3"
    sample_rate: int = 24000
    boundaries: list[Boundary] = field(default_factory=list)


@dataclass
class VoiceInfo:
    name: str
    locale: str = ""
    gender: str = ""
    description: str = ""


class Engine(ABC):
    """Interface every backend implements."""

    #: Short identifier used by ``--engine`` and ``TTSKIT_ENGINE``.
    name: str = "base"
    #: Container the engine natively produces: ``mp3`` or ``wav``.
    native_format: str = "mp3"
    #: Whether the engine can report word/sentence timings.
    supports_boundaries: bool = False
    #: Whether the engine needs network access.
    online: bool = True

    @abstractmethod
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
        """Render *text* completely and return the result."""

    async def stream(
        self,
        text: str,
        *,
        voice: str,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
    ) -> AsyncIterator[bytes]:
        """Yield audio bytes as they arrive.

        The default implementation renders fully and yields once; engines that
        can do better (Edge) override it.
        """
        result = await self.synthesize(text, voice=voice, rate=rate, volume=volume, pitch=pitch)
        yield result.audio

    @abstractmethod
    async def list_voices(self) -> list[VoiceInfo]:
        """Return the voices this engine can use."""


def get_engine(name: str | None = None, **kwargs) -> Engine:
    """Instantiate an engine by name (``edge`` or ``kokoro``)."""
    key = (name or DEFAULT_ENGINE).strip().lower()
    if key in {"edge", "edge-tts", "edgetts"}:
        from .edge import EdgeEngine

        return EdgeEngine(**kwargs)
    if key in {"kokoro", "kokoro-onnx", "offline"}:
        from .kokoro import KokoroEngine

        return KokoroEngine(**kwargs)
    raise ValueError(f"unknown engine {name!r}; available: edge, kokoro")


def available_engines() -> list[str]:
    return ["edge", "kokoro"]


__all__ = [
    "Boundary",
    "Engine",
    "SynthResult",
    "VoiceInfo",
    "available_engines",
    "get_engine",
]
