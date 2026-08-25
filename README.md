# ttskit

Free, local-first speech tooling for the files you generate with Claude.

Drop a Markdown file in, get a chaptered, tagged audiobook out — with matching
subtitles. Or run it as a server and point any OpenAI-compatible app at
`http://localhost:5050/v1`. No API keys, no accounts, no per-character billing.

```bash
pip install -e ".[server,ffmpeg]"

ttskit say "It works."                      # → speech.mp3
ttskit book notes.md -o notes.mp3 --subs srt
ttskit serve                                # OpenAI drop-in on :5050
ttskit watch ~/claude-output --out ~/audio  # narrate files as they appear
```

---

## What's in the box

| Command | What it does |
|---|---|
| `ttskit serve` | OpenAI-compatible `/v1/audio/speech` server + browser playground |
| `ttskit book` | Markdown / PDF / HTML / URL → chaptered MP3 or M4B audiobook |
| `ttskit subs` | Voiceover plus word-accurate `.srt`, `.vtt` and JSON timings |
| `ttskit watch` | Narrates every new file dropped into a folder |
| `ttskit say` | One-shot text → audio |
| `ttskit transcribe` | Speech **to** text, offline, via Whisper |
| `ttskit voices` | Browse ~400 Edge voices or the Kokoro voice pack |
| `ttskit doctor` | Tells you what's installed and what's missing |

Two engines, one interface:

| Engine | Network | Voices | Timings | Best for |
|---|---|---|---|---|
| `edge` (default) | required | ~400, 90+ languages | word + sentence | quality, speed, subtitles |
| `kokoro` | none | 27 English | — | privacy, air-gapped, no rate limits |

---

## Install

```bash
git clone https://github.com/skyar123/speech-to-text-.git
cd speech-to-text-
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[server,ffmpeg]"
```

Extras, all optional:

| Extra | Adds |
|---|---|
| `server` | the FastAPI server and playground |
| `ffmpeg` | a bundled static ffmpeg (opus/aac/flac/m4b) — no system install needed |
| `offline` | the Kokoro engine (ONNX Runtime) |
| `stt` | offline transcription with faster-whisper |
| `docs` | PDF, HTML and URL input |
| `all` | everything |

**ffmpeg is genuinely optional.** MP3 and WAV output, paragraph gaps, chapter
offsets and stitching are all pure Python — ttskit builds silent MPEG frames
that match the surrounding stream rather than shelling out. ffmpeg is only
needed for opus/aac/flac and for chaptered `.m4b`.

Check your setup any time:

```bash
ttskit doctor
```

---

## 1. OpenAI-compatible server

```bash
ttskit serve                    # http://127.0.0.1:5050
ttskit serve --port 8080 --engine kokoro --host 0.0.0.0
```

Then point anything that speaks the OpenAI audio API at it:

```bash
curl http://localhost:5050/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","input":"Hello from my own machine.","voice":"nova"}' \
  --output hello.mp3
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:5050/v1", api_key="anything")
client.audio.speech.create(
    model="tts-1", voice="onyx", input="Verified against the official SDK."
).write_to_file("out.mp3")
```

All eleven OpenAI voice names work (`alloy`, `ash`, `ballad`, `coral`, `echo`,
`fable`, `nova`, `onyx`, `sage`, `shimmer`, `verse`) and map onto real neural
voices. So do short aliases (`jenny`, `sonia`, `ryan`) and native names
(`en-AU-NatashaNeural`, `af_heart`) — anything unrecognised is passed straight
through to the engine, so a new voice never needs a code change here.

| Endpoint | Notes |
|---|---|
| `POST /v1/audio/speech` | streams MP3 by default; `mp3, opus, aac, flac, wav, pcm` |
| `POST /v1/audio/transcriptions` | needs `ttskit[stt]`; supports `json, text, srt, vtt` |
| `GET /v1/models` | advertises `tts-1`, `tts-1-hd`, `gpt-4o-mini-tts` |
| `GET /v1/voices` | full catalogue + the alias table (ttskit extension) |
| `GET /healthz` | engine, ffmpeg path, available formats |
| `GET /` | a small browser playground |

Extra body fields, all optional: `engine`, `rate`, `pitch`, `volume`, `stream`.
`speed` (0.25–4.0) is translated to the engine's rate. Inputs longer than
~1800 characters are automatically routed through the chunking pipeline, so
paragraph pacing survives even on a single API call.

Set `TTSKIT_API_KEY` to require `Authorization: Bearer <key>`; leave it empty
(the default) and any key is accepted, which is what most local clients expect.

---

## 2. Markdown → audiobook

```bash
ttskit book article.md -o article.mp3
ttskit book book.md -o book.m4b --m4b --voice fable      # chaptered audiobook
ttskit book https://example.com/post -o post.mp3          # needs ttskit[docs]
ttskit book paper.pdf -o paper.mp3 --rate +10%
```

What it handles for you:

- **Strips what shouldn't be read**: front matter, code fences, URLs, image
  syntax, HTML tags, table pipes, footnote markers.
- **Keeps structure**: headings become chapters and are spoken; table rows are
  read as prose; list items become their own paragraphs.
- **Splits safely**: at sentence boundaries (with abbreviation handling, so
  `Dr. Smith` and `5 p.m. They left` both come out right), never mid-word, at
  most 2,000 characters per request.
