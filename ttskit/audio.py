"""Audio plumbing: MP3/WAV stitching, silence, format conversion, ID3 tags.

The design goal here is that **nothing in the core pipeline requires ffmpeg**.
MP3 output from a single engine is constant-bitrate and self-framing, so
segments can be concatenated at the frame level, and digital silence can be
emitted as hand-built MPEG frames that match the surrounding audio exactly.

ffmpeg is used when it *is* available (and for formats MP3/WAV cannot cover:
opus, aac, flac, m4b chapters). It is discovered in this order:

1. ``TTSKIT_FFMPEG`` / ``FFMPEG_BINARY`` environment variable
2. ``ffmpeg`` on ``PATH``
3. the static binary shipped by ``imageio-ffmpeg`` (``pip install ttskit[ffmpeg]``)
"""

from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# --------------------------------------------------------------------------
# ffmpeg discovery
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def ffmpeg_path() -> str | None:
    """Locate an ffmpeg binary, or return ``None`` if there is none."""
    for var in ("TTSKIT_FFMPEG", "FFMPEG_BINARY"):
        candidate = os.environ.get(var)
        if candidate and Path(candidate).exists():
            return candidate

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:  # optional dependency: a self-contained static build
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:  # pragma: no cover - depends on optional install
        pass
    return None


def have_ffmpeg() -> bool:
    return ffmpeg_path() is not None


class FFmpegMissing(RuntimeError):
    """Raised when an operation genuinely cannot be done without ffmpeg."""

    def __init__(self, what: str) -> None:
        super().__init__(
            f"{what} requires ffmpeg. Install it with `pip install ttskit[ffmpeg]` "
            f"(ships a static build, no system packages) or install ffmpeg yourself."
        )


def run_ffmpeg(args: list[str], *, input_bytes: bytes | None = None) -> bytes:
    """Run ffmpeg with *args* and return stdout, raising on failure."""
    exe = ffmpeg_path()
    if exe is None:
        raise FFmpegMissing("This conversion")
    proc = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", *args],
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {detail[:500]}")
    return proc.stdout


# --------------------------------------------------------------------------
# MP3 frame parsing
# --------------------------------------------------------------------------

_BITRATES_V1_L3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_BITRATES_V2_L3 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
_RATES = {
    3: (44100, 48000, 32000),  # MPEG 1
    2: (22050, 24000, 16000),  # MPEG 2
    0: (11025, 12000, 8000),  # MPEG 2.5
}


@dataclass(frozen=True)
class Mp3Frame:
    """The parts of an MPEG audio frame header that we care about."""

    version: int  # 3 = MPEG1, 2 = MPEG2, 0 = MPEG2.5
    bitrate_index: int
    sample_rate_index: int
    padding: int
    channel_mode: int  # 3 = mono
    frame_len: int
    samples: int

    @property
    def sample_rate(self) -> int:
        return _RATES[self.version][self.sample_rate_index]

    @property
    def bitrate_kbps(self) -> int:
        table = _BITRATES_V1_L3 if self.version == 3 else _BITRATES_V2_L3
        return table[self.bitrate_index]

    @property
    def channels(self) -> int:
        return 1 if self.channel_mode == 3 else 2

    @property
    def duration_ms(self) -> float:
        return self.samples * 1000.0 / self.sample_rate


