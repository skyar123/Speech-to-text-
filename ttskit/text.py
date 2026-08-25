"""Turn documents into speakable chunks.

Markdown written for the eye contains a lot that should never be read aloud —
fences, URLs, badge images, table pipes, YAML front matter. This module strips
that, keeps the heading structure (which becomes chapters), and splits the
result at sentence boundaries into chunks small enough for any TTS engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MAX_CHARS = 2000

# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class Block:
    """One logical unit of prose: a heading or a paragraph."""

    kind: str  # "heading" | "paragraph"
    text: str
    level: int = 0


@dataclass
class Chunk:
    """A single synthesis request: short enough for one engine round-trip."""

    text: str
    chapter: str = ""
    chapter_index: int = 0
    is_heading: bool = False
    gap_after_ms: int = 0
    index: int = 0


@dataclass
class Document:
    title: str = ""
    chunks: list[Chunk] = field(default_factory=list)
    chapters: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(c.text) for c in self.chunks)


# --------------------------------------------------------------------------
# Markdown -> prose
# --------------------------------------------------------------------------

_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$", re.DOTALL | re.MULTILINE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG = re.compile(r"<[^>\n]{1,200}>")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_REF_LINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_AUTOLINK = re.compile(r"<(https?://[^>]+)>")
_BARE_URL = re.compile(r"https?://\S+")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_EMPHASIS = re.compile(r"(\*\*\*|\*\*|\*|___|__|_)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)
_RULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$", re.MULTILINE)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT = re.compile(r"^(=+|-+)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_BLOCKQUOTE = re.compile(r"^\s*>+\s?")
_TABLE_DIVIDER = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\]")
_INDENTED_CODE = re.compile(r"^(?: {4}|\t)\S")
_MULTI_WS = re.compile(r"[ \t]+")
_MULTI_NL = re.compile(r"\n{3,}")


def clean_markdown(text: str, *, skip_code: bool = True) -> str:
    """Strip Markdown syntax, leaving prose a narrator can read."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _FRONT_MATTER.sub("", text)
    text = _HTML_COMMENT.sub("", text)
    text = _FENCE.sub("\n\n[code block]\n\n" if not skip_code else "\n\n", text)
    text = _IMAGE.sub(lambda m: m.group(1) or "", text)
    text = _AUTOLINK.sub(lambda m: "", text)
    text = _LINK.sub(r"\1", text)
    text = _REF_LINK.sub(r"\1", text)
    text = _BARE_URL.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _STRIKE.sub(r"\1", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _FOOTNOTE_REF.sub("", text)
    text = _RULE.sub("", text)
    text = _HTML_TAG.sub("", text)
    return text


def parse_blocks(text: str, *, skip_code: bool = True) -> list[Block]:
    """Split cleaned Markdown into heading and paragraph blocks."""
    cleaned = clean_markdown(text, skip_code=skip_code)
    blocks: list[Block] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        joined = " ".join(part.strip() for part in buffer if part.strip())
        joined = _MULTI_WS.sub(" ", joined).strip()
        buffer.clear()
        if joined:
            blocks.append(Block("paragraph", joined))

    for raw in cleaned.split("\n"):
        line = raw.rstrip()

        if not line.strip():
            flush()
            continue

        if skip_code and _INDENTED_CODE.match(line) and not _LIST_ITEM.match(line):
            flush()
            continue

        heading = _HEADING.match(line)
        if heading:
            flush()
            title = heading.group(2).strip()
            if title:
                blocks.append(Block("heading", title, level=len(heading.group(1))))
            continue

        # Setext heading: the *previous* line held the title.
        if _SETEXT.match(line) and buffer:
            title = " ".join(buffer).strip()
            buffer.clear()
            if title:
                blocks.append(Block("heading", title, level=1 if "=" in line else 2))
            continue

        line = _BLOCKQUOTE.sub("", line)

        if _TABLE_DIVIDER.match(line) and "|" in line:
            continue
        if line.count("|") >= 2:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            line = ", ".join(c for c in cells if c)
            if line:
                flush()
                blocks.append(Block("paragraph", line))
            continue

        item = _LIST_ITEM.match(line)
        if item:
            flush()
            buffer.append(line[item.end() :])
            continue

        buffer.append(line)

    flush()
    return blocks


# --------------------------------------------------------------------------
# Sentence splitting
# --------------------------------------------------------------------------

# Abbreviations that essentially never end a sentence — a period after one of
# these is part of the word, whatever follows it.
_NEVER_END = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "rev", "hon",
    "fig", "no", "vol", "vs", "eg", "ie", "cf", "pp", "al", "approx",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
}

# Abbreviations that often *do* land at the end of a sentence ("...at 5 p.m.
# They left."). These split only when the next word starts a new sentence.
_AMBIGUOUS = {
    "etc", "inc", "ltd", "co", "corp", "dept", "est", "min", "max", "sec",
    "u.s", "u.k", "a.m", "p.m", "ph.d", "e.g", "i.e",
}

