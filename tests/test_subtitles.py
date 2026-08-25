from ttskit.engines import Boundary
from ttskit.subtitles import group_boundaries, to_json, to_srt, to_vtt

WORDS = [
    Boundary("Hello", 0, 400, "word"),
    Boundary("there,", 420, 380, "word"),
    Boundary("friend.", 820, 500, "word"),
    Boundary("A", 2200, 150, "word"),  # long gap -> new cue
    Boundary("second", 2360, 400, "word"),
    Boundary("line.", 2780, 400, "word"),
]


def test_pause_starts_a_new_cue():
    cues = group_boundaries(WORDS, max_gap_ms=700)
    assert len(cues) == 2
    assert cues[0].text == "Hello there, friend."
    assert cues[1].text == "A second line."


def test_punctuation_stays_attached():
    cues = group_boundaries(
        [Boundary("Wait", 0, 300, "word"), Boundary(",", 300, 20, "word"),
         Boundary("stop", 330, 300, "word")]
    )
    assert cues[0].text == "Wait, stop"


def test_long_cues_are_split_on_character_budget():
    words = [Boundary(f"word{i}", i * 300, 280, "word") for i in range(20)]
    cues = group_boundaries(words, max_chars=30)
    assert len(cues) > 1
    assert all(len(c.text) <= 30 for c in cues)


def test_srt_timestamp_format():
    srt = to_srt(WORDS)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,320\n")
    assert "\n\n2\n" in srt


def test_vtt_has_a_header_and_dot_separator():
    vtt = to_vtt(WORDS)
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:01.320" in vtt


def test_sentence_marks_become_one_cue_each():
    marks = [
        Boundary("First sentence.", 0, 1000, "sentence"),
        Boundary("Second sentence.", 1000, 1200, "sentence"),
    ]
    cues = group_boundaries(marks)
    assert [c.text for c in cues] == ["First sentence.", "Second sentence."]


def test_overlapping_sentence_cues_are_separated():
    marks = [
        Boundary("One.", 0, 1500, "sentence"),
        Boundary("Two.", 1000, 1000, "sentence"),
    ]
    cues = group_boundaries(marks)
    assert cues[1].start_ms >= cues[0].end_ms


def test_empty_input_yields_empty_output():
    assert group_boundaries([]) == []
    assert to_srt([]) == ""
    assert to_vtt([]).strip() == "WEBVTT"


def test_json_export_round_trips():
    import json

    payload = json.loads(to_json(WORDS))
    assert len(payload) == len(WORDS)
    assert payload[0] == {"text": "Hello", "start_ms": 0.0, "end_ms": 400.0, "kind": "word"}
