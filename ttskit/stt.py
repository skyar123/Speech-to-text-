"""Speech-to-text: the reverse direction, offline via faster-whisper.

This closes the loop — record a voice memo, transcribe it to Markdown, edit,
then send it back through the TTS pipeline. Whisper's word timestamps reuse
the same :class:`~ttskit.engines.Boundary` type as the TTS side, so the exact
same subtitle writer produces the ``.srt``/``.vtt``.

Install with ``pip install ttskit[stt]``. Models download themselves on first
use and then run entirely on CPU.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .engines import Boundary

DEFAULT_MODEL = os.environ.get("TTSKIT_WHISPER_MODEL", "base")
_models: dict[tuple[str, str], object] = {}


@dataclass
class Transcript:
    text: str
    language: str = ""
    duration_s: float = 0.0
    boundaries: list[Boundary] = field(default_factory=list)


def _load(model_size: str, compute_type: str):
    key = (model_size, compute_type)
    if key in _models:
        return _models[key]
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Transcription needs `pip install ttskit[stt]`.") from exc
    _models[key] = WhisperModel(model_size, device="cpu", compute_type=compute_type)
    return _models[key]


def transcribe(
    path: str | Path,
    *,
    language: str | None = None,
    model_size: str = DEFAULT_MODEL,
    compute_type: str = "int8",
    word_timestamps: bool = True,
) -> Transcript:
    """Transcribe an audio file. Any format ffmpeg-free faster-whisper accepts."""
    model = _load(model_size, compute_type)
    segments, info = model.transcribe(
        str(path),
        language=language,
        word_timestamps=word_timestamps,
        vad_filter=True,
    )

    pieces: list[str] = []
    marks: list[Boundary] = []
    for segment in segments:
        pieces.append(segment.text.strip())
        words = getattr(segment, "words", None) or []
        if word_timestamps and words:
            for word in words:
                marks.append(
                    Boundary(
                        text=word.word.strip(),
                        start_ms=word.start * 1000.0,
                        duration_ms=max(0.0, (word.end - word.start) * 1000.0),
                        kind="word",
                    )
                )
        else:
            marks.append(
                Boundary(
                    text=segment.text.strip(),
                    start_ms=segment.start * 1000.0,
                    duration_ms=max(0.0, (segment.end - segment.start) * 1000.0),
                    kind="sentence",
                )
            )

    return Transcript(
        text=" ".join(p for p in pieces if p).strip(),
        language=getattr(info, "language", "") or "",
        duration_s=float(getattr(info, "duration", 0.0) or 0.0),
        boundaries=marks,
    )


def transcribe_bytes(
    data: bytes,
    filename: str = "audio.wav",
    language: str | None = None,
    **kwargs,
) -> Transcript:
    """Transcribe an in-memory buffer by staging it to a temp file."""
    suffix = Path(filename).suffix or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        return transcribe(tmp.name, language=language, **kwargs)
    finally:
        os.unlink(tmp.name)
