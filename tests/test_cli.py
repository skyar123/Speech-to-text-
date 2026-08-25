import pytest

from ttskit.cli import build_parser, main

from .conftest import FakeEngine


@pytest.fixture
def offline(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr("ttskit.pipeline.get_engine", lambda name=None, **kw: engine)
    monkeypatch.setattr("ttskit.cli.get_engine", lambda name=None, **kw: engine)
    return engine


def test_parser_exposes_every_command():
    parser = build_parser()
    action = next(a for a in parser._actions if a.dest == "command")
    assert set(action.choices) == {
        "say", "book", "subs", "serve", "voices", "watch", "transcribe", "models", "doctor",
    }


def test_say_writes_a_file(tmp_path, offline):
    out = tmp_path / "hello.mp3"
    assert main(["say", "Hello there.", "-o", str(out), "-q"]) == 0
    assert out.is_file() and out.stat().st_size > 0


def test_say_can_emit_subtitles(tmp_path, offline):
    out = tmp_path / "hello.mp3"
    main(["say", "Hello there world.", "-o", str(out), "--subs", "srt,vtt", "-q"])
    assert (tmp_path / "hello.srt").is_file()
    assert (tmp_path / "hello.vtt").is_file()


def test_say_rejects_empty_input(tmp_path, offline, monkeypatch):
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": staticmethod(lambda: "  ")})())
    assert main(["say", "-", "-o", str(tmp_path / "x.mp3")]) == 2


def test_book_reads_a_markdown_file(tmp_path, offline, sample_markdown):
    src = tmp_path / "doc.md"
    src.write_text(sample_markdown)
    out = tmp_path / "doc.mp3"
    assert main(["book", str(src), "-o", str(out), "-q", "--chapters"]) == 0
    assert out.is_file()
    assert (tmp_path / "doc.chapters.txt").read_text().strip().endswith("Second Chapter")


def test_book_reports_an_unspeakable_file(tmp_path, offline):
    src = tmp_path / "empty.md"
    src.write_text("```\njust code\n```\n")
    assert main(["book", str(src), "-o", str(tmp_path / "x.mp3"), "-q"]) == 1


def test_missing_input_exits_cleanly(tmp_path, offline):
    assert main(["book", str(tmp_path / "nope.md"), "-q"]) == 2


def test_voices_alias_table(capsys, offline):
    assert main(["voices", "--aliases"]) == 0
    assert "alloy" in capsys.readouterr().out


def test_subs_requires_a_timing_capable_engine(monkeypatch, tmp_path):
    class NoMarks(FakeEngine):
        supports_boundaries = False

    monkeypatch.setattr("ttskit.cli.get_engine", lambda name=None, **kw: NoMarks())
    assert main(["subs", "Some text.", "-o", str(tmp_path / "vo"), "-q"]) == 2
