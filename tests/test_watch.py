import pytest

from ttskit import watch as watch_mod
from ttskit.pipeline import RenderOptions
from ttskit.watch import Ledger, watch_folder

from .conftest import FakeEngine

pytestmark = pytest.mark.asyncio


@pytest.fixture
def offline(monkeypatch):
    """Route the watcher's synthesis through the fake engine."""
    engine = FakeEngine()
    monkeypatch.setattr("ttskit.pipeline.get_engine", lambda name=None, **kw: engine)
    return engine


async def test_a_dropped_file_is_narrated(tmp_path, offline):
    drop, out = tmp_path / "drop", tmp_path / "out"
    drop.mkdir()
    (drop / "note.md").write_text("# Note\n\nSome words to read aloud.\n")

    count = await watch_folder(drop, out, RenderOptions(), once=True, settle_s=0)

    assert count == 1
    assert (out / "note.mp3").is_file()
    assert offline.calls


async def test_unknown_extensions_are_ignored(tmp_path, offline):
    drop, out = tmp_path / "drop", tmp_path / "out"
    drop.mkdir()
    (drop / "data.json").write_text("{}")

    assert await watch_folder(drop, out, once=True, settle_s=0) == 0
    assert not (out / "data.mp3").exists()


async def test_already_converted_files_are_skipped(tmp_path, offline):
    drop, out = tmp_path / "drop", tmp_path / "out"
    drop.mkdir()
    (drop / "note.txt").write_text("First pass.")

    assert await watch_folder(drop, out, once=True, settle_s=0) == 1
    assert await watch_folder(drop, out, once=True, settle_s=0) == 0


async def test_an_edited_file_is_reconverted(tmp_path, offline):
    drop, out = tmp_path / "drop", tmp_path / "out"
    drop.mkdir()
    note = drop / "note.txt"
    note.write_text("First pass.")
    await watch_folder(drop, out, once=True, settle_s=0)

    note.write_text("A longer second pass with different content.")
    assert await watch_folder(drop, out, once=True, settle_s=0) == 1


async def test_one_bad_file_does_not_block_the_others(tmp_path, monkeypatch):
    drop, out = tmp_path / "drop", tmp_path / "out"
    drop.mkdir()
    (drop / "a-bad.md").write_text("boom")
    (drop / "b-good.md").write_text("This one is fine.")

    engine = FakeEngine()
    monkeypatch.setattr("ttskit.pipeline.get_engine", lambda name=None, **kw: engine)

    original = watch_mod.convert_one

    async def flaky(src, *args, **kwargs):
        if "bad" in src.name:
            raise RuntimeError("simulated failure")
        return await original(src, *args, **kwargs)

    monkeypatch.setattr(watch_mod, "convert_one", flaky)

    events: list[tuple[str, str]] = []
    count = await watch_folder(
        drop, out, once=True, settle_s=0, on_event=lambda k, m: events.append((k, m))
    )

    assert count == 1
    assert (out / "b-good.mp3").is_file()
    assert any(kind == "error" for kind, _ in events)


async def test_missing_folder_is_reported(tmp_path):
    with pytest.raises(NotADirectoryError):
        await watch_folder(tmp_path / "nope", tmp_path / "out", once=True)


@pytest.mark.asyncio(loop_scope="function")
async def test_ledger_survives_a_corrupt_file(tmp_path):
    tmp_path.joinpath(".ttskit-ledger.json").write_text("{not json")
    assert Ledger(tmp_path).entries == {}
