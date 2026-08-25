"""Kokoro engine — 82M-parameter neural TTS running fully offline on CPU.

No network, no accounts, no telemetry: inference happens in-process through
ONNX Runtime. Weights live in the ttskit cache (``ttskit models download``).
Kokoro produces raw audio rather than a compressed stream, so this engine's
native format is WAV; the pipeline converts when a caller wants MP3.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from ..audio import float_to_wav
from ..models import model_paths, models_present
from . import Engine, SynthResult, VoiceInfo

SAMPLE_RATE = 24000

#: Voices bundled with the Kokoro v1.0 voice pack, grouped by accent/gender.
KNOWN_VOICES: dict[str, str] = {
    "af_heart": "American English, female, warm",
    "af_alloy": "American English, female, neutral",
    "af_aoede": "American English, female, bright",
    "af_bella": "American English, female, expressive",
    "af_jessica": "American English, female, calm",
    "af_kore": "American English, female, clear",
    "af_nicole": "American English, female, soft/ASMR",
    "af_nova": "American English, female, crisp",
    "af_river": "American English, female, mellow",
    "af_sarah": "American English, female, friendly",
    "af_sky": "American English, female, light",
    "am_adam": "American English, male, neutral",
    "am_echo": "American English, male, even",
    "am_eric": "American English, male, firm",
    "am_fenrir": "American English, male, deep",
    "am_liam": "American English, male, youthful",
    "am_michael": "American English, male, narrator",
    "am_onyx": "American English, male, low",
    "am_puck": "American English, male, playful",
    "bf_alice": "British English, female, clear",
    "bf_emma": "British English, female, warm",
    "bf_isabella": "British English, female, formal",
    "bf_lily": "British English, female, gentle",
    "bm_daniel": "British English, male, measured",
    "bm_fable": "British English, male, storyteller",
    "bm_george": "British English, male, mature",
    "bm_lewis": "British English, male, resonant",
}

_lock = threading.Lock()
_model: Any = None


def _load_model() -> Any:
    """Load the ONNX session once per process (it is expensive and thread-safe)."""
    global _model
    with _lock:
        if _model is not None:
            return _model
        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise RuntimeError(
                "The offline engine needs `pip install ttskit[offline]`."
            ) from exc
        model_path, voices_path = model_paths()
        if not models_present():
            raise RuntimeError(
                "Kokoro model files are missing. Run `ttskit models download` "
                f"(expected {model_path} and {voices_path})."
            )
        _model = Kokoro(str(model_path), str(voices_path))
        return _model


def _rate_to_speed(rate: str) -> float:
    """Translate an Edge-style rate string (``+20%``) into a Kokoro speed factor."""
    rate = (rate or "").strip()
    if not rate or rate in {"+0%", "0%", "-0%"}:
        return 1.0
    if rate.endswith("%"):
        try:
            return max(0.5, min(2.0, 1.0 + float(rate[:-1]) / 100.0))
        except ValueError:
            return 1.0
    try:
        return max(0.5, min(2.0, float(rate)))
    except ValueError:
        return 1.0


class KokoroEngine(Engine):
    name = "kokoro"
    native_format = "wav"
    supports_boundaries = False
    online = False

    def __init__(self, *, lang: str = "en-us") -> None:
        self.lang = lang

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
        del volume, pitch, boundary  # Kokoro exposes speed only.
        speed = _rate_to_speed(rate)

        def _run() -> tuple[Any, int]:
            model = _load_model()
            return model.create(text, voice=voice, speed=speed, lang=self.lang)

        samples, sample_rate = await asyncio.get_running_loop().run_in_executor(None, _run)
        return SynthResult(
            audio=float_to_wav(samples, sample_rate),
            fmt="wav",
            sample_rate=sample_rate,
        )

    async def list_voices(self) -> list[VoiceInfo]:
        names: list[str] = []
        try:
            model = await asyncio.get_running_loop().run_in_executor(None, _load_model)
            names = sorted(model.get_voices())
        except Exception:
            names = sorted(KNOWN_VOICES)

        out = []
        for name in names:
            accent = "en-GB" if name.startswith(("bf_", "bm_")) else "en-US"
            gender = "Female" if name[1:2] == "f" else "Male"
            out.append(
                VoiceInfo(
                    name=name,
                    locale=accent,
                    gender=gender,
                    description=KNOWN_VOICES.get(name, ""),
                )
            )
        return out
