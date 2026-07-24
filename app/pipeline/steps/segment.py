"""Pass 1 — semantic segmentation of the script (split-first pipeline).

The LLM does ONE thing: group the script's numbered sentences into ordered
scenes at semantic boundaries. Everything that must be correct is done in code,
never by re-prompting the model:

- `coerce_partition` turns the model's (possibly messy) grouping into a clean
  contiguous partition of [1..N] — it trusts only each scene's start sentence.
- `derive_captions` chunks each scene's exact sentence text into caption
  strings sized for the video's orientation (short vs. long-form), so
  `join(captions) == join(sentences) == script` holds by construction.

The model additionally writes the video-level YouTube metadata (it sees the
whole script here, before TTS). Long scripts are grouped one sentence-window at
a time so no single call has to partition a 100+ sentence script.
"""
import json
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import Settings
from app.languages import DEFAULT_LANGUAGE
from app.llm.client import with_retries
from app.pipeline.steps import scene_split
from app.pipeline.steps.sections import split_sentences, window_sentences
from app.subjects import SubjectConfig

logger = logging.getLogger(__name__)

# Caption chunk shape (schema says 2-6 words, ≤55 chars each) — short/vertical.
_CAPTION_MIN_WORDS = 2
_CAPTION_MAX_WORDS = 5
_CAPTION_MAX_CHARS = 55

# Long/horizontal caption chunk shape: fewer, denser scenes read better as
# slightly longer 3-7 word captions (wider char cap so 7 longer words don't
# hit it before the word-count target).
_CAPTION_MIN_WORDS_LONG = 3
_CAPTION_MAX_WORDS_LONG = 7
_CAPTION_MAX_CHARS_LONG = 75

# Punctuation a chunk prefers to end on, once it already has min_words.
_CAPTION_BREAK_CHARS = (":", ",", ";")

_METADATA_BLOCK = (
    'ALSO include a top-level "config" object alongside "scenes", carrying the '
    "video's YouTube metadata written from the script:\n"
    '- "description": 2-4 sentence YouTube description summarizing the topic for '
    'a general audience, plain prose (no hashtags in this field).\n'
    '- "hashtags": 3-6 short lowercase words/phrases, no "#" and no spaces '
    '(e.g. "ai", "llmagents"), most relevant first.\n'
    '- "tags": 5-10 short lowercase search-keyword phrases for YouTube\'s tags '
    'field (these may contain spaces, e.g. "ai agents").'
)


class SegmentError(scene_split.SceneSplitError):
    pass


@dataclass
class SceneIndex:
    """One scene as decided in Pass 1: which script sentences it covers and the
    code-derived caption chunks for those sentences."""

    scene_id: str
    idx_sentences: list[int]   # 1-based, global, contiguous (coerced in code)
    captions: list[str]        # derived in code from the sentence text

    def to_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "idx_sentences": self.idx_sentences,
            "captions": self.captions,
        }


def build_sentence_index(script: str) -> list[dict]:
    """Number every sentence of the script, 1-based."""
    return [
        {"i": i, "text": text}
        for i, text in enumerate(split_sentences(script), start=1)
    ]


def _normalize(text: str) -> str:
    return " ".join(str(text).split())


def _caption_bounds(orientation: str) -> tuple[int, int, int]:
    """(min_words, max_words, max_chars) for a caption chunk: short/vertical
    keeps the schema's 2-5/55 shape; long/horizontal widens to 3-7/75 so a
    long-form video's fewer, denser captions don't chop too fine."""
    if orientation == "horizontal":
        return _CAPTION_MIN_WORDS_LONG, _CAPTION_MAX_WORDS_LONG, _CAPTION_MAX_CHARS_LONG
    return _CAPTION_MIN_WORDS, _CAPTION_MAX_WORDS, _CAPTION_MAX_CHARS


