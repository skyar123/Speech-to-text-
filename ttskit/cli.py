"""Command line interface.

    ttskit say "Hello"                      one-shot speech
    ttskit book notes.md -o notes.mp3       long-form audiobook
    ttskit subs script.md -o voiceover      audio + .srt/.vtt
    ttskit serve                            OpenAI-compatible API
    ttskit watch ./notes --out ./audio      narrate a drop folder
    ttskit voices --locale en-GB            browse voices
    ttskit transcribe memo.m4a --srt        speech -> text
    ttskit doctor                           check the local setup
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from . import audio as audio_mod
from .config import get_settings
from .engines import get_engine
from .pipeline import RenderOptions, render_text, save
from .text import build_document, load_source
from .voices import OPENAI_VOICES, describe, resolve


def _eprint(*args) -> None:
    print(*args, file=sys.stderr)


def _progress(done: int, total: int, label: str) -> None:
    width = 28
    filled = int(width * done / max(total, 1))
    bar = "#" * filled + "." * (width - filled)
    text = label.replace("\n", " ")[:40]
    sys.stderr.write(f"\r  [{bar}] {done}/{total}  {text:<40}")
    if done == total:
        sys.stderr.write("\n")
    sys.stderr.flush()


def _render_options(args, settings) -> RenderOptions:
    return RenderOptions(
        engine=args.engine or settings.engine,
        voice=args.voice or settings.default_voice,
        rate=args.rate or settings.rate,
        volume=args.volume or settings.volume,
        pitch=args.pitch or settings.pitch,
        boundary=getattr(args, "boundary", "sentence"),
        concurrency=getattr(args, "concurrency", None) or settings.concurrency,
        retries=settings.retries,
    )


def _subtitle_formats(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(f.strip().lower() for f in value.split(",") if f.strip())


def _play(path: str) -> None:
    """Best-effort playback with whatever the OS provides."""
    for player, flags in (
        ("ffplay", ["-autoexit", "-nodisp", "-loglevel", "quiet"]),
        ("afplay", []),
        ("mpv", ["--really-quiet"]),
        ("cvlc", ["--play-and-exit", "--intf", "dummy"]),
        ("aplay", []),
    ):
        exe = shutil.which(player)
        if exe:
            subprocess.run([exe, *flags, path], check=False)
            return
    if sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    elif sys.platform.startswith("win"):
        subprocess.run(["cmd", "/c", "start", "", path], check=False, shell=False)
    else:
        _eprint("  (no audio player found — install ffmpeg for ffplay)")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_say(args) -> int:
    settings = get_settings()
    text = args.text
    if text == "-" or not text:
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        _eprint("nothing to say (empty input)")
        return 2

    opts = _render_options(args, settings)
    out = Path(args.output or f"speech.{args.format}")
    subs = _subtitle_formats(args.subs)
    if subs:
        opts.boundary = args.boundary

    result = asyncio.run(
        render_text(
            text,
            opts,
            title=args.title or "",
            max_chars=args.max_chars or settings.max_chunk_chars,
            paragraph_gap_ms=args.gap if args.gap is not None else settings.paragraph_gap_ms,
            heading_gap_ms=settings.heading_gap_ms,
            speak_headings=not args.skip_headings,
            progress=_progress if not args.quiet and len(text) > 400 else None,
        )
    )
    written = save(
        result,
        out,
        title=args.title or out.stem,
        artist=args.artist,
        subtitles=subs,
    )
    if not args.quiet:
        _eprint(f"  wrote {written['audio']}  ({result.duration_hms}, {result.chunk_count} chunks)")
        for kind, path in written.items():
            if kind != "audio":
                _eprint(f"  wrote {path}")
    if args.play:
        _play(written["audio"])
    return 0


def cmd_book(args) -> int:
    settings = get_settings()
    text, derived_title = load_source(args.input)
    opts = _render_options(args, settings)
    subs = _subtitle_formats(args.subs)

    doc = build_document(
        text,
        title=args.title or "",
        max_chars=args.max_chars or settings.max_chunk_chars,
        paragraph_gap_ms=args.gap if args.gap is not None else settings.paragraph_gap_ms,
        heading_gap_ms=args.heading_gap
        if args.heading_gap is not None
        else settings.heading_gap_ms,
        speak_headings=not args.skip_headings,
    )
    if not doc.chunks:
        _eprint("  nothing speakable found in the input")
        return 1

    # A document's own first heading names it better than its filename does.
    title = args.title or doc.title or derived_title

    if not args.quiet:
        _eprint(
            f"  {title}: {len(doc.chunks)} chunks, {doc.char_count:,} characters, "
            f"{len(doc.chapters)} chapters"
        )

    from .pipeline import render_document

    result = asyncio.run(
        render_document(doc, opts, progress=None if args.quiet else _progress)
    )

    default_ext = "m4b" if args.m4b else args.format
    out = Path(args.output or f"{title}.{default_ext}")
    written = save(
        result,
        out,
        title=title,
        artist=args.artist,
        album=args.album or title,
        track=args.track,
        subtitles=subs,
        chapters_m4b=args.chapters,
    )

    if not args.quiet:
        rate = doc.char_count / result.elapsed_s if result.elapsed_s else 0
        _eprint(
            f"  wrote {written['audio']}  ({result.duration_hms}, "
            f"{result.elapsed_s:.1f}s at {rate:,.0f} chars/s)"
        )
        for kind, path in written.items():
            if kind != "audio":
                _eprint(f"  wrote {path}")
    if args.play:
        _play(written["audio"])
    return 0


def cmd_subs(args) -> int:
    settings = get_settings()
    if Path(args.input).is_file() or args.input.startswith(("http://", "https://")):
        text, derived = load_source(args.input)
    else:
        text, derived = args.input, "voiceover"

    opts = _render_options(args, settings)
    opts.boundary = args.boundary

    engine = get_engine(opts.engine)
    if not engine.supports_boundaries:
        _eprint(
            f"  engine '{engine.name}' does not report timings; "
            f"use --engine edge for subtitles."
        )
        return 2

    result = asyncio.run(
        render_text(
            text,
            opts,
            title=derived,
            max_chars=args.max_chars or settings.max_chunk_chars,
            engine=engine,
            progress=None if args.quiet else _progress,
        )
    )
    if not result.boundaries:
        _eprint("  engine returned no timing marks")
        return 1

    stem = args.output or derived
    out = Path(f"{stem}.{args.format}")
    formats = _subtitle_formats(args.formats) or ("srt", "vtt")
    written = save(result, out, title=derived, artist=args.artist, subtitles=formats)

    if not args.quiet:
        _eprint(f"  wrote {written['audio']}  ({result.duration_hms})")
        for kind, path in written.items():
            if kind != "audio":
                _eprint(f"  wrote {path}  ({len(result.boundaries)} {args.boundary} marks)")
    return 0


def cmd_serve(args) -> int:
    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    engine = args.engine or settings.engine
    _eprint(f"  ttskit {__version__}  engine={engine}  ffmpeg={'yes' if audio_mod.have_ffmpeg() else 'no'}")
    _eprint(f"  OpenAI base URL:  http://{host}:{port}/v1")
    _eprint(f"  playground:       http://{host}:{port}/")
    try:
        from .server import serve
    except ImportError:
        _eprint("  the server needs `pip install ttskit[server]`")
        return 1
    serve(host, port, engine_name=engine, reload=args.reload)
    return 0


def cmd_voices(args) -> int:
    settings = get_settings()
    engine_name = args.engine or settings.engine
    engine = get_engine(engine_name)

    if args.aliases:
        for alias, native in sorted(describe(engine.name).items()):
            marker = "*" if alias in OPENAI_VOICES else " "
            print(f"{marker} {alias:<14} -> {native}")
        return 0

    try:
        voices = asyncio.run(engine.list_voices())
    except Exception as exc:
        _eprint(f"  could not list voices: {exc}")
        return 1

    needle = (args.grep or "").lower()
    locale = (args.locale or "").lower()
    shown = 0
    for voice in voices:
        if locale and not voice.locale.lower().startswith(locale):
            continue
        blob = f"{voice.name} {voice.locale} {voice.gender} {voice.description}".lower()
        if needle and needle not in blob:
            continue
        print(f"{voice.name:<34} {voice.locale:<8} {voice.gender:<7} {voice.description}")
        shown += 1
    _eprint(f"  {shown} of {len(voices)} voices ({engine.name})")
    return 0


def cmd_watch(args) -> int:
    settings = get_settings()
    from .watch import watch_folder

    opts = _render_options(args, settings)

    def on_event(kind: str, message: str) -> None:
        if not args.quiet:
            _eprint(f"  [{kind}] {message}")

    count = asyncio.run(
        watch_folder(
            args.folder,
            args.out,
            opts,
            interval=args.interval,
            once=args.once,
            out_format=args.format,
            subtitles=_subtitle_formats(args.subs),
            on_event=on_event,
        )
    )
    if args.once and not args.quiet:
        _eprint(f"  converted {count} file(s)")
    return 0


def cmd_transcribe(args) -> int:
    try:
        from .stt import transcribe
    except ImportError:
        _eprint("  transcription needs `pip install ttskit[stt]`")
        return 1
    try:
        result = transcribe(args.input, language=args.language, model_size=args.model)
    except RuntimeError as exc:
        _eprint(f"  {exc}")
        return 1

    stem = args.output or str(Path(args.input).with_suffix(""))
    if args.srt or args.vtt or args.json:
        from .subtitles import write_all

        formats = tuple(
            f for f, on in (("srt", args.srt), ("vtt", args.vtt), ("json", args.json)) if on
        )
        for path in write_all(result.boundaries, stem, formats=formats):
            _eprint(f"  wrote {path}")
    if args.text or not (args.srt or args.vtt or args.json):
        out = Path(f"{stem}.txt")
        out.write_text(result.text + "\n", encoding="utf-8")
        _eprint(f"  wrote {out}  ({result.language}, {result.duration_s:.1f}s)")
    return 0


def cmd_models(args) -> int:
    from .models import KOKORO_FILES, download_kokoro, model_paths, models_present

    if args.action == "path":
        for path in model_paths():
            print(path)
        return 0
    if args.action == "status":
        for path in model_paths():
            state = "present" if path.is_file() else "missing"
            size = f"{path.stat().st_size // 1048576} MB" if path.is_file() else ""
            print(f"{state:<8} {path}  {size}")
        return 0 if models_present() else 1

    total = ", ".join(size for _url, size in KOKORO_FILES.values())
    _eprint(f"  downloading Kokoro-82M model files ({total})")
    download_kokoro(force=args.force)
    _eprint("  done — offline engine ready: ttskit say --engine kokoro \"hello\"")
    return 0


def cmd_doctor(args) -> int:
    del args
    settings = get_settings()
    print(f"ttskit {__version__}  (python {sys.version.split()[0]})")
    print(f"  default engine   : {settings.engine}")
    print(f"  default voice    : {settings.default_voice} "
          f"-> {resolve(settings.default_voice, settings.engine)}")
    ffmpeg = audio_mod.ffmpeg_path()
    print(f"  ffmpeg           : {ffmpeg or 'not found (mp3/wav still work)'}")
    print(f"  output formats   : {', '.join(sorted(audio_mod.FORMAT_ARGS)) if ffmpeg else 'mp3, wav'}")
    print(f"  cache dir        : {settings.cache_dir}")

    for label, module, extra in (
        ("server (fastapi)", "fastapi", "server"),
        ("offline (kokoro)", "kokoro_onnx", "offline"),
        ("stt (whisper)", "faster_whisper", "stt"),
        ("pdf input", "pypdf", "docs"),
        ("html input", "bs4", "docs"),
    ):
        try:
            __import__(module)
            state = "installed"
        except ImportError:
            state = f"missing (pip install 'ttskit[{extra}]')"
        print(f"  {label:<17}: {state}")

    from .models import models_present

    print(f"  kokoro weights   : {'present' if models_present() else 'not downloaded'}")

    try:
        engine = get_engine("edge")
        result = asyncio.run(engine.synthesize("Test.", voice="en-US-JennyNeural"))
        print(f"  edge round-trip  : ok ({len(result.audio):,} bytes)")
    except Exception as exc:
        print(f"  edge round-trip  : FAILED ({type(exc).__name__}: {exc})")
        return 1
    return 0


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def _add_voice_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--engine", help="edge (default, online) or kokoro (offline)")
    parser.add_argument("--voice", help="OpenAI name (nova), alias (jenny) or native name")
    parser.add_argument("--rate", help="speaking rate, e.g. +15%% or -10%%")
    parser.add_argument("--pitch", help="pitch shift, e.g. +5Hz")
    parser.add_argument("--volume", help="volume change, e.g. +10%%")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ttskit",
        description="Free text-to-speech: OpenAI-compatible server, audiobooks, subtitles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-V", "--version", action="version", version=f"ttskit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # say ------------------------------------------------------------------
    say = sub.add_parser("say", help="speak a string or stdin")
    say.add_argument("text", nargs="?", default="-", help="text, or - for stdin")
    say.add_argument("-o", "--output", help="output file (default speech.mp3)")
    say.add_argument("-f", "--format", default="mp3", help="mp3, wav, opus, aac, flac, pcm")
    say.add_argument("--play", action="store_true", help="play the result when done")
    say.add_argument("--subs", help="also write subtitles: srt, vtt, json (comma separated)")
    say.add_argument("--boundary", default="word", choices=["word", "sentence"])
    say.add_argument("--title", help="ID3 title")
    say.add_argument("--artist", default="ttskit", help="ID3 artist")
    say.add_argument("--max-chars", type=int, help="max characters per synthesis chunk")
    say.add_argument("--gap", type=int, help="paragraph gap in milliseconds")
    say.add_argument("--skip-headings", action="store_true", help="do not read headings aloud")
    _add_voice_args(say)
    say.set_defaults(func=cmd_say)

    # book -----------------------------------------------------------------
    book = sub.add_parser("book", help="convert a file or URL into an audiobook")
    book.add_argument("input", help=".md, .txt, .html, .pdf, a URL, or - for stdin")
    book.add_argument("-o", "--output", help="output file (default <title>.mp3)")
    book.add_argument("-f", "--format", default="mp3", help="mp3, wav, opus, aac, flac")
    book.add_argument("--m4b", action="store_true", help="write a chaptered .m4b (needs ffmpeg)")
    book.add_argument("--chapters", action="store_true", help="also write a chapter list file")
    book.add_argument("--subs", help="also write subtitles: srt, vtt, json")
    book.add_argument("--boundary", default="sentence", choices=["word", "sentence"])
    book.add_argument("--title", help="book title (default: filename or first heading)")
    book.add_argument("--artist", default="ttskit", help="ID3 artist")
    book.add_argument("--album", help="ID3 album")
    book.add_argument("--track", type=int, default=1, help="ID3 track number")
    book.add_argument("--max-chars", type=int, help="max characters per chunk (default 2000)")
    book.add_argument("--gap", type=int, help="paragraph gap in ms (default 500)")
    book.add_argument("--heading-gap", type=int, help="gap after a heading in ms (default 900)")
    book.add_argument("--skip-headings", action="store_true", help="do not read headings aloud")
    book.add_argument("-j", "--concurrency", type=int, help="parallel synthesis requests")
    book.add_argument("--play", action="store_true", help="play the result when done")
    _add_voice_args(book)
    book.set_defaults(func=cmd_book)

    # subs -----------------------------------------------------------------
    subs = sub.add_parser("subs", help="render a voiceover with matching .srt/.vtt")
    subs.add_argument("input", help="text, a file path, or a URL")
    subs.add_argument("-o", "--output", help="output stem (default derived from input)")
    subs.add_argument("-f", "--format", default="mp3", help="audio format")
    subs.add_argument("--formats", default="srt,vtt", help="subtitle formats: srt, vtt, json")
    subs.add_argument("--boundary", default="word", choices=["word", "sentence"])
    subs.add_argument("--artist", default="ttskit")
    subs.add_argument("--max-chars", type=int)
    _add_voice_args(subs)
    subs.set_defaults(func=cmd_subs)

    # serve ----------------------------------------------------------------
    serve = sub.add_parser("serve", help="run the OpenAI-compatible API server")
    serve.add_argument("--host", help="bind address (default 127.0.0.1)")
    serve.add_argument("--port", type=int, help="port (default 5050)")
    serve.add_argument("--engine", help="default engine for the server")
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    serve.set_defaults(func=cmd_serve)

    # voices ---------------------------------------------------------------
    voices = sub.add_parser("voices", help="list available voices")
    voices.add_argument("--engine", help="edge or kokoro")
    voices.add_argument("--locale", help="filter by locale prefix, e.g. en-GB")
    voices.add_argument("--grep", help="filter by substring")
    voices.add_argument("--aliases", action="store_true", help="show the alias table instead")
    voices.set_defaults(func=cmd_voices)

    # watch ----------------------------------------------------------------
    watch = sub.add_parser("watch", help="narrate files as they appear in a folder")
    watch.add_argument("folder", help="folder to watch")
    watch.add_argument("--out", default="audio", help="output folder (default ./audio)")
    watch.add_argument("-f", "--format", default="mp3", help="audio format")
    watch.add_argument("--subs", help="also write subtitles for each file")
    watch.add_argument("--interval", type=float, default=2.0, help="poll interval in seconds")
    watch.add_argument("--once", action="store_true", help="single sweep, then exit")
    watch.add_argument("-j", "--concurrency", type=int, help="parallel synthesis requests")
    _add_voice_args(watch)
    watch.set_defaults(func=cmd_watch)

    # transcribe -----------------------------------------------------------
    transcribe = sub.add_parser("transcribe", help="speech to text (offline Whisper)")
    transcribe.add_argument("input", help="audio file")
    transcribe.add_argument("-o", "--output", help="output stem")
    transcribe.add_argument("--model", default="base", help="tiny, base, small, medium, large-v3")
    transcribe.add_argument("--language", help="force a language code, e.g. en")
    transcribe.add_argument("--text", action="store_true", help="write a .txt transcript")
    transcribe.add_argument("--srt", action="store_true", help="write timed .srt captions")
    transcribe.add_argument("--vtt", action="store_true", help="write timed .vtt captions")
    transcribe.add_argument("--json", action="store_true", help="write raw word timings")
    transcribe.set_defaults(func=cmd_transcribe)

    # models ---------------------------------------------------------------
    models = sub.add_parser("models", help="manage offline model weights")
    models.add_argument(
        "action", nargs="?", default="download", choices=["download", "status", "path"]
    )
    models.add_argument("--force", action="store_true", help="re-download even if present")
    models.set_defaults(func=cmd_models)

    # doctor ---------------------------------------------------------------
    doctor = sub.add_parser("doctor", help="check engines, ffmpeg and optional extras")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        _eprint("\n  interrupted")
        return 130
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        _eprint(f"  error: {exc}")
        return 2
    except audio_mod.FFmpegMissing as exc:
        _eprint(f"  error: {exc}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
