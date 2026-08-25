"""Runtime configuration, resolved from environment variables with sane defaults.

Every knob has a working default, so ``ttskit say "hello"`` runs with no setup.
A local ``.env`` file (see ``.env.example``) is loaded if present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_ENV_LOADED = False


def load_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """Minimal ``.env`` loader — no dependency on python-dotenv.

    Existing environment variables always win, so real env vars are never
    clobbered by a stale file.
    """
    global _ENV_LOADED
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)
    _ENV_LOADED = True


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class Settings:
    """Resolved settings for a ttskit process."""

    # --- engine selection -------------------------------------------------
    engine: str = field(default_factory=lambda: _env("TTSKIT_ENGINE", "edge"))
    default_voice: str = field(default_factory=lambda: _env("TTSKIT_DEFAULT_VOICE", "alloy"))

    # --- synthesis defaults ----------------------------------------------
    rate: str = field(default_factory=lambda: _env("TTSKIT_RATE", "+0%"))
    volume: str = field(default_factory=lambda: _env("TTSKIT_VOLUME", "+0%"))
    pitch: str = field(default_factory=lambda: _env("TTSKIT_PITCH", "+0Hz"))

    # --- long-form pipeline ----------------------------------------------
    max_chunk_chars: int = field(default_factory=lambda: _env_int("TTSKIT_MAX_CHUNK_CHARS", 2000))
    paragraph_gap_ms: int = field(default_factory=lambda: _env_int("TTSKIT_PARAGRAPH_GAP_MS", 500))
    heading_gap_ms: int = field(default_factory=lambda: _env_int("TTSKIT_HEADING_GAP_MS", 900))
    concurrency: int = field(default_factory=lambda: _env_int("TTSKIT_CONCURRENCY", 6))
    retries: int = field(default_factory=lambda: _env_int("TTSKIT_RETRIES", 3))

    # --- server -----------------------------------------------------------
    host: str = field(default_factory=lambda: _env("TTSKIT_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("TTSKIT_PORT", 5050))
    api_key: str = field(default_factory=lambda: _env("TTSKIT_API_KEY", ""))

    # --- offline engine ---------------------------------------------------
    kokoro_model: str = field(default_factory=lambda: _env("TTSKIT_KOKORO_MODEL", ""))
    kokoro_voices: str = field(default_factory=lambda: _env("TTSKIT_KOKORO_VOICES", ""))

    # --- misc -------------------------------------------------------------
    cache_dir: Path = field(
        default_factory=lambda: Path(
            _env("TTSKIT_CACHE_DIR", str(Path.home() / ".cache" / "ttskit"))
        )
    )
    ffmpeg: str = field(default_factory=lambda: _env("TTSKIT_FFMPEG", ""))

    def model_dir(self) -> Path:
        d = self.cache_dir / "models"
        d.mkdir(parents=True, exist_ok=True)
        return d


def get_settings() -> Settings:
    """Build a :class:`Settings` from the current environment."""
    if not _ENV_LOADED:
        load_dotenv()
    return Settings()
