from ttskit.voices import OPENAI_VOICES, describe, resolve


def test_openai_names_map_to_edge_voices():
    assert resolve("alloy") == "en-US-JennyNeural"
    assert resolve("echo") == "en-US-GuyNeural"
    assert resolve("NOVA") == "en-US-AriaNeural"


def test_every_openai_voice_resolves_on_every_engine():
    for name in OPENAI_VOICES:
        assert resolve(name, "edge").endswith("Neural")
        assert "_" in resolve(name, "kokoro")


def test_friendly_aliases():
    assert resolve("jenny") == "en-US-JennyNeural"
    assert resolve("sonia") == "en-GB-SoniaNeural"


def test_native_names_pass_through_untouched():
    assert resolve("en-AU-NatashaNeural") == "en-AU-NatashaNeural"
    assert resolve("af_heart", "kokoro") == "af_heart"
    assert resolve("fr-FR-DeniseNeural") == "fr-FR-DeniseNeural"


def test_blank_voice_falls_back_to_the_default():
    assert resolve("", default="echo") == "en-US-GuyNeural"
    assert resolve(None, default="echo") == "en-US-GuyNeural"


def test_describe_lists_aliases_per_engine():
    assert "alloy" in describe("edge")
    assert describe("kokoro")["alloy"] == "af_heart"
