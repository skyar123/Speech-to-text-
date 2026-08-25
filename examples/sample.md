---
title: A Short Note on Free Speech Synthesis
author: ttskit
---

# A Short Note on Free Speech Synthesis

Neural text to speech used to be a paid API call. It is not any more. Two
open pipelines now cover almost every case, and both of them are free.

## The online path

Microsoft ships hundreds of neural voices behind the Edge read-aloud
endpoint. They are fast, they are natural, and they report the exact
millisecond each word begins — which is what makes real subtitles possible.

- No API key.
- Roughly four hundred voices.
- Word-level timing marks.

## The offline path

Kokoro is an eighty-two million parameter model. That is small enough to run
on a laptop CPU, and good enough that most listeners will not notice the
difference on narration. Nothing leaves the machine.

> Small models, run locally, are the quiet story of the last two years.

## Putting it together

Chunk the text at sentence boundaries. Synthesize the chunks in parallel.
Insert half a second of silence between paragraphs. Stitch, tag, and ship.

```python
# code blocks are skipped by the narrator
print("not spoken")
```

That is the whole pipeline. See [the docs](https://example.com/docs) for more.