- **Paces properly**: 500 ms between paragraphs, 900 ms after a heading,
  120 ms inside a split paragraph. All configurable.
- **Runs in parallel**: six concurrent requests by default, reassembled in
  order, with retries and exponential backoff on transient failures.
- **Tags the output**: ID3 title / artist / album / track, plus a chapter list
  or real M4B chapter markers.

```
  A Short Note on Free Speech Synthesis: 13 chunks, 976 characters, 5 chapters
  [############################] 13/13
  wrote sample.mp3  (0:01:26, 4.4s at 223 chars/s)
```

Useful flags: `--voice`, `--rate`, `--max-chars`, `--gap`, `--heading-gap`,
`--skip-headings`, `--concurrency`, `--chapters`, `--subs`, `--play`.

---

## 3. Subtitles from real timing marks

Edge reports the exact millisecond every word begins. ttskit shifts those marks
onto the stitched master timeline, so subtitles stay in sync across a whole
audiobook — not just a single clip.

```bash
ttskit subs script.md -o voiceover --formats srt,vtt,json
```

```
voiceover.mp3   voiceover.srt   voiceover.vtt   voiceover.json
```

```srt
1
00:00:00,100 --> 00:00:02,412
Karaoke captions generated from real

2
00:00:02,425 --> 00:00:04,375
timing marks not guesses
```

Cues break on pauses, sentence ends, a 42-character line budget and a
6-second ceiling. The JSON output is per-word (`text`, `start_ms`, `end_ms`) —
what you want for karaoke-style highlighting in a reader.

Use `--boundary sentence` for one cue per sentence instead.

---

## 4. Fully offline neural voices

```bash
pip install -e ".[offline]"
ttskit models download          # ~337 MB, once
ttskit say --engine kokoro --voice af_heart "Nothing left this machine."
ttskit serve --engine kokoro    # a completely air-gapped OpenAI endpoint
```

Kokoro-82M runs on CPU through ONNX Runtime. It doesn't report word timings,
so `ttskit subs` will tell you to use `--engine edge` for captions.

---

## 5. The drop folder

The piece that wires it to Claude. Point it at wherever your generated files
land and everything new gets narrated:

```bash
ttskit watch ~/claude-output --out ~/audio --voice nova --subs srt
ttskit watch ./notes --out ./audio --once      # one sweep, then exit
```

Files are only picked up once they've stopped changing, and a small
`.ttskit-ledger.json` in the output folder records what's been done — restart
the watcher and it won't redo the backlog. Edit a file and it's re-narrated.

---

## 6. Speech to text

The reverse direction, also free and also offline:

```bash
pip install -e ".[stt]"
ttskit transcribe memo.m4a --text --srt
```

Whisper's word timestamps feed the same subtitle writer as the TTS side, so
`.srt`/`.vtt` output is identical in shape. Available over HTTP too, at
`POST /v1/audio/transcriptions`.

---

## Configuration

Every setting has a working default. Override with environment variables or a
`.env` file — see [`.env.example`](.env.example) for the full list.

```bash
TTSKIT_ENGINE=kokoro
TTSKIT_DEFAULT_VOICE=nova
TTSKIT_PARAGRAPH_GAP_MS=650
TTSKIT_CONCURRENCY=8
TTSKIT_API_KEY=my-local-secret
```

Behind a TLS-inspecting corporate proxy, point `TTSKIT_CA_BUNDLE` (or the
standard `SSL_CERT_FILE`) at your CA bundle — edge-tts otherwise pins its own
and fails to verify.

---

## Using it as a library

```python
import asyncio
from ttskit.pipeline import RenderOptions, render_file, save

async def main():
    result, title = await render_file(
        "notes.md",
        RenderOptions(voice="nova", rate="+8%", concurrency=8),
    )
    save(result, "notes.mp3", title=title, subtitles=("srt", "vtt"))
    print(result.duration_hms, len(result.chapters), "chapters")

asyncio.run(main())
```

| Module | Responsibility |
|---|---|
| `ttskit.engines` | backend interface; `edge.py` and `kokoro.py` implement it |
| `ttskit.text` | document → cleaned, chapter-aware, sentence-split chunks |
| `ttskit.pipeline` | concurrent synthesis, stitching, timing, tagging, output |
| `ttskit.audio` | MP3 frame maths, silence, concat, transcode, ID3, M4B |
| `ttskit.subtitles` | timing marks → SRT / VTT / JSON |
| `ttskit.server` | the OpenAI-compatible API |
| `ttskit.watch` | drop-folder automation |
| `ttskit.stt` | offline transcription |

Adding a backend means one file implementing `synthesize()` and
`list_voices()` — everything downstream is engine-agnostic.

---

## Development

```bash
make dev     # install with dev extras
make test    # 85 tests, no network required
make lint
make demo    # narrate examples/sample.md
```

The suite runs against a fake engine that emits real, decodable MP3 frames, so
stitching, duration arithmetic and subtitle offsets are all exercised without a
single network call.

---

## Notes on the free tiers

`edge-tts` talks to the endpoint Microsoft Edge uses for Read Aloud. It's free
and needs no key, but it's an undocumented consumer service: be reasonable with
volume, and use `--engine kokoro` if you need a guarantee. Kokoro-82M is
Apache-2.0 and runs entirely on your hardware.

## License

MIT — see [LICENSE](LICENSE).
