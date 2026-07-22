"""Sentence-level helpers for the split-first pipeline (pure functions, no I/O).

`split_sentences` feeds both the TTS chunker and Pass 1's sentence index.
`window_sentences` breaks a long script's numbered sentences into windows so no
single Pass 1 LLM call has to group a whole 100+ sentence script at once.
"""
import re

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_BOUNDARY.split(str(text)) if s]


def split_for_tts(text: str, max_chars: int) -> list[str]:
    """Mechanical sentence-boundary chunker for the TTS API's input limit.
    Purely about the character cap."""
    pieces: list[str] = []
    for sentence in split_sentences(text):
        if len(sentence) <= max_chars:
            pieces.append(sentence)
            continue
        # Degenerate case: one sentence over the cap — hard-wrap on words.
        current = ""
        for word in sentence.split():
            if current and len(current) + 1 + len(word) > max_chars:
                pieces.append(current)
                current = ""
            current = f"{current} {word}" if current else word
        if current:
            pieces.append(current)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > max_chars:
            chunks.append(current)
            current = ""
        current = f"{current} {piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def window_sentences(
    sentences: list[dict], max_per_window: int
) -> list[list[dict]]:
    """Split the numbered sentence index into windows of at most
    `max_per_window` sentences, preserving each sentence's global `i`. A single
    window (short scripts) means one Pass 1 call; multiple windows are grouped
    independently and their scenes concatenated in order."""
    if max_per_window <= 0 or len(sentences) <= max_per_window:
        return [sentences]
    return [
        sentences[start : start + max_per_window]
        for start in range(0, len(sentences), max_per_window)
    ]