def id3_size(data: bytes) -> int:
    """Return the byte length of a leading ID3v2 tag (0 if there is none)."""
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    flags = data[5]
    size = (
        (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
    )
    total = 10 + size
    if flags & 0x10:  # footer present
        total += 10
    return total


def strip_id3(data: bytes) -> bytes:
    """Drop a leading ID3v2 tag and a trailing ID3v1 tag."""
    out = data[id3_size(data) :]
    if len(out) >= 128 and out[-128:-125] == b"TAG":
        out = out[:-128]
    return out


def parse_frame(data: bytes, pos: int = 0) -> Mp3Frame | None:
    """Parse the MPEG frame header at *pos*, or return ``None`` if invalid."""
    if pos + 4 > len(data):
        return None
    b0, b1, b2, b3 = data[pos], data[pos + 1], data[pos + 2], data[pos + 3]
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return None
    version = (b1 >> 3) & 0x03
    layer = (b1 >> 1) & 0x03
    if version == 1 or layer != 1:  # reserved version, or not Layer III
        return None
    bitrate_index = (b2 >> 4) & 0x0F
    sample_rate_index = (b2 >> 2) & 0x03
    if bitrate_index in (0, 15) or sample_rate_index == 3:
        return None
    padding = (b2 >> 1) & 0x01
    channel_mode = (b3 >> 6) & 0x03

    table = _BITRATES_V1_L3 if version == 3 else _BITRATES_V2_L3
    bitrate = table[bitrate_index] * 1000
    sample_rate = _RATES[version][sample_rate_index]
    samples = 1152 if version == 3 else 576
    coeff = 144 if version == 3 else 72
    frame_len = coeff * bitrate // sample_rate + padding
    if frame_len < 8:
        return None
    return Mp3Frame(
        version=version,
        bitrate_index=bitrate_index,
        sample_rate_index=sample_rate_index,
        padding=padding,
        channel_mode=channel_mode,
        frame_len=frame_len,
        samples=samples,
    )


def first_frame(data: bytes) -> Mp3Frame | None:
    """Find the first real audio frame, skipping ID3 tags and any junk."""
    payload = strip_id3(data)
    limit = min(len(payload), 1 << 16)
    for pos in range(limit):
        frame = parse_frame(payload, pos)
        if frame is None:
            continue
        # Confirm with the following frame — guards against false sync words.
        nxt = parse_frame(payload, pos + frame.frame_len)
        if nxt is not None or pos + frame.frame_len >= len(payload):
            return frame
    return None


def mp3_duration_ms(data: bytes) -> float:
    """Total duration of an MP3 buffer, by walking its frames."""
    payload = strip_id3(data)
    pos, total = 0, 0.0
    # Skip to the first valid frame.
    first = None
    for start in range(min(len(payload), 1 << 16)):
        first = parse_frame(payload, start)
        if first is not None:
            pos = start
            break
    if first is None:
        return 0.0
    while pos < len(payload):
        frame = parse_frame(payload, pos)
        if frame is None:
            pos += 1
            continue
        total += frame.duration_ms
        pos += frame.frame_len
    return total


# --------------------------------------------------------------------------
# Silence
# --------------------------------------------------------------------------


def mp3_silence(duration_ms: int, like: Mp3Frame | None = None) -> bytes:
    """Build digital silence as raw MPEG Layer III frames.

    A Layer III frame whose side-info is all zeros carries no main data, so
    every decoder renders it as silence. Building the frames by hand means
    paragraph gaps cost no ffmpeg call and no temp file, and matching *like*
    keeps the stream constant-bitrate so players report the right duration.
    """
    if duration_ms <= 0:
        return b""

    version = like.version if like else 2
    bitrate_index = like.bitrate_index if like else 6  # 48 kbps for MPEG2
    sample_rate_index = like.sample_rate_index if like else 1  # 24 kHz
    channel_mode = like.channel_mode if like else 3  # mono

    table = _BITRATES_V1_L3 if version == 3 else _BITRATES_V2_L3
    bitrate = table[bitrate_index] * 1000
    sample_rate = _RATES[version][sample_rate_index]
    samples = 1152 if version == 3 else 576
    coeff = 144 if version == 3 else 72
    frame_len = coeff * bitrate // sample_rate

    mono = channel_mode == 3
    if version == 3:
        side_info = 17 if mono else 32
    else:
        side_info = 9 if mono else 17

    header = bytes(
        (
            0xFF,
            0xE0 | (version << 3) | (1 << 1) | 1,  # Layer III, no CRC
            (bitrate_index << 4) | (sample_rate_index << 2),
            (channel_mode << 6),
        )
    )
    frame = header + bytes(side_info) + bytes(frame_len - 4 - side_info)

    frame_ms = samples * 1000.0 / sample_rate
    count = max(1, round(duration_ms / frame_ms))
    return frame * count


def wav_silence(duration_ms: int, sample_rate: int, channels: int = 1, width: int = 2) -> bytes:
    """Build a silent WAV buffer matching the given PCM parameters."""
    frames = int(sample_rate * max(duration_ms, 0) / 1000)
    return wav_from_pcm(bytes(frames * channels * width), sample_rate, channels, width)


def silence(duration_ms: int, fmt: str, *, like: bytes | None = None) -> bytes:
    """Format-aware silence, shaped to match the *like* buffer when given."""
    if fmt == "mp3":
        return mp3_silence(duration_ms, first_frame(like) if like else None)
    if fmt == "wav":
        if like:
            rate, channels, width, _ = wav_params(like)
            return wav_silence(duration_ms, rate, channels, width)
        return wav_silence(duration_ms, 24000)
    raise ValueError(f"cannot generate silence for format {fmt!r}")


# --------------------------------------------------------------------------
# WAV helpers (stdlib only)
# --------------------------------------------------------------------------


def wav_from_pcm(pcm: bytes, sample_rate: int, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_params(data: bytes) -> tuple[int, int, int, int]:
    """Return ``(sample_rate, channels, sample_width, n_frames)``."""
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()


def wav_pcm(data: bytes) -> bytes:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.readframes(w.getnframes())


def float_to_wav(samples, sample_rate: int, channels: int = 1) -> bytes:
    """Convert an iterable/array of floats in [-1, 1] to a 16-bit WAV buffer."""
    try:
        import numpy as np

        arr = np.asarray(samples, dtype="float32")
        arr = np.clip(arr, -1.0, 1.0)
        pcm = (arr * 32767.0).astype("<i2").tobytes()
    except ImportError:  # pragma: no cover - numpy ships with the offline extra
        pcm = b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, float(s))) * 32767)) for s in samples
        )
    return wav_from_pcm(pcm, sample_rate, channels)