def _greedy_chunk(
    words: list[str], min_words: int, max_words: int, max_chars: int,
) -> list[str]:
    """Greedily pack a word list into min_words-max_words / <=max_chars chunks,
    preferring to end a chunk right after punctuation once it already has
    min_words. Purely mechanical: adds/drops/rewords nothing, so the chunks
    joined reproduce the input word sequence exactly. Bounds are passed in by
    the caller (short vs. long-form) rather than looked up here, so this stays
    a pure, directly-testable function like `coerce_partition`. Best-effort by
    design: the trailing remainder is merged back into the previous chunk
    whenever that keeps it within max_words/max_chars; if there simply aren't
    enough words left to reach min_words and no such merge is possible, the
    short remainder is left as-is rather than treated as a hard failure — this
    is the deterministic fallback for the semantic chunker and the body of
    `derive_captions`."""
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        tentative = current + [word]
        too_long = len(" ".join(tentative)) > max_chars
        at_max = len(current) >= max_words
        char_forced = too_long and len(current) >= min_words
        if current and (at_max or char_forced):
            chunks.append(" ".join(current))
            current = [word]
            continue
        current = tentative
        if len(current) >= min_words and current[-1].endswith(_CAPTION_BREAK_CHARS):
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))

    # Trailing-remainder fix: the final chunk can come up short only because
    # the words ran out, never mid-stream (every break above already requires
    # len(current) >= min_words). Merge it back if that keeps the combined
    # chunk in bounds; otherwise leave the short remainder — there is no way
    # to manufacture words that don't exist.
    if len(chunks) >= 2 and len(chunks[-1].split()) < min_words:
        merged = chunks[-2] + " " + chunks[-1]
        chunks[-2:] = [merged]
    return chunks


def derive_captions(
    sentence_texts: list[str], orientation: str = "vertical",
) -> list[str]:
    """Chunk each sentence of the scene independently into caption strings
    sized for the video's orientation — 2-5 words / ≤55 chars for
    short/vertical, 3-7 words / ≤75 chars for long/horizontal — then
    concatenate the per-sentence chunks. A caption never straddles a sentence
    boundary: a short sentence gets its own (possibly under-minimum) caption
    rather than borrowing words from its neighbor. Re-splits on sentence
    boundaries itself (via `split_sentences`) so this holds regardless of
    whether the caller passes separate sentences or one already-joined scene
    string — `derive_captions_semantic`'s fallback does the latter. No word is
    added, dropped, or reworded, so the chunks joined reproduce the sentences
    exactly."""
    min_words, max_words, max_chars = _caption_bounds(orientation)
    chunks: list[str] = []
    for sentence in sentence_texts:
        # sentence = _normalize(sentence)
        words = sentence.split()
        if words:
            chunks.extend(_greedy_chunk(words, min_words, max_words, max_chars))
    return chunks


def coerce_partition(raw_groups: list[list[int]], n_sentences: int) -> list[list[int]]:
    """Turn the model's scene groupings into a clean contiguous partition of
    [1..N]. Trusts only each group's START sentence: sorted, de-duplicated,
    forced to begin at 1, then filled into contiguous ranges. This alone
    guarantees every sentence is covered exactly once, in order — no
    re-prompting the model."""
    starts: list[int] = []
    for group in raw_groups:
        ints = [
            int(x) for x in group
            if isinstance(x, (int, float)) and 1 <= int(x) <= n_sentences
        ]
        if ints:
            starts.append(min(ints))
    starts = sorted(set(starts))
    if not starts or starts[0] != 1:
        starts = [1] + [s for s in starts if s > 1]

    ranges: list[list[int]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] - 1 if idx + 1 < len(starts) else n_sentences
        ranges.append(list(range(start, end + 1)))
    return ranges


def _even_starts(n_sentences: int, per: int = 3) -> list[list[int]]:
    """Fallback grouping when the model returns nothing usable: roughly `per`
    sentences per scene."""
    return [[s] for s in range(1, n_sentences + 1, max(1, per))]


def _sentence_texts(sentences: list[dict], indices: list[int]) -> list[str]:
    by_i = {int(s["i"]): s["text"] for s in sentences}
    return [by_i[i] for i in indices if i in by_i]


# --- Semantic caption chunking (LLM chunks the scene paragraph; code validates) -

