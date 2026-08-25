"""Shared fixtures.

The tests never hit the network: a fake engine returns real, decodable MP3
frames (built by the same silence generator the pipeline uses), so stitching,
duration maths and subtitle offsets are all exercised for real.
"""

from __future__ import annotations

import pytest

from ttskit.audio import mp3_silence
from ttskit.engines import Boundary, Engine, SynthResult, VoiceInfo

#: Milliseconds of audio the fake engine produces per character of input.
MS_PER_CHAR = 60


class FakeEngine(Engine):
    """Deterministic offline stand-in for a real backend."""

    name = "fake"
    native_format = "mp3"
    supports_boundaries = True
    online = False

    def __init__(self, *, fail_times: int = 0) -> None:
        self.calls: list[str] = []
        self.fail_times = fail_times

    async def synthesize(self, text, *, voice, rate="+0%", volume="+0%", pitch="+0Hz",
                         boundary="word") -> SynthResult:
        self.calls.append(text)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("simulated transient failure")

        words = text.split()
        marks: list[Boundary] = []
        cursor = 0.0
        for position, word in enumerate(words):
            span = len(word) * MS_PER_CHAR
            marks.append(Boundary(word, cursor, span, "word"))
            cursor += span
            if position < len(words) - 1:
                cursor += MS_PER_CHAR  # the space between words

        return SynthResult(
            audio=mp3_silence(int(cursor) or MS_PER_CHAR),
            fmt="mp3",
            sample_rate=24000,
            boundaries=marks,
        )

    async def list_voices(self) -> list[VoiceInfo]:
        return [
            VoiceInfo("fake-en-US-One", "en-US", "Female", "test voice"),
            VoiceInfo("fake-en-GB-Two", "en-GB", "Male", "test voice"),
        ]


@pytest.fixture
def fake_engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def sample_markdown() -> str:
    return (
        "---\ntitle: Ignored front matter\n---\n\n"
        "# First Chapter\n\n"
        "Hello there. This is the opening paragraph, and it has two sentences.\n\n"
        "- A bullet point.\n"
        "- Another bullet point.\n\n"
        "```python\nprint('never spoken')\n```\n\n"
        "## Second Chapter\n\n"
        "See [the docs](https://example.com) for **more** detail.\n"
    )
