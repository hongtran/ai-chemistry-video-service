"""Pass 1 — semantic segmentation of the script (split-first pipeline).

The LLM does ONE thing: group the script's numbered sentences into ordered
scenes at semantic boundaries. Everything that must be correct is done in code,
never by re-prompting the model:

- `coerce_partition` turns the model's (possibly messy) grouping into a clean
  contiguous partition of [1..N] — it trusts only each scene's start sentence.
- `derive_captions` chunks each scene's exact sentence text into 2–5 word
  caption strings, so `join(captions) == join(sentences) == script` holds by
  construction.

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

# Caption chunk shape (schema says 2-6 words, ≤55 chars each).
_CAPTION_MIN_WORDS = 2
_CAPTION_MAX_WORDS = 5
_CAPTION_MAX_CHARS = 55

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


def derive_captions(sentence_texts: list[str]) -> list[str]:
    """Chunk the scene's joined sentence text into 2–5 word caption strings
    (≤55 chars). No word is added, dropped, or reworded, so the chunks joined
    reproduce the sentences exactly."""
    words = _normalize(" ".join(sentence_texts)).split()
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        tentative = current + [word]
        too_long = len(" ".join(tentative)) > _CAPTION_MAX_CHARS
        if current and (
            len(current) >= _CAPTION_MAX_WORDS
            or (too_long and len(current) >= _CAPTION_MIN_WORDS)
        ):
            chunks.append(" ".join(current))
            current = [word]
        else:
            current = tentative
    if current:
        chunks.append(" ".join(current))
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

    scenes_index: list[SceneIndex] = []
    metadata: dict = {}
    next_id = 1
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
        window_ranges = _coerce_window(remapped, global_lo, global_hi)
        for indices in window_ranges:
            scenes_index.append(
                SceneIndex(
                    scene_id=f"scene-{next_id}",
                    idx_sentences=indices,
                    captions=derive_captions(_sentence_texts(sentences, indices)),
                )
            )
            next_id += 1

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