_CAPTION_SYSTEM_PROMPT = (
    "You split a video scene's narration into on-screen caption chunks for "
    "karaoke-style subtitles. You are given one or more SCENES; each scene is a "
    "paragraph of narration. For each scene, return an ordered list of caption "
    "strings that, read in order, reproduce the scene's text EXACTLY.\n"
    "Rules:\n"
    "1. Reproduce the words verbatim and in order — do NOT add, drop, reorder, "
    "reword, translate, or change accents/diacritics or punctuation. The chunks "
    "joined with single spaces must equal the scene's own words.\n"
    "2. Each chunk is 2–5 words and at most ~55 characters.\n"
    "3. Break only at meaningful phrase boundaries. NEVER end a chunk on a "
    "leading conjunction, preposition, or article that introduces the next "
    'phrase (e.g. Vietnamese "và", "cùng", "của", "nhưng", "để"; English "and", '
    '"with", "the", "to") — keep such a word at the START of the next chunk.\n'
    "Return ONLY a JSON object shaped exactly like:\n"
    '{"scenes": [ {"captions": ["Các biện pháp QC", "Thực nghiệm hóa học"]}, '
    '{"captions": ["..."]} ]}\n'
    "with exactly one entry per input scene, in the same order."
)


def _build_caption_user_message(scene_texts: list[str]) -> str:
    """One user message carrying each scene's paragraph. Per-request data only."""
    blocks = [f"Scene {i}:\n{t}" for i, t in enumerate(scene_texts, start=1)]
    return "SCENES:\n\n" + "\n\n".join(blocks)


def _enforce_caps(words: list[str]) -> list[str]:
    """A caption within caps passes through unchanged; an over-long one degrades
    to the greedy rule (the only place the mechanical chunker still applies).
    Word-preserving, so it never breaks validation."""
    if (
        len(words) <= _CAPTION_MAX_WORDS
        and len(" ".join(words)) <= _CAPTION_MAX_CHARS
    ):
        return [" ".join(words)]
    return _greedy_chunk(words, _CAPTION_MIN_WORDS, _CAPTION_MAX_WORDS, _CAPTION_MAX_CHARS)


def _validate_scene_captions(captions: object, scene_text: str) -> list[str] | None:
    """Accept the model's caption strings only if they reproduce the scene's
    exact word sequence (whitespace-normalized). This is the integrity guard: a
    reworded/dropped word or a changed diacritic fails here and the caller falls
    back to the greedy chunker. Valid captions get a word-preserving cap-repair
    so the ≤5-word / ≤55-char invariant always holds."""
    if not isinstance(captions, list) or not all(isinstance(c, str) for c in captions):
        return None
    if _normalize(" ".join(captions)).split() != _normalize(scene_text).split():
        return None
    out: list[str] = []
    for caption in captions:
        out.extend(_enforce_caps(_normalize(caption).split()))
    return out


async def derive_captions_semantic(
    client: AsyncOpenAI,
    settings: Settings,
    scene_sentences: list[list[str]],
    orientation: str = "vertical",
) -> list[list[str]]:
    """Chunk each scene's paragraph into caption strings at semantic boundaries
    via the LLM — ONE call for ALL scenes. `scene_sentences` is one list of
    sentence texts per scene (segment_script passes _sentence_texts' real
    output, not a pre-joined blob), so the greedy fallback below always gets
    genuine per-sentence input. Each scene's captions are validated against
    that scene's own words; any scene that fails validation (or is
    missing/garbled) falls back to the greedy chunker (bounds sized for
    `orientation`), so join(captions) always reproduces the scene text. Makes
    no LLM call in stub/disabled mode."""
    if not settings.semantic_captions_enabled:
        return [derive_captions(sents, orientation=orientation) for sents in scene_sentences]
    if not scene_sentences:
        return []

    scene_texts = [_normalize(" ".join(sents)) for sents in scene_sentences]
    messages = [
        {"role": "system", "content": _CAPTION_SYSTEM_PROMPT},
        {"role": "user", "content": _build_caption_user_message(scene_texts)},
    ]

    async def _call() -> str:
        completion = await client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=settings.llm_temperature,
        )
        return completion.choices[0].message.content or ""

    raw = await with_retries(_call, max_attempts=settings.max_retries)

    try:
        payload = json.loads(raw)
        scenes = payload["scenes"] if isinstance(payload, dict) else None
    except (json.JSONDecodeError, KeyError, TypeError):
        scenes = None
    if not isinstance(scenes, list):
        logger.warning("captions: response unusable JSON — greedy fallback")
        return [derive_captions(sents, orientation=orientation) for sents in scene_sentences]

    results: list[list[str]] = []
    for idx, (text, sents) in enumerate(zip(scene_texts, scene_sentences)):
        entry = scenes[idx] if idx < len(scenes) else None
        raw_captions = entry.get("captions") if isinstance(entry, dict) else None
        validated = _validate_scene_captions(raw_captions, text)
        results.append(
            validated if validated is not None
            else derive_captions(sents, orientation=orientation)
        )
    return results