def wav_duration_ms(data: bytes) -> float:
    rate, _channels, _width, frames = wav_params(data)
    return frames * 1000.0 / rate if rate else 0.0


def duration_ms(data: bytes, fmt: str) -> float:
    if fmt == "mp3":
        return mp3_duration_ms(data)
    if fmt == "wav":
        return wav_duration_ms(data)
    if have_ffmpeg():
        return _probe_duration_ms(data, fmt)
    raise ValueError(f"cannot measure duration of {fmt!r} without ffmpeg")


def _probe_duration_ms(data: bytes, fmt: str) -> float:
    with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as fh:
        fh.write(data)
        path = fh.name
    try:
        # Decode to raw 16-bit mono PCM and measure it; this needs no ffprobe
        # and is exact. Only reached for formats MP3/WAV parsing cannot cover.
        raw = run_ffmpeg(["-i", path, "-f", "s16le", "-ac", "1", "-ar", "24000", "-"])
        return len(raw) / 2 / 24000 * 1000
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------
# Concatenation and conversion
# --------------------------------------------------------------------------


def concat(parts: list[bytes], fmt: str) -> bytes:
    """Join audio *parts* that share an encoder and format."""
    parts = [p for p in parts if p]
    if not parts:
        return b""
    if len(parts) == 1:
        return parts[0]
    if fmt == "mp3":
        return b"".join(strip_id3(p) for p in parts)
    if fmt == "wav":
        return _concat_wav(parts)
    if have_ffmpeg():
        return _concat_ffmpeg(parts, fmt)
    raise ValueError(f"cannot concatenate format {fmt!r} without ffmpeg")


def _concat_wav(parts: list[bytes]) -> bytes:
    rate, channels, width, _ = wav_params(parts[0])
    pcm = b"".join(wav_pcm(p) for p in parts)
    return wav_from_pcm(pcm, rate, channels, width)


def _concat_ffmpeg(parts: list[bytes], fmt: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        listing = []
        for i, part in enumerate(parts):
            p = tmpdir / f"part{i:05d}.{fmt}"
            p.write_bytes(part)
            listing.append(f"file '{p.as_posix()}'")
        manifest = tmpdir / "list.txt"
        manifest.write_text("\n".join(listing), encoding="utf-8")
        out = tmpdir / f"joined.{fmt}"
        run_ffmpeg(
            [
                "-f", "concat", "-safe", "0", "-i", manifest.as_posix(),
                "-c", "copy", "-y", out.as_posix(),
            ]
        )
        return out.read_bytes()


#: Response formats the OpenAI speech API accepts, and the ffmpeg args for each.
FORMAT_ARGS: dict[str, list[str]] = {
    "mp3": ["-f", "mp3", "-codec:a", "libmp3lame", "-q:a", "4"],
    "opus": ["-f", "ogg", "-codec:a", "libopus", "-b:a", "48k"],
    "aac": ["-f", "adts", "-codec:a", "aac", "-b:a", "96k"],
    "flac": ["-f", "flac"],
    "wav": ["-f", "wav"],
    "pcm": ["-f", "s16le", "-ar", "24000", "-ac", "1"],
}

MIME_TYPES: dict[str, str] = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/L16",
    "m4b": "audio/mp4",
    "m4a": "audio/mp4",
}


