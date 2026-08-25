"""ttskit — a free, local-first text-to-speech toolkit.

Four capabilities, one shared core:

* ``ttskit.server``    — OpenAI-compatible ``/v1/audio/speech`` drop-in.
* ``ttskit.pipeline``  — long-form Markdown/PDF/HTML -> audiobook.
* ``ttskit.subtitles`` — word/sentence timed ``.srt`` / ``.vtt`` output.
* ``ttskit.engines``   — pluggable backends (Edge cloud, Kokoro offline).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