def assert_three_way_equality(
    scenes_index: list[SceneIndex], sentences: list[dict], script: str
) -> None:
    """Code guard: join(captions) == join(sentences) == script (whitespace-
    normalized). A mismatch is a bug in the chunker, not a model problem."""
    captions_text = _normalize(
        " ".join(c for scene in scenes_index for c in scene.captions)
    )
    sentences_text = _normalize(" ".join(s["text"] for s in sentences))
    script_text = _normalize(script)
    if not (captions_text == sentences_text == script_text):
        raise SegmentError(
            "three-way equality broken: captions, sentences, and script must "
            "reproduce the same word sequence"
        )


def build_segment_system_prompt(subject_config: SubjectConfig) -> str:
    """Cache-stable per subject: the Pass 1 grouping task + the subject's own
    segmentation guidance. Contains no per-request data."""
    return "\n\n".join([
        "You segment a finished narration SCRIPT into scenes for a video. You "
        "are given the script as a NUMBERED list of sentences. Group the "
        "sentences into an ordered list of scenes, cutting only at natural "
        "semantic boundaries: each scene is one coherent idea. Every sentence "
        "belongs to exactly one scene, scenes are contiguous (no gaps, no "
        "overlap, no reordering), and the first scene starts at sentence 1.\n\n"
        "Return ONLY a JSON object shaped exactly like:\n"
        '{"scenes": [ {"idx_sentences": [1, 2]}, {"idx_sentences": [3, 4, 5]} ]}\n'
        "Each scene lists the sentence numbers it covers, in order. Do NOT "
        "write captions, scene types, ids, or any other field — only the "
        "sentence grouping. Do NOT rewrite or echo the sentence text.",
        subject_config.segment_prompt,
    ])


def build_segment_user_message(
    sentences: list[dict],
    orientation: str,
    language: str = DEFAULT_LANGUAGE,
    include_metadata: bool = True,
    window: tuple[int, int] | None = None,
) -> str:
    parts = [scene_split.canvas_line(orientation)]

    language_block = scene_split._language_block(language)
    if language_block:
        parts.append(language_block)

    if window is not None:
        index, total = window
        parts.append(
            f"This is PART {index + 1} of {total} of one longer script — group "
            "ONLY the sentences below; the other parts are handled separately."
        )

    if include_metadata:
        parts.append(_METADATA_BLOCK)

    numbered = "\n".join(f'{s["i"]}. {s["text"]}' for s in sentences)
    parts.append("NUMBERED SENTENCES:\n" + numbered)
    return "\n\n".join(parts)


