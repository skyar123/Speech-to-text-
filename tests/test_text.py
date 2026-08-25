from ttskit.text import (
    build_document,
    clean_markdown,
    pack_sentences,
    parse_blocks,
    split_sentences,
)


def test_clean_markdown_strips_syntax(sample_markdown):
    cleaned = clean_markdown(sample_markdown)
    assert "front matter" not in cleaned
    assert "never spoken" not in cleaned
    assert "https://example.com" not in cleaned
    assert "the docs" in cleaned
    assert "**" not in cleaned


def test_parse_blocks_finds_headings(sample_markdown):
    blocks = parse_blocks(sample_markdown)
    headings = [b.text for b in blocks if b.kind == "heading"]
    assert headings == ["First Chapter", "Second Chapter"]


def test_bullets_become_separate_paragraphs(sample_markdown):
    texts = [b.text for b in parse_blocks(sample_markdown) if b.kind == "paragraph"]
    assert "A bullet point." in texts
    assert "Another bullet point." in texts


def test_setext_heading():
    blocks = parse_blocks("Title Here\n==========\n\nBody text.\n")
    assert blocks[0].kind == "heading"
    assert blocks[0].text == "Title Here"


def test_table_rows_are_read_as_prose():
    md = "| Engine | Use |\n|---|---|\n| edge | online |\n"
    texts = [b.text for b in parse_blocks(md)]
    assert "Engine, Use" in texts
    assert "edge, online" in texts
    assert not any("---" in t for t in texts)


def test_split_sentences_respects_abbreviations():
    text = "Dr. Smith met Mr. Jones at 5 p.m. They spoke briefly. Then he left."
    sentences = split_sentences(text)
    assert len(sentences) == 3
    assert sentences[0].startswith("Dr. Smith")


def test_ambiguous_abbreviation_splits_only_before_a_new_sentence():
    assert split_sentences("Ship it, etc. Then rest.") == ["Ship it, etc.", "Then rest."]
    assert split_sentences("Acme Inc. makes widgets.") == ["Acme Inc. makes widgets."]


def test_split_sentences_handles_quotes_and_initials():
    text = 'He said "stop!" Then J. R. Ewing arrived. Nobody moved.'
    assert len(split_sentences(text)) == 3


def test_pack_sentences_respects_limit():
    sentences = ["A" * 90, "B" * 90, "C" * 90]
    chunks = pack_sentences(sentences, 200)
    assert len(chunks) == 2
    assert all(len(c) <= 200 for c in chunks)


def test_pack_sentences_splits_an_overlong_sentence():
    monster = "word " * 400  # ~2000 chars, no sentence break at all
    chunks = pack_sentences([monster.strip()], 500)
    assert len(chunks) >= 4
    assert all(len(c) <= 500 for c in chunks)


def test_build_document_assigns_chapters(sample_markdown):
    doc = build_document(sample_markdown, max_chars=200)
    assert doc.title == "First Chapter"
    assert doc.chapters == ["First Chapter", "Second Chapter"]
    assert all(0 <= c.chapter_index < len(doc.chapters) for c in doc.chunks)
    assert doc.chunks[0].is_heading


def test_preamble_before_first_heading_gets_its_own_chapter():
    doc = build_document("Opening words.\n\n# Real Heading\n\nBody.\n", title="My Doc")
    assert doc.chapters[0] == "My Doc"
    assert doc.chapters[1] == "Real Heading"


def test_last_chunk_has_no_trailing_gap(sample_markdown):
    doc = build_document(sample_markdown)
    assert doc.chunks[-1].gap_after_ms == 0


def test_headings_can_be_silenced(sample_markdown):
    doc = build_document(sample_markdown, speak_headings=False)
    assert not any(c.is_heading for c in doc.chunks)
    assert doc.chapters == ["First Chapter", "Second Chapter"]