#: Formats whose container header carries sizes that ffmpeg can only fill in
#: by seeking back — they must be written to a real file, never to a pipe, or
#: the header ends up with 0xFFFFFFFF placeholders that confuse strict readers.
_SEEKABLE_OUTPUT = {"wav", "flac"}


def convert(data: bytes, src_fmt: str, dst_fmt: str) -> bytes:
    """Transcode *data* from *src_fmt* to *dst_fmt*."""
    if src_fmt == dst_fmt:
        return data
    if dst_fmt not in FORMAT_ARGS:
        raise ValueError(f"unsupported target format {dst_fmt!r}")
    if not have_ffmpeg():
        raise FFmpegMissing(f"Converting {src_fmt} to {dst_fmt}")

    if dst_fmt in _SEEKABLE_OUTPUT:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / f"in.{src_fmt}"
            target = Path(tmp) / f"out.{dst_fmt}"
            source.write_bytes(data)
            run_ffmpeg(
                ["-i", source.as_posix(), *FORMAT_ARGS[dst_fmt], "-y", target.as_posix()]
            )
            return target.read_bytes()

    return run_ffmpeg(["-i", "pipe:0", *FORMAT_ARGS[dst_fmt], "pipe:1"], input_bytes=data)


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------


def write_id3(
    path: str | os.PathLike[str],
    *,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    track: int | None = None,
    genre: str | None = "Speech",
    comment: str | None = None,
) -> None:
    """Write ID3v2 tags onto an MP3 file (no-op if mutagen is unavailable)."""
    try:
        from mutagen.id3 import COMM, ID3, TALB, TCON, TIT2, TPE1, TRCK
        from mutagen.mp3 import MP3
    except ImportError:  # pragma: no cover - mutagen is a hard dependency
        return

    audio = MP3(str(path))
    if audio.tags is None:
        audio.add_tags()
    tags: ID3 = audio.tags  # type: ignore[assignment]
    if title:
        tags.add(TIT2(encoding=3, text=title))
    if artist:
        tags.add(TPE1(encoding=3, text=artist))
    if album:
        tags.add(TALB(encoding=3, text=album))
    if track is not None:
        tags.add(TRCK(encoding=3, text=str(track)))
    if genre:
        tags.add(TCON(encoding=3, text=genre))
    if comment:
        tags.add(COMM(encoding=3, lang="eng", desc="", text=comment))
    audio.save(v2_version=3)


def write_m4b(
    audio_path: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    chapters: list[tuple[str, float, float]],
    *,
    title: str | None = None,
    artist: str | None = None,
) -> None:
    """Produce a chaptered ``.m4b`` audiobook from an existing audio file.

    *chapters* is a list of ``(title, start_ms, end_ms)``.
    """
    if not have_ffmpeg():
        raise FFmpegMissing("Building an .m4b audiobook")

    lines = [";FFMETADATA1"]
    if title:
        lines.append(f"title={_meta_escape(title)}")
    if artist:
        lines.append(f"artist={_meta_escape(artist)}")
    for name, start, end in chapters:
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(start)}",
            f"END={int(max(end, start + 1))}",
            f"title={_meta_escape(name)}",
        ]
    with tempfile.TemporaryDirectory() as tmp:
        meta = Path(tmp) / "chapters.txt"
        meta.write_text("\n".join(lines) + "\n", encoding="utf-8")
        run_ffmpeg(
            [
                "-i", str(audio_path),
                "-i", meta.as_posix(),
                "-map_metadata", "1",
                "-codec:a", "aac", "-b:a", "64k",
                "-f", "mp4", "-y", str(out_path),
            ]
        )


def _meta_escape(value: str) -> str:
    for ch in ("\\", "=", ";", "#", "\n"):
        value = value.replace(ch, "\\" + ch)
    return value
