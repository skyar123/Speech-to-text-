"""OpenAI-compatible speech server.

Point any client that speaks the OpenAI audio API at
``http://127.0.0.1:5050/v1`` with any API key and it works — Open WebUI,
SillyTavern, LibreChat, the official SDKs, a plain ``curl``. Nothing leaves
your machine except the Edge synthesis request itself (and with
``--engine kokoro``, not even that).

Endpoints
---------
``POST /v1/audio/speech``          OpenAI text-to-speech, streaming MP3
``POST /v1/audio/transcriptions``  OpenAI speech-to-text (needs ``ttskit[stt]``)
``GET  /v1/models``                model list, for clients that probe it
``GET  /v1/voices``                voice catalogue (ttskit extension)
``GET  /healthz``                  liveness + engine/ffmpeg status
``GET  /``                         a small browser playground
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from . import audio as audio_mod
from .config import get_settings
from .engines import Engine, get_engine
from .pipeline import RenderOptions, render_text
from .voices import OPENAI_VOICES, resolve

logger = logging.getLogger("ttskit.server")

STATIC_DIR = Path(__file__).parent / "static"

#: Model ids advertised on /v1/models. They all map to the same backend; the
#: names exist so OpenAI clients with hard-coded model pickers work unchanged.
MODEL_IDS = ("tts-1", "tts-1-hd", "gpt-4o-mini-tts", "ttskit")

#: Inputs longer than this go through the chunking pipeline instead of one
#: engine call, which keeps paragraph pacing and avoids engine length limits.
PIPELINE_THRESHOLD = 1800


class SpeechRequest(BaseModel):
    """The OpenAI ``/v1/audio/speech`` body, plus a few optional extensions."""

    model: str = "tts-1"
    input: str = ""
    voice: str = "alloy"
    response_format: str = "mp3"
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    instructions: str | None = None  # accepted and ignored, like a no-op style hint

    # ttskit extensions — safe to omit
    engine: str | None = None
    rate: str | None = None
    pitch: str | None = None
    volume: str | None = None
    stream: bool | None = None


def _error(status: int, message: str, *, kind: str = "invalid_request_error",
           param: str | None = None, code: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": kind, "param": param, "code": code}},
    )


def speed_to_rate(speed: float) -> str:
    """Convert OpenAI's multiplier into the percentage string engines expect."""
    percent = int(round((float(speed) - 1.0) * 100))
    return f"{percent:+d}%"


