"""Long-form sectioning helpers (pure functions, no I/O).

A horizontal/long-form narration is far too long to scene-split in one LLM
call, so it's divided into ~200-word narrative sections that are split
independently, each in its own conversation. Scene ids carry an "s{n}-"
prefix so any downstream failure (alignment, layout gate) traces back to the
one section that owns it and only that section gets regenerated.
"""
import re

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_SECTION_ID = re.compile(r"^s(\d+)-")


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_BOUNDARY.split(str(text)) if s]


def split_for_tts(text: str, max_chars: int) -> list[str]:
    """Mechanical sentence-boundary chunker for the TTS API's input limit.
    Purely about the character cap — unrelated to section splitting below."""
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


def split_into_sections(narration: str, target_words: int = 200) -> list[str]:
    """Divides a long narration into narrative sections for per-section scene
    splitting. Word-budgeted (not char-budgeted like split_for_tts) because
    the concern here is LLM response size/coherence, not an API character cap.
    A runt tail (under half a budget) is merged into the previous section so
    the closing section is substantial enough to carry the CTA."""
    sections: list[dict] = []
    current: list[str] = []
    current_words = 0

    for sentence in split_sentences(narration):
        n = len(sentence.split())
        if current_words > 0 and current_words + n > target_words:
            sections.append({"text": " ".join(current), "words": current_words})
            current, current_words = [], 0
        current.append(sentence)
        current_words += n
    if current:
        sections.append({"text": " ".join(current), "words": current_words})

    if len(sections) > 1 and sections[-1]["words"] < target_words / 2:
        tail = sections.pop()
        sections[-1]["text"] += " " + tail["text"]

    return [s["text"] for s in sections]


def section_index_from_scene_id(scene_id: str) -> int:
    """Section index encoded in a scene id by the "s{n}-" prefix applied at
    split time. Unprefixed ids (the single-section short path) are section 0."""
    match = _SECTION_ID.match(str(scene_id))
    return int(match.group(1)) if match else 0


def build_arc_rule(index: int, total: int) -> str:
    """Arc + pacing rule for the split prompt, by section position. The
    single-section (short) path keeps the original quick-cut 6-9-scene arc.
    Long-form sections carry no numeric scene count — pacing guidance lets the
    LLM decide boundaries from the content, holding scenes much longer than a
    short would."""
    if total == 1:
        return (
            "- 6-9 scenes, contiguous narrative arc: hook → core concept → "
            "supporting detail(s) → a comparison or concrete result → closing "
            "takeaway/CTA."
        )
    pacing = (
        "This is a LONG-FORM video: let the content decide scene boundaries. "
        "Hold each scene for one full idea — roughly 20-45 seconds of narration "
        "(about 50-120 words) per scene — and change scenes only when the "
        "narration moves to a new concept. Fewer, longer scenes are strongly "
        "preferred over the quick cuts of a short."
    )
    if index == 0:
        return (
            f"- {pacing} This is the OPENING part: start with a hook/cover-type "
            "scene introducing the topic, then move into the core concept. Do "
            "NOT close or add a CTA — later parts continue."
        )
    if index == total - 1:
        return (
            f"- {pacing} This is the CLOSING part: continue the narrative "
            "directly (no re-introduction, no hook/cover scene) and end with a "
            "closing takeaway/CTA-type scene."
        )
    return (
        f"- {pacing} This is a MIDDLE part: continue the narrative directly — "
        "core concept → supporting detail(s) → a comparison or concrete "
        "example. No hook/cover scene, no closing/CTA scene."
    )
