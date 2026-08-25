"""Voice-name translation.

Callers may use three kinds of names and they all resolve to something an
engine understands:

1. **OpenAI names** — ``alloy``, ``echo``, ``nova`` …  Mapped per engine so an
   OpenAI client gets a sensible voice without knowing anything about Edge or
   Kokoro.
2. **Friendly aliases** — ``jenny``, ``guy``, ``sonia`` …  Short names for the
   Edge voices people reach for most.
3. **Native engine names** — ``en-US-JennyNeural``, ``af_heart`` …  Passed
   straight through, so nothing here can lock you out of a voice.
"""

from __future__ import annotations

# The eleven voice names the OpenAI TTS API accepts, mapped onto Microsoft
# Edge neural voices with roughly comparable timbre.
OPENAI_TO_EDGE: dict[str, str] = {
    "alloy": "en-US-JennyNeural",
    "echo": "en-US-GuyNeural",
    "fable": "en-GB-SoniaNeural",
    "onyx": "en-US-ChristopherNeural",
    "nova": "en-US-AriaNeural",
    "shimmer": "en-US-MichelleNeural",
    "ash": "en-US-AndrewNeural",
    "ballad": "en-GB-RyanNeural",
    "coral": "en-US-EmmaNeural",
    "sage": "en-US-AvaNeural",
    "verse": "en-US-BrianNeural",
}

# Same names mapped onto the Kokoro-82M voice library, for offline use.
OPENAI_TO_KOKORO: dict[str, str] = {
    "alloy": "af_heart",
    "echo": "am_adam",
    "fable": "bf_emma",
    "onyx": "am_michael",
    "nova": "af_bella",
    "shimmer": "af_nicole",
    "ash": "am_adam",
    "ballad": "bm_george",
    "coral": "af_sarah",
    "sage": "af_sky",
    "verse": "bm_lewis",
}

# Short, memorable aliases for popular Edge voices.
EDGE_ALIASES: dict[str, str] = {
    "jenny": "en-US-JennyNeural",
    "guy": "en-US-GuyNeural",
    "aria": "en-US-AriaNeural",
    "ava": "en-US-AvaNeural",
    "andrew": "en-US-AndrewNeural",
    "emma": "en-US-EmmaNeural",
    "brian": "en-US-BrianNeural",
    "christopher": "en-US-ChristopherNeural",
    "michelle": "en-US-MichelleNeural",
    "eric": "en-US-EricNeural",
    "roger": "en-US-RogerNeural",
    "steffan": "en-US-SteffanNeural",
    "sonia": "en-GB-SoniaNeural",
    "ryan": "en-GB-RyanNeural",
    "libby": "en-GB-LibbyNeural",
    "natasha": "en-AU-NatashaNeural",
    "william": "en-AU-WilliamNeural",
    "clara": "en-CA-ClaraNeural",
    "neerja": "en-IN-NeerjaNeural",
    "prabhat": "en-IN-PrabhatNeural",
}

OPENAI_VOICES: tuple[str, ...] = tuple(OPENAI_TO_EDGE)


def resolve(voice: str | None, engine: str = "edge", *, default: str = "alloy") -> str:
    """Resolve *voice* to a native voice name for *engine*.

    Unknown names are returned unchanged — that is what makes native names
    work, and it means a new Edge or Kokoro voice needs no code change here.
    """
    name = (voice or default or "").strip()
    if not name:
        name = default
    key = name.lower()

    if engine == "kokoro":
        if key in OPENAI_TO_KOKORO:
            return OPENAI_TO_KOKORO[key]
        return name

    if key in OPENAI_TO_EDGE:
        return OPENAI_TO_EDGE[key]
    if key in EDGE_ALIASES:
        return EDGE_ALIASES[key]
    return name


def describe(engine: str = "edge") -> dict[str, str]:
    """Return the full alias table used for *engine* (for ``ttskit voices``)."""
    if engine == "kokoro":
        return dict(OPENAI_TO_KOKORO)
    table = dict(OPENAI_TO_EDGE)
    table.update(EDGE_ALIASES)
    return table