_SENTENCE_END = re.compile(r"([.!?…][\"'”’)\]]*)(\s+)")


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, guarding common abbreviations."""
    text = text.strip()
    if not text:
        return []

    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.end(1)
        head = text[start:end]
        last_word = re.split(r"[\s(\[]", head.strip())[-1].rstrip(".!?…\"')]").lower()

        if last_word in _NEVER_END:
            continue
        # A single capital letter before the period is an initial ("J. R. Ewing").
        if len(last_word) == 1 and last_word.isalpha():
            continue
        if last_word in _AMBIGUOUS and not _starts_sentence(text, match.end()):
            continue

        sentences.append(head.strip())
        start = match.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _starts_sentence(text: str, position: int) -> bool:
    """True if the text at *position* looks like the start of a new sentence."""
    rest = text[position:].lstrip("\"'“‘([")
    return bool(rest) and rest[0].isupper()


def _split_long(piece: str, max_chars: int) -> list[str]:
    """Break a single over-long sentence at clause, then word boundaries."""
    if len(piece) <= max_chars:
        return [piece]
    out: list[str] = []
    remaining = piece
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = max(window.rfind("; "), window.rfind(", "), window.rfind(" — "), window.rfind(": "))
        if cut < max_chars // 3:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = max_chars
        else:
            cut += 1
        out.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        out.append(remaining)
    return out


def pack_sentences(sentences: list[str], max_chars: int) -> list[str]:
    """Greedily pack sentences into chunks of at most *max_chars*."""
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        for part in _split_long(sentence, max_chars):
            if not current:
                current = part
            elif len(current) + 1 + len(part) <= max_chars:
                current = f"{current} {part}"
            else:
                chunks.append(current)
                current = part
    if current:
        chunks.append(current)
    return chunks


# --------------------------------------------------------------------------
# Document assembly
# --------------------------------------------------------------------------


def build_document(
    text: str,
    *,
    title: str = "",
    max_chars: int = DEFAULT_MAX_CHARS,
    paragraph_gap_ms: int = 500,
    heading_gap_ms: int = 900,
    speak_headings: bool = True,
    skip_code: bool = True,
) -> Document:
    """Parse *text* into an ordered list of synthesis chunks."""
    blocks = parse_blocks(text, skip_code=skip_code)
    doc = Document(title=title)
    chunks_in_chapter = 0

    def current_chapter() -> int:
        """Index of the chapter chunks belong to, creating a preamble if needed."""
        if not doc.chapters:
            doc.chapters.append(title or "Introduction")
        return len(doc.chapters) - 1

    for block in blocks:
        if block.kind == "heading":
            # Reuse the open chapter if nothing has been spoken under it yet —
            # that is the common `title == first H1` case — otherwise start one.
            if doc.chapters and chunks_in_chapter == 0:
                doc.chapters[-1] = block.text
            else:
                doc.chapters.append(block.text)
            chunks_in_chapter = 0
            if not doc.title:
                doc.title = block.text
            if speak_headings:
                doc.chunks.append(
                    Chunk(
                        text=block.text,
                        chapter=block.text,
                        chapter_index=len(doc.chapters) - 1,
                        is_heading=True,
                        gap_after_ms=heading_gap_ms,
                    )
                )
            continue

        index = current_chapter()
        pieces = pack_sentences(split_sentences(block.text), max_chars)
        for offset, piece in enumerate(pieces):
            doc.chunks.append(
                Chunk(
                    text=piece,
                    chapter=doc.chapters[index],
                    chapter_index=index,
                    # Only the last piece of a paragraph gets the full gap;
                    # mid-paragraph splits stay tight so prose keeps flowing.
                    gap_after_ms=paragraph_gap_ms if offset == len(pieces) - 1 else 120,
                )
            )
            chunks_in_chapter += 1

    for i, chunk in enumerate(doc.chunks):
        chunk.index = i
    if doc.chunks:
        doc.chunks[-1].gap_after_ms = 0
    return doc


# --------------------------------------------------------------------------
# Input loading
# --------------------------------------------------------------------------


def load_source(source: str, *, encoding: str = "utf-8") -> tuple[str, str]:
    """Load *source* (path or URL) and return ``(text, title)``."""
    if source.startswith(("http://", "https://")):
        return _load_url(source)
    if source == "-":
        import sys

        return sys.stdin.read(), "stdin"

    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {source}")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf(path), path.stem
    if suffix in {".html", ".htm"}:
        return _html_to_text(path.read_text(encoding=encoding, errors="replace")), path.stem
    return path.read_text(encoding=encoding, errors="replace"), path.stem


def _load_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("PDF input needs `pip install ttskit[docs]`") from exc

    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        extracted = page.extract_text() or ""
        # Rejoin words hyphenated across a line break, then unwrap lines.
        extracted = re.sub(r"(\w)-\n(\w)", r"\1\2", extracted)
        extracted = re.sub(r"(?<![.!?:;])\n(?![\n•\-*\d])", " ", extracted)
        pages.append(extracted.strip())
    return "\n\n".join(p for p in pages if p)


def _load_url(url: str) -> tuple[str, str]:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("URL input needs `pip install ttskit[docs]`") from exc

    response = httpx.get(url, follow_redirects=True, timeout=30.0)
    response.raise_for_status()
    return _html_to_text(response.text), url.rstrip("/").rsplit("/", 1)[-1] or "article"


def _html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("HTML input needs `pip install ttskit[docs]`") from exc

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup

    parts: list[str] = []
    for element in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote"]):
        content = element.get_text(" ", strip=True)
        if not content:
            continue
        if element.name.startswith("h") and element.name[1:].isdigit():
            parts.append("#" * int(element.name[1]) + " " + content)
        else:
            parts.append(content)
    if not parts:
        parts = [root.get_text("\n", strip=True)]
    return "\n\n".join(parts)