def create_app(
    *,
    engine_name: str | None = None,
    api_key: str | None = None,
) -> FastAPI:
    settings = get_settings()
    engine_name = engine_name or settings.engine
    required_key = api_key if api_key is not None else settings.api_key

    app = FastAPI(
        title="ttskit",
        version=__version__,
        description="OpenAI-compatible text-to-speech, running on free local/edge voices.",
    )
    app.state.engines = {}
    app.state.default_engine = engine_name
    app.state.settings = settings

    @app.exception_handler(HTTPException)
    async def _openai_error(request: Request, exc: HTTPException):
        del request
        kind = "invalid_request_error" if exc.status_code < 500 else "api_error"
        return _error(exc.status_code, str(exc.detail), kind=kind)

    def engine_for(name: str | None) -> Engine:
        key = (name or app.state.default_engine or "edge").lower()
        if key not in app.state.engines:
            app.state.engines[key] = get_engine(key)
        return app.state.engines[key]

    def check_auth(authorization: str | None) -> None:
        if not required_key:
            return
        token = (authorization or "").removeprefix("Bearer ").strip()
        if token != required_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    # ---------------------------------------------------------------- speech
    @app.post("/v1/audio/speech")
    @app.post("/audio/speech")  # some clients drop the /v1 prefix
    async def create_speech(
        body: SpeechRequest,
        authorization: str | None = Header(default=None),
    ):
        check_auth(authorization)

        text = (body.input or "").strip()
        if not text:
            return _error(400, "'input' must be a non-empty string", param="input")

        fmt = (body.response_format or "mp3").lower()
        if fmt not in audio_mod.FORMAT_ARGS:
            return _error(
                400,
                f"'{fmt}' is not a supported response_format. "
                f"Use one of: {', '.join(sorted(audio_mod.FORMAT_ARGS))}.",
                param="response_format",
            )

        try:
            engine = engine_for(body.engine)
        except ValueError as exc:
            return _error(400, str(exc), param="engine")

        voice = resolve(body.voice, engine.name, default=settings.default_voice)
        rate = body.rate or speed_to_rate(body.speed)
        pitch = body.pitch or settings.pitch
        volume = body.volume or settings.volume
        media_type = audio_mod.MIME_TYPES.get(fmt, "application/octet-stream")

        native = engine.native_format
        wants_stream = body.stream if body.stream is not None else True
        can_stream = wants_stream and fmt == native and len(text) <= PIPELINE_THRESHOLD

        if can_stream:
            async def body_iter():
                try:
                    async for piece in engine.stream(
                        text, voice=voice, rate=rate, volume=volume, pitch=pitch
                    ):
                        yield piece
                except Exception:
                    # Headers are already sent, so the only honest thing left
                    # is to end the stream; the client sees a short body.
                    logger.exception("streaming synthesis failed for voice %s", voice)
                    return

            return StreamingResponse(
                body_iter(),
                media_type=media_type,
                headers={
                    "X-TTSKit-Engine": engine.name,
                    "X-TTSKit-Voice": voice,
                    "Cache-Control": "no-store",
                },
            )

        try:
            if len(text) > PIPELINE_THRESHOLD:
                result = await render_text(
                    text,
                    RenderOptions(
                        engine=engine.name,
                        voice=voice,
                        rate=rate,
                        volume=volume,
                        pitch=pitch,
                        concurrency=settings.concurrency,
                        retries=settings.retries,
                    ),
                    engine=engine,
                    max_chars=settings.max_chunk_chars,
                    paragraph_gap_ms=settings.paragraph_gap_ms,
                    heading_gap_ms=settings.heading_gap_ms,
                )
                data, src_fmt = result.audio, result.fmt
            else:
                single = await engine.synthesize(
                    text, voice=voice, rate=rate, volume=volume, pitch=pitch
                )
                data, src_fmt = single.audio, single.fmt

            if src_fmt != fmt:
                data = await asyncio.to_thread(audio_mod.convert, data, src_fmt, fmt)
        except audio_mod.FFmpegMissing as exc:
            return _error(400, str(exc), param="response_format", code="ffmpeg_missing")
        except Exception as exc:
            return _error(502, f"synthesis failed: {exc}", kind="api_error")

        return Response(
            content=data,
            media_type=media_type,
            headers={"X-TTSKit-Engine": engine.name, "X-TTSKit-Voice": voice},
        )

    # ------------------------------------------------------- transcriptions
    # Form/File routes need python-multipart; skip them rather than breaking
    # the whole app for someone who only wants text-to-speech.
    try:
        import python_multipart  # noqa: F401

        _multipart_ready = True
    except ImportError:  # pragma: no cover - depends on optional install
        _multipart_ready = False

    async def create_transcription(
        file: UploadFile = File(...),
        model: str = Form("whisper-1"),
        language: str | None = Form(None),
        response_format: str = Form("json"),
        authorization: str | None = Header(default=None),
    ):
        """Speech-to-text, the other direction. Requires ``ttskit[stt]``."""
        check_auth(authorization)
        del model
        try:
            from .stt import transcribe_bytes
        except ImportError:
            return _error(
                501, "Transcription needs `pip install ttskit[stt]`.", kind="api_error"
            )

        payload = await file.read()
        try:
            transcript = await asyncio.to_thread(
                transcribe_bytes, payload, file.filename or "audio.wav", language
            )
        except RuntimeError as exc:
            return _error(501, str(exc), kind="api_error")
        except Exception as exc:
            return _error(502, f"transcription failed: {exc}", kind="api_error")

        if response_format == "text":
            return Response(content=transcript.text, media_type="text/plain")
        if response_format in {"srt", "vtt"}:
            from .subtitles import to_srt, to_vtt

            render = to_srt if response_format == "srt" else to_vtt
            return Response(content=render(transcript.boundaries), media_type="text/plain")
        return JSONResponse(
            {
                "text": transcript.text,
                "language": transcript.language,
                "duration": round(transcript.duration_s, 3),
            }
        )

    if _multipart_ready:
        for path in ("/v1/audio/transcriptions", "/audio/transcriptions"):
            app.post(path)(create_transcription)

    # ------------------------------------------------------------- metadata
    @app.get("/v1/models")
    @app.get("/models")
    async def list_models():
        return {
            "object": "list",
            "data": [
                {"id": mid, "object": "model", "created": 0, "owned_by": "ttskit"}
                for mid in MODEL_IDS
            ],
        }

    @app.get("/v1/voices")
    @app.get("/voices")
    async def list_voices(engine: str | None = None, locale: str | None = None):
        try:
            backend = engine_for(engine)
        except ValueError as exc:
            return _error(400, str(exc), param="engine")
        try:
            voices = await backend.list_voices()
        except Exception as exc:
            return _error(502, f"could not list voices: {exc}", kind="api_error")
        if locale:
            needle = locale.lower()
            voices = [v for v in voices if v.locale.lower().startswith(needle)]
        return {
            "engine": backend.name,
            "openai_aliases": {name: resolve(name, backend.name) for name in OPENAI_VOICES},
            "voices": [
                {
                    "name": v.name,
                    "locale": v.locale,
                    "gender": v.gender,
                    "description": v.description,
                }
                for v in voices
            ],
        }

    @app.get("/healthz")
    @app.get("/health")
    async def healthz():
        return {
            "status": "ok",
            "version": __version__,
            "default_engine": app.state.default_engine,
            "ffmpeg": audio_mod.ffmpeg_path() or None,
            "formats": sorted(audio_mod.FORMAT_ARGS) if audio_mod.have_ffmpeg() else ["mp3", "wav"],
            "auth_required": bool(required_key),
        }

    @app.get("/", response_class=HTMLResponse)
    async def playground(request: Request) -> Any:
        del request
        index = STATIC_DIR / "index.html"
        if not index.is_file():  # pragma: no cover - packaging safety net
            return HTMLResponse("<h1>ttskit</h1><p>POST /v1/audio/speech</p>")
        return HTMLResponse(index.read_text(encoding="utf-8"))

    return app


def serve(
    host: str | None = None,
    port: int | None = None,
    *,
    engine_name: str | None = None,
    reload: bool = False,
) -> None:
    """Run the server with uvicorn."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        create_app(engine_name=engine_name),
        host=host or settings.host,
        port=port or settings.port,
        reload=reload,
        log_level="info",
    )


def get_app() -> FastAPI:
    """Factory for ``uvicorn --factory ttskit.server:get_app``."""
    return create_app()


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """Build ``ttskit.server:app`` on first access, so `uvicorn ttskit.server:app` works."""
    if name == "app":
        global _app
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
