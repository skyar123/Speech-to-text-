"""Drop-folder watcher.

Point this at wherever your generated Markdown lands and every new file is
narrated automatically:

    ttskit watch ./notes --out ./audio --voice nova

Files are only picked up once they stop changing (so half-written files are
never read aloud), and a small JSON ledger records what has been converted so
restarting the watcher does not redo the whole folder.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path

from .pipeline import RenderOptions, render_text, save
from .text import load_source

DEFAULT_PATTERNS = (".md", ".markdown", ".txt", ".html", ".htm", ".pdf")
LEDGER_NAME = ".ttskit-ledger.json"


def _digest(path: Path) -> str:
    stat = path.stat()
    key = f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class Ledger:
    """Remembers which (file, version) pairs have already been narrated."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / LEDGER_NAME
        self.entries: dict[str, str] = {}
        if self.path.is_file():
            try:
                self.entries = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.entries = {}

    def seen(self, path: Path) -> bool:
        return self.entries.get(path.name) == _digest(path)

    def record(self, path: Path) -> None:
        self.entries[path.name] = _digest(path)
        try:
            self.path.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")
        except OSError:
            pass


def _is_stable(path: Path, settle_s: float) -> bool:
    """True once a file has stopped growing — i.e. the writer has finished."""
    try:
        first = path.stat()
        time.sleep(settle_s)
        second = path.stat()
    except OSError:
        return False
    return first.st_size == second.st_size and first.st_mtime == second.st_mtime


async def convert_one(
    src: Path,
    out_dir: Path,
    opts: RenderOptions,
    *,
    out_format: str = "mp3",
    subtitles: tuple[str, ...] = (),
    max_chars: int = 2000,
    paragraph_gap_ms: int = 500,
    heading_gap_ms: int = 900,
) -> dict[str, str]:
    """Narrate a single file into *out_dir*."""
    text, title = load_source(str(src))
    result = await render_text(
        text,
        opts,
        title=title,
        max_chars=max_chars,
        paragraph_gap_ms=paragraph_gap_ms,
        heading_gap_ms=heading_gap_ms,
    )
    out_path = out_dir / f"{src.stem}.{out_format}"
    return save(
        result,
        out_path,
        title=title,
        album="ttskit watch",
        subtitles=subtitles,
    )


async def watch_folder(
    folder: str | Path,
    out_dir: str | Path,
    opts: RenderOptions | None = None,
    *,
    patterns: tuple[str, ...] = DEFAULT_PATTERNS,
    interval: float = 2.0,
    settle_s: float = 0.75,
    once: bool = False,
    out_format: str = "mp3",
    subtitles: tuple[str, ...] = (),
    on_event: Callable[[str, str], None] | None = None,
) -> int:
    """Watch *folder* and narrate matching files into *out_dir*.

    Returns the number of files converted. With ``once=True`` it does a single
    sweep and returns, which is what the ``--once`` flag and tests use.
    """
    src_dir = Path(folder)
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    if not src_dir.is_dir():
        raise NotADirectoryError(f"not a directory: {src_dir}")

    opts = opts or RenderOptions()
    ledger = Ledger(dest)
    converted = 0

    def notify(kind: str, message: str) -> None:
        if on_event:
            on_event(kind, message)

    notify("start", f"watching {src_dir} -> {dest}")

    while True:
        for path in sorted(src_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in patterns:
                continue
            if path.name == LEDGER_NAME or ledger.seen(path):
                continue
            if not _is_stable(path, settle_s):
                continue
            notify("converting", path.name)
            try:
                written = await convert_one(
                    path, dest, opts, out_format=out_format, subtitles=subtitles
                )
                ledger.record(path)
                converted += 1
                notify("done", written.get("audio", path.name))
            except Exception as exc:
                notify("error", f"{path.name}: {exc}")
                # Record it anyway so one bad file does not loop forever.
                ledger.record(path)

        if once:
            return converted
        await asyncio.sleep(interval)
