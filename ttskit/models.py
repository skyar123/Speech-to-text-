"""Download and locate the model files the offline engine needs."""

from __future__ import annotations

import shutil
import sys
import urllib.request
from pathlib import Path

from .config import get_settings

KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

#: filename -> (url, human-readable size)
KOKORO_FILES: dict[str, tuple[str, str]] = {
    "kokoro-v1.0.onnx": (f"{KOKORO_RELEASE}/kokoro-v1.0.onnx", "~310 MB"),
    "voices-v1.0.bin": (f"{KOKORO_RELEASE}/voices-v1.0.bin", "~27 MB"),
}


def model_paths() -> tuple[Path, Path]:
    """Return ``(model_path, voices_path)`` for the Kokoro engine.

    Explicit settings win; otherwise the shared cache directory is used.
    """
    settings = get_settings()
    model_dir = settings.model_dir()
    model = Path(settings.kokoro_model) if settings.kokoro_model else model_dir / "kokoro-v1.0.onnx"
    voices = (
        Path(settings.kokoro_voices) if settings.kokoro_voices else model_dir / "voices-v1.0.bin"
    )
    return model, voices


def models_present() -> bool:
    return all(p.is_file() for p in model_paths())


def _download(url: str, dest: Path, *, quiet: bool = False) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    def hook(block: int, block_size: int, total: int) -> None:
        if quiet or total <= 0:
            return
        done = min(block * block_size, total)
        pct = done * 100 // total
        sys.stderr.write(f"\r  {dest.name}: {pct:3d}%  ({done // 1048576} / {total // 1048576} MB)")
        sys.stderr.flush()

    urllib.request.urlretrieve(url, tmp, reporthook=hook)  # noqa: S310 - fixed https URLs
    if not quiet:
        sys.stderr.write("\n")
    shutil.move(str(tmp), str(dest))
    return dest


def download_kokoro(*, force: bool = False, quiet: bool = False) -> tuple[Path, Path]:
    """Fetch the Kokoro-82M ONNX weights and voice pack into the cache."""
    model, voices = model_paths()
    targets = {"kokoro-v1.0.onnx": model, "voices-v1.0.bin": voices}
    for filename, (url, size) in KOKORO_FILES.items():
        dest = targets[filename]
        if dest.is_file() and not force:
            if not quiet:
                print(f"  {dest.name}: already present ({dest.stat().st_size // 1048576} MB)")
            continue
        if not quiet:
            print(f"  fetching {filename} ({size}) -> {dest}")
        _download(url, dest, quiet=quiet)
    return model, voices
