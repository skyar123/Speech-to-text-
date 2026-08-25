"""The long-form pipeline: document in, finished audiobook out.

Chunks are synthesized concurrently but reassembled strictly in order, with
silence inserted between paragraphs and around headings. Because every chunk's
real duration is measured after synthesis, the engine's per-chunk timing marks
can be shifted onto the master timeline — so a two-hour audiobook still gets
accurate subtitles and chapter offsets.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import audio as audio_mod
from .engines import Boundary, Engine, get_engine
from .text import Chunk, Document, build_document, load_source
from .voices import resolve

ProgressFn = Callable[[int, int, str], None]


@dataclass
class RenderOptions:
    """Everything that controls how a document is voiced."""

    engine: str = "edge"
    voice: str = "alloy"
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"
    boundary: str = "sentence"
    concurrency: int = 6
    retries: int = 3
    retry_base_delay: float = 1.0


@dataclass
class Chapter:
    title: str
    start_ms: float
    end_ms: float


@dataclass
class RenderResult:
    audio: bytes
    fmt: str
    boundaries: list[Boundary] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    duration_ms: float = 0.0
    chunk_count: int = 0
    elapsed_s: float = 0.0

    @property
    def duration_hms(self) -> str:
        total = int(self.duration_ms // 1000)
        return f"{total // 3600:d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


class SynthesisError(RuntimeError):
    """Raised when a chunk could not be synthesized after all retries."""


async def _synthesize_chunk(
    engine: Engine,
    chunk: Chunk,
    voice: str,
    opts: RenderOptions,
    semaphore: asyncio.Semaphore,
):
    """Synthesize one chunk, retrying transient failures with backoff."""
    last_error: Exception | None = None
    for attempt in range(max(1, opts.retries)):
        async with semaphore:
            try:
                return await engine.synthesize(
                    chunk.text,
                    voice=voice,
                    rate=opts.rate,
                    volume=opts.volume,
                    pitch=opts.pitch,
                    boundary=opts.boundary,
                )
            except Exception as exc:  # network hiccups are the common case
                last_error = exc
        if attempt < opts.retries - 1:
            await asyncio.sleep(opts.retry_base_delay * (2**attempt))
    raise SynthesisError(
        f"chunk {chunk.index} failed after {opts.retries} attempts: {last_error}"
    ) from last_error


async def render_document(
    doc: Document,
    opts: RenderOptions | None = None,
    *,
    engine: Engine | None = None,
    progress: ProgressFn | None = None,
) -> RenderResult:
    """Render every chunk of *doc* and stitch the result into one buffer."""
    opts = opts or RenderOptions()
    engine = engine or get_engine(opts.engine)
    voice = resolve(opts.voice, engine.name)
    started = time.monotonic()

    if not doc.chunks:
        return RenderResult(audio=b"", fmt=engine.native_format)

    semaphore = asyncio.Semaphore(max(1, opts.concurrency))
    done = 0
    total = len(doc.chunks)

    async def run(chunk: Chunk):
        nonlocal done
        result = await _synthesize_chunk(engine, chunk, voice, opts, semaphore)
        done += 1
        if progress:
            progress(done, total, chunk.text[:60])
        return result

    results = await asyncio.gather(*(run(chunk) for chunk in doc.chunks))

    fmt = results[0].fmt
    parts: list[bytes] = []
    boundaries: list[Boundary] = []
    chapter_spans: dict[int, list[float]] = {}
    offset_ms = 0.0

    for chunk, result in zip(doc.chunks, results, strict=True):
        span = chapter_spans.setdefault(chunk.chapter_index, [offset_ms, offset_ms])
        span[0] = min(span[0], offset_ms)

        parts.append(result.audio)
        for mark in result.boundaries:
            boundaries.append(mark.shifted(offset_ms))
        offset_ms += audio_mod.duration_ms(result.audio, fmt)

        if chunk.gap_after_ms > 0:
            gap = audio_mod.silence(chunk.gap_after_ms, fmt, like=result.audio)
            if gap:
                parts.append(gap)
                offset_ms += audio_mod.duration_ms(gap, fmt)
        span[1] = offset_ms

    chapters = [
        Chapter(
            title=doc.chapters[index] if index < len(doc.chapters) else f"Part {index + 1}",
            start_ms=span[0],
            end_ms=span[1],
        )
        for index, span in sorted(chapter_spans.items())
    ]

    return RenderResult(
        audio=audio_mod.concat(parts, fmt),
        fmt=fmt,
        boundaries=boundaries,
        chapters=chapters,
        duration_ms=offset_ms,
        chunk_count=total,
        elapsed_s=time.monotonic() - started,
    )


async def render_text(
    text: str,
    opts: RenderOptions | None = None,
    *,
    title: str = "",
    max_chars: int = 2000,
    paragraph_gap_ms: int = 500,
    heading_gap_ms: int = 900,
    speak_headings: bool = True,
    engine: Engine | None = None,
    progress: ProgressFn | None = None,
) -> RenderResult:
    """Convenience wrapper: parse *text* into a document, then render it."""
    doc = build_document(
        text,
        title=title,
        max_chars=max_chars,
        paragraph_gap_ms=paragraph_gap_ms,
        heading_gap_ms=heading_gap_ms,
        speak_headings=speak_headings,
    )
    return await render_document(doc, opts, engine=engine, progress=progress)


async def render_file(
    source: str,
    opts: RenderOptions | None = None,
    **kwargs,
) -> tuple[RenderResult, str]:
    """Load a path or URL and render it. Returns ``(result, derived_title)``."""
    text, title = load_source(source)
    kwargs.setdefault("title", title)
    return await render_text(text, opts, **kwargs), title


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def save(
    result: RenderResult,
    out_path: str | Path,
    *,
    title: str = "",
    artist: str = "ttskit",
    album: str = "",
    track: int | None = 1,
    subtitles: tuple[str, ...] = (),
    chapters_m4b: bool = False,
) -> dict[str, str]:
    """Write the rendered audio plus optional sidecar files.

    Returns a mapping of ``kind -> path`` for everything written.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    target_fmt = out.suffix.lstrip(".").lower() or "mp3"
    written: dict[str, str] = {}

    if target_fmt in {"m4b", "m4a"}:
        # Encode to MP3 first so chapter offsets line up with a real timeline.
        intermediate = out.with_suffix(".stage.mp3")
        data = (
            result.audio
            if result.fmt == "mp3"
            else audio_mod.convert(result.audio, result.fmt, "mp3")
        )
        intermediate.write_bytes(data)
        try:
            audio_mod.write_m4b(
                intermediate,
                out,
                [(c.title, c.start_ms, c.end_ms) for c in result.chapters],
                title=title or None,
                artist=artist or None,
            )
        finally:
            intermediate.unlink(missing_ok=True)
        written["audio"] = str(out)
    else:
        data = (
            result.audio
            if target_fmt == result.fmt
            else audio_mod.convert(result.audio, result.fmt, target_fmt)
        )
        out.write_bytes(data)
        written["audio"] = str(out)
        if target_fmt == "mp3":
            audio_mod.write_id3(
                out,
                title=title or out.stem,
                artist=artist,
                album=album or title or out.stem,
                track=track,
                comment=f"Generated by ttskit — {result.duration_hms}",
            )

    if subtitles and result.boundaries:
        from .subtitles import write_all

        for path in write_all(result.boundaries, str(out.with_suffix("")), formats=subtitles):
            written[Path(path).suffix.lstrip(".")] = path

    if chapters_m4b and result.chapters and not target_fmt.startswith("m4"):
        chapters_file = out.with_suffix(".chapters.txt")
        chapters_file.write_text(
            "\n".join(
                f"{int(c.start_ms // 3_600_000):02d}:"
                f"{int(c.start_ms // 60_000) % 60:02d}:"
                f"{int(c.start_ms // 1000) % 60:02d} {c.title}"
                for c in result.chapters
            )
            + "\n",
            encoding="utf-8",
        )
        written["chapters"] = str(chapters_file)

    return written
