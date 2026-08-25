"""Subtitle generation from engine timing marks.

Edge reports the exact start and duration of every word (or sentence) it
speaks. That is enough to emit real, synchronised captions — no forced
alignment, no Whisper pass, no guessing.

Word marks are grouped into readable cues: a cue ends when it would get too
long to read, too long on screen, when the speaker pauses, or at a sentence
end. Sentence marks are emitted one cue each.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .engines import Boundary

DEFAULT_MAX_CHARS = 42
DEFAULT_MAX_MS = 6000
DEFAULT_GAP_MS = 700


@dataclass
class Cue:
    index: int
    start_ms: float
    end_ms: float
    text: str


def _timestamp(ms: float, *, comma: bool) -> str:
    ms = max(0.0, ms)
    total = int(round(ms))
    hours, rem = divmod(total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    sep = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{millis:03d}"


def group_boundaries(
    boundaries: list[Boundary],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_duration_ms: int = DEFAULT_MAX_MS,
    max_gap_ms: int = DEFAULT_GAP_MS,
) -> list[Cue]:
    """Turn timing marks into caption cues."""
    marks = [b for b in boundaries if b.text and b.text.strip()]
    if not marks:
        return []

    # Sentence marks are already cue-sized.
    if all(b.kind == "sentence" for b in marks):
        return [
            Cue(i + 1, b.start_ms, b.end_ms, b.text.strip())
            for i, b in enumerate(_dedupe_overlaps(marks))
        ]

    cues: list[Cue] = []
    words: list[Boundary] = []

    def flush() -> None:
        if not words:
            return
        text = _join_words(w.text for w in words)
        cues.append(Cue(len(cues) + 1, words[0].start_ms, words[-1].end_ms, text))
        words.clear()

    for mark in marks:
        if words:
            gap = mark.start_ms - words[-1].end_ms
            projected = _join_words([*(w.text for w in words), mark.text])
            span = mark.end_ms - words[0].start_ms
            if gap > max_gap_ms or len(projected) > max_chars or span > max_duration_ms:
                flush()
            elif words[-1].text.rstrip().endswith((".", "!", "?", "…")):
                flush()
        words.append(mark)
    flush()
    return cues


def _join_words(parts) -> str:
    """Join word marks, keeping punctuation attached to the previous word."""
    out = ""
    for part in parts:
        token = part.strip()
        if not token:
            continue
        if not out:
            out = token
        elif token[0] in ",.!?;:%)]}'\"…" or token.startswith("'"):
            out += token
        elif out[-1] in "([{“‘$":
            out += token
        else:
            out += " " + token
    return out


def _dedupe_overlaps(marks: list[Boundary]) -> list[Boundary]:
    """Nudge cue starts so consecutive cues never overlap."""
    fixed: list[Boundary] = []
    for mark in marks:
        if fixed and mark.start_ms < fixed[-1].end_ms:
            shift = fixed[-1].end_ms - mark.start_ms
            mark = Boundary(mark.text, fixed[-1].end_ms, max(0.0, mark.duration_ms - shift),
                            mark.kind)
        fixed.append(mark)
    return fixed


def to_srt(boundaries: list[Boundary], **kwargs) -> str:
    """Render marks as SubRip (``.srt``)."""
    cues = group_boundaries(boundaries, **kwargs)
    blocks = [
        f"{cue.index}\n"
        f"{_timestamp(cue.start_ms, comma=True)} --> {_timestamp(cue.end_ms, comma=True)}\n"
        f"{cue.text}"
        for cue in cues
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def to_vtt(boundaries: list[Boundary], **kwargs) -> str:
    """Render marks as WebVTT (``.vtt``)."""
    cues = group_boundaries(boundaries, **kwargs)
    blocks = ["WEBVTT", ""]
    for cue in cues:
        blocks.append(f"{cue.index}")
        blocks.append(
            f"{_timestamp(cue.start_ms, comma=False)} --> {_timestamp(cue.end_ms, comma=False)}"
        )
        blocks.append(cue.text)
        blocks.append("")
    return "\n".join(blocks)


def to_json(boundaries: list[Boundary]) -> str:
    """Raw per-mark timings — the input for karaoke-style word highlighting."""
    payload = [
        {
            "text": b.text,
            "start_ms": round(b.start_ms, 2),
            "end_ms": round(b.end_ms, 2),
            "kind": b.kind,
        }
        for b in boundaries
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def write_all(
    boundaries: list[Boundary],
    stem: str,
    *,
    formats: tuple[str, ...] = ("srt", "vtt"),
    **kwargs,
) -> list[str]:
    """Write the requested subtitle formats next to ``<stem>`` and return paths."""
    from pathlib import Path

    written: list[str] = []
    for fmt in formats:
        path = Path(f"{stem}.{fmt}")
        if fmt == "srt":
            path.write_text(to_srt(boundaries, **kwargs), encoding="utf-8")
        elif fmt == "vtt":
            path.write_text(to_vtt(boundaries, **kwargs), encoding="utf-8")
        elif fmt == "json":
            path.write_text(to_json(boundaries), encoding="utf-8")
        else:
            raise ValueError(f"unknown subtitle format {fmt!r}")
        written.append(str(path))
    return written
