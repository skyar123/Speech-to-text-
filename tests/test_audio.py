import pytest

from ttskit import audio


def test_silent_frame_is_a_valid_mpeg_frame():
    data = audio.mp3_silence(500)
    frame = audio.first_frame(data)
    assert frame is not None
    assert frame.sample_rate == 24000
    assert frame.channels == 1
    assert frame.bitrate_kbps == 48


@pytest.mark.parametrize("ms", [100, 500, 1000, 2500])
def test_silence_duration_is_accurate(ms):
    # One MPEG2 Layer III frame is 24 ms, so rounding is the only error.
    assert abs(audio.mp3_duration_ms(audio.mp3_silence(ms)) - ms) <= 24


def test_zero_silence_is_empty():
    assert audio.mp3_silence(0) == b""


def test_concat_adds_durations():
    a, b = audio.mp3_silence(480), audio.mp3_silence(720)
    joined = audio.concat([a, b], "mp3")
    assert len(joined) == len(a) + len(b)
    assert abs(audio.mp3_duration_ms(joined) - 1200) <= 48


def test_concat_ignores_empty_parts():
    part = audio.mp3_silence(240)
    assert audio.concat([b"", part, b""], "mp3") == part


def test_id3_tags_are_stripped_before_joining():
    part = audio.mp3_silence(240)
    tagged = b"ID3\x03\x00\x00\x00\x00\x00\x0a" + bytes(10) + part
    assert audio.id3_size(tagged) == 20
    assert audio.strip_id3(tagged) == part
    assert audio.concat([tagged, part], "mp3") == part + part


def test_silence_matches_the_surrounding_stream():
    reference = audio.mp3_silence(240)
    gap = audio.silence(500, "mp3", like=reference)
    assert audio.first_frame(gap).frame_len == audio.first_frame(reference).frame_len


def test_wav_roundtrip():
    data = audio.wav_from_pcm(bytes(24000 * 2), 24000)
    assert audio.wav_params(data) == (24000, 1, 2, 24000)
    assert abs(audio.wav_duration_ms(data) - 1000) < 1


def test_wav_concat_sums_frames():
    one = audio.wav_silence(500, 24000)
    joined = audio.concat([one, one], "wav")
    assert abs(audio.wav_duration_ms(joined) - 1000) < 1


def test_float_to_wav():
    data = audio.float_to_wav([0.0, 0.5, -0.5, 1.0, -1.0], 24000)
    assert audio.wav_params(data)[3] == 5


def test_convert_without_ffmpeg_raises_clearly(monkeypatch):
    monkeypatch.setattr(audio, "have_ffmpeg", lambda: False)
    with pytest.raises(audio.FFmpegMissing) as excinfo:
        audio.convert(audio.mp3_silence(100), "mp3", "opus")
    assert "ffmpeg" in str(excinfo.value)


def test_convert_is_a_noop_for_matching_formats():
    data = audio.mp3_silence(100)
    assert audio.convert(data, "mp3", "mp3") is data


@pytest.mark.skipif(not audio.have_ffmpeg(), reason="ffmpeg not installed")
def test_ffmpeg_transcode_to_wav():
    wav = audio.convert(audio.mp3_silence(500), "mp3", "wav")
    assert wav[:4] == b"RIFF"
    assert abs(audio.wav_duration_ms(wav) - 500) < 60
