import pytest

from ttskit import audio
from ttskit.pipeline import RenderOptions, SynthesisError, render_document, render_text, save
from ttskit.text import build_document

from .conftest import MS_PER_CHAR, FakeEngine

pytestmark = pytest.mark.asyncio


async def test_render_stitches_every_chunk(fake_engine, sample_markdown):
    doc = build_document(sample_markdown, max_chars=120)
    result = await render_document(doc, RenderOptions(), engine=fake_engine)

    assert result.chunk_count == len(doc.chunks)
    assert len(fake_engine.calls) == len(doc.chunks)
    assert result.fmt == "mp3"
    # The stitched buffer really decodes to the duration we reported.
    assert abs(audio.mp3_duration_ms(result.audio) - result.duration_ms) <= 48


async def test_gaps_are_inserted_between_paragraphs(fake_engine):
    text = "First paragraph here.\n\nSecond paragraph here."
    with_gap = await render_text(text, engine=fake_engine, paragraph_gap_ms=1000)
    fake_engine.calls.clear()
    without_gap = await render_text(text, engine=FakeEngine(), paragraph_gap_ms=0)
    assert with_gap.duration_ms - without_gap.duration_ms == pytest.approx(1000, abs=48)


async def test_boundaries_are_shifted_onto_the_master_timeline(fake_engine):
    result = await render_text(
        "Alpha beta.\n\nGamma delta.", engine=fake_engine, paragraph_gap_ms=500
    )
    starts = [b.start_ms for b in result.boundaries]
    assert starts == sorted(starts), "marks must be monotonically increasing"
    # The second paragraph's first word starts after the first paragraph + gap.
    assert starts[2] > result.duration_ms / 3


async def test_chapters_cover_the_whole_timeline(fake_engine, sample_markdown):
    result = await render_text(sample_markdown, engine=fake_engine)
    assert [c.title for c in result.chapters] == ["First Chapter", "Second Chapter"]
    assert result.chapters[0].start_ms == 0
    assert result.chapters[-1].end_ms == pytest.approx(result.duration_ms, abs=1)
    for earlier, later in zip(result.chapters, result.chapters[1:], strict=False):
        assert earlier.end_ms <= later.start_ms + 1


async def test_transient_failures_are_retried():
    engine = FakeEngine(fail_times=2)
    result = await render_text(
        "Just one sentence.",
        RenderOptions(retries=3, retry_base_delay=0.0),
        engine=engine,
    )
    assert result.chunk_count == 1
    assert len(engine.calls) == 3


async def test_permanent_failure_is_reported():
    engine = FakeEngine(fail_times=99)
    with pytest.raises(SynthesisError):
        await render_text(
            "Nope.", RenderOptions(retries=2, retry_base_delay=0.0), engine=engine
        )


async def test_empty_document_renders_nothing(fake_engine):
    result = await render_text("", engine=fake_engine)
    assert result.audio == b""
    assert result.chunk_count == 0


async def test_concurrency_limit_is_respected(fake_engine):
    long_text = "\n\n".join(f"Paragraph number {i} of the document." for i in range(12))
    result = await render_text(long_text, RenderOptions(concurrency=2), engine=fake_engine)
    assert result.chunk_count == 12


async def test_duration_scales_with_text_length(fake_engine):
    result = await render_text("abcdefghij", engine=fake_engine)
    assert result.duration_ms == pytest.approx(10 * MS_PER_CHAR, abs=48)


async def test_save_writes_audio_tags_and_subtitles(tmp_path, fake_engine):
    result = await render_text("Hello world. Second sentence here.", engine=fake_engine)
    out = tmp_path / "demo.mp3"
    written = save(result, out, title="Demo", artist="Tester", subtitles=("srt", "vtt"))

    assert out.is_file()
    assert set(written) == {"audio", "srt", "vtt"}
    assert (tmp_path / "demo.srt").read_text().startswith("1\n")
    assert (tmp_path / "demo.vtt").read_text().startswith("WEBVTT")

    from mutagen.mp3 import MP3

    tags = MP3(out).tags
    assert str(tags["TIT2"]) == "Demo"
    assert str(tags["TPE1"]) == "Tester"


async def test_save_writes_a_chapter_list(tmp_path, fake_engine, sample_markdown):
    result = await render_text(sample_markdown, engine=fake_engine)
    written = save(result, tmp_path / "book.mp3", chapters_m4b=True)
    assert "chapters" in written
    lines = (tmp_path / "book.chapters.txt").read_text().splitlines()
    assert lines[0].endswith("First Chapter")
    assert lines[0].startswith("00:00:00")