async def _segment_window(
    client: AsyncOpenAI,
    settings: Settings,
    subject_config: SubjectConfig,
    sentences: list[dict],
    *,
    orientation: str,
    language: str,
    include_metadata: bool,
    window: tuple[int, int] | None,
) -> tuple[list[list[int]], dict]:
    """One Pass 1 call for one window. Returns (raw scene groups, metadata).
    The model may misbehave; correctness is enforced by coerce_partition."""
    messages = [
        {"role": "system", "content": build_segment_system_prompt(subject_config)},
        {
            "role": "user",
            "content": build_segment_user_message(
                sentences, orientation, language, include_metadata, window
            ),
        },
    ]

    async def _call() -> str:
        completion = await client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=settings.llm_temperature,
        )
        return completion.choices[0].message.content or ""

    raw = await with_retries(_call, max_attempts=settings.max_retries)

    groups: list[list[int]] = []
    metadata: dict = {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("segment: window returned unparseable JSON — falling back")
        payload = {}
    if isinstance(payload, dict):
        for scene in payload.get("scenes") or []:
            if isinstance(scene, dict):
                idx = scene.get("idx_sentences")
                if isinstance(idx, list):
                    groups.append(idx)
        if include_metadata:
            metadata = scene_split.coerce_metadata(payload.get("config"))
    return groups, metadata


async def segment_script(
    client: AsyncOpenAI,
    settings: Settings,
    subject_config: SubjectConfig,
    sentences: list[dict],
    *,
    orientation: str = "vertical",
    language: str = DEFAULT_LANGUAGE,
) -> tuple[list[SceneIndex], dict]:
    """Group the whole script into scenes (windowed for long scripts) and derive
    each scene's captions in code. Returns (scenes_index, YouTube metadata)."""
    if not sentences:
        raise SegmentError("script produced no sentences to segment")

    windows = window_sentences(sentences, settings.segment_sentence_window)
    total = len(windows)

    metadata: dict = {}
    scene_ranges: list[list[int]] = []
    for w_index, window in enumerate(windows):
        n = len(window)
        groups, window_metadata = await _segment_window(
            client, settings, subject_config, window,
            orientation=orientation, language=language,
            include_metadata=(w_index == 0),
            window=(w_index, total) if total > 1 else None,
        )
        metadata = metadata or window_metadata

        # Groups from the model are 1-based over the WINDOW's own numbering only
        # if it echoed them that way; we always trust the sentences' global `i`.
        # Map any in-window index back to the global sentence numbers, then coerce.
        global_lo = int(window[0]["i"])
        global_hi = int(window[-1]["i"])
        # Accept indices whether the model used global numbering or 1..n local.
        remapped: list[list[int]] = []
        for group in groups:
            g: list[int] = []
            for x in group:
                if not isinstance(x, (int, float)):
                    continue
                gi = int(x)
                if global_lo <= gi <= global_hi:
                    g.append(gi)              # already global
                elif 1 <= gi <= n:
                    g.append(global_lo + gi - 1)  # local → global
            if g:
                remapped.append(g)
        if not remapped:
            remapped = [[global_lo + s - 1] for s in range(1, n + 1, 3)]

        # coerce over the window's global index span
        scene_ranges.extend(_coerce_window(remapped, global_lo, global_hi))

    # Semantic caption chunking: ONE LLM call chunks every scene's paragraph at
    # semantic boundaries; code validates each scene against its own words and
    # falls back to the greedy chunker per failing scene (see
    # derive_captions_semantic), so three-way equality always holds. Each
    # scene keeps its real per-sentence list (not pre-joined) so the greedy
    # fallback never has to re-derive sentence boundaries from a flattened
    # blob.
    scene_sentences = [
        _sentence_texts(sentences, indices) for indices in scene_ranges
    ]
    captions_per_scene = await derive_captions_semantic(
        client, settings, scene_sentences, orientation=orientation,
    )

    scenes_index = [
        SceneIndex(
            scene_id=f"scene-{next_id}",
            idx_sentences=indices,
            captions=captions,
        )
        for next_id, (indices, captions) in enumerate(
            zip(scene_ranges, captions_per_scene), start=1
        )
    ]

    return scenes_index, metadata


def _coerce_window(
    raw_groups: list[list[int]], lo: int, hi: int
) -> list[list[int]]:
    """coerce_partition over an arbitrary [lo..hi] global span."""
    n = hi - lo + 1
    local = [[x - lo + 1 for x in group if lo <= x <= hi] for group in raw_groups]
    local = [g for g in local if g]
    ranges = coerce_partition(local or _even_starts(n), n)
    return [[i + lo - 1 for i in indices] for indices in ranges]
