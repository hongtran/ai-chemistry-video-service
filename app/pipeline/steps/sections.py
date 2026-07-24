"""Sentence-level helpers for the split-first pipeline (pure functions, no I/O).

`split_sentences` feeds both the TTS chunker and Pass 1's sentence index.
`window_sentences` breaks a long script's numbered sentences into windows so no
single Pass 1 LLM call has to group a whole 100+ sentence script at once.
"""
import re

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")

# A raw split segment under this many words isn't a sentence on its own — a
# bare numbering marker ("1.") or a short interjection ("Hi!") reads as one
# sentence together with its neighbor.
_MIN_SENTENCE_WORDS = 2


def split_sentences(text: str) -> list[str]:
    return _merge_short_sentences(
        [s for s in _SENTENCE_BOUNDARY.split(str(text)) if s]
    )


def _merge_short_sentences(sentences: list[str]) -> list[str]:
    """Fold any segment under _MIN_SENTENCE_WORDS words into a neighbor:
    forward into the next segment when one exists (a lead-in like "Hi!" or
    "1." attaches to what follows), else backward for a short trailing
    fragment with nothing left to merge into. Word-preserving — this only
    changes where the sentence boundaries fall, never the text itself."""
    merged: list[str] = []
    i = 0
    n = len(sentences)
    while i < n:
        current = sentences[i]
        while len(current.split()) < _MIN_SENTENCE_WORDS and i + 1 < n:
            i += 1
            current = f"{current} {sentences[i]}"
        merged.append(current)
        i += 1
    if len(merged) >= 2 and len(merged[-1].split()) < _MIN_SENTENCE_WORDS:
        merged[-2] = f"{merged[-2]} {merged[-1]}"
        merged.pop()
    return merged


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
