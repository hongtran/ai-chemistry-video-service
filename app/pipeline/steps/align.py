"""Python-native word alignment (no LLM, no subprocess).

Greedy sequential matcher: walks the whisper word list once, matching each
scene's caption chunks in order with a small lookahead window. Produces
absolute scene start/duration plus scene-local captionTiming.

Raises AlignmentError with a precise description of what diverged — the
orchestrator uses that message to drive one corrective re-scene-split before
failing the job.
"""
import copy
import re

# How far ahead (in transcript words) we search for the start of a chunk /
# the next expected word. Absorbs TTS hiccups and filler without letting a
# match run away from its true position.
_CHUNK_LOOKAHEAD = 12
_WORD_LOOKAHEAD = 6
_MIN_MATCH_RATIO = 0.5


class AlignmentError(Exception):
    pass


_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _num_words(n: int) -> list[str]:
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10]] + ([_ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        words = [_ONES[n // 100], "hundred"]
        return words + (_num_words(n % 100) if n % 100 else [])
    if n < 1_000_000:
        words = _num_words(n // 1000) + ["thousand"]
        return words + (_num_words(n % 1000) if n % 1000 else [])
    return [str(n)]


def _expand_token(token: str) -> list[str]:
    """Normalize one token into comparable words. Digits become their spoken
    form so Whisper's "14" matches a caption's "fourteen" (and vice versa);
    mixed tokens split ("h2o" → h two o), hyphens split ("twenty-five")."""
    out: list[str] = []
    for part in re.split(r"[-–—/]", token.lower()):
        cleaned = re.sub(r"[^a-z0-9.]", "", part)
        for run in re.findall(r"[a-z]+|\d+\.\d+|\d+", cleaned):
            if run[0].isdigit():
                if "." in run:
                    intp, frac = run.split(".", 1)
                    out += _num_words(int(intp)) + ["point"]
                    out += [_ONES[int(d)] for d in frac]
                else:
                    out += _num_words(int(run))
            else:
                out.append(run)
    return out


def _chunk_words(chunk: str) -> list[str]:
    return [n for t in chunk.split() for n in _expand_token(t)]


def _flatten_words(words: list[dict]) -> list[dict]:
    """Expand transcript words into normalized sub-words; each sub-word keeps
    its source word's timing and index (wi) for error context."""
    flat: list[dict] = []
    for wi, w in enumerate(words):
        for sub in _expand_token(w["text"]):
            flat.append({"text": sub, "start": w["start"], "end": w["end"], "wi": wi})
    return flat


def _match_chunk(
    expected: list[str], words: list[dict], pos: int
) -> tuple[float, float, int] | None:
    """Match one caption chunk starting at flattened-transcript index `pos`.
    Returns (start_time, end_time, next_pos) or None if it can't anchor.
    `words` entries are already normalized (see _flatten_words)."""
    # Anchor: find the chunk's first word within the lookahead window.
    anchor = None
    for i in range(pos, min(pos + _CHUNK_LOOKAHEAD, len(words))):
        if words[i]["text"] == expected[0]:
            anchor = i
            break
    if anchor is None:
        return None

    matched = [anchor]
    cursor = anchor + 1
    for exp in expected[1:]:
        for i in range(cursor, min(cursor + _WORD_LOOKAHEAD, len(words))):
            if words[i]["text"] == exp:
                matched.append(i)
                cursor = i + 1
                break
        # unmatched word: skip it, keep going — ratio check catches drift

    if len(matched) / len(expected) < _MIN_MATCH_RATIO:
        return None
    return words[matched[0]]["start"], words[matched[-1]]["end"], matched[-1] + 1


def align_scenes(
    scenes: list[dict], words: list[dict], total_duration: float
) -> list[dict]:
    """Returns deep-copied scenes with start/duration/captionTiming filled in."""
    if not words:
        raise AlignmentError("transcript has no words")

    flat = _flatten_words(words)
    if not flat:
        raise AlignmentError("transcript has no alignable words")

    aligned = copy.deepcopy(scenes)
    pos = 0
    scene_spans: list[list[tuple[str, float, float]]] = []

    for scene in aligned:
        scene_id = scene.get("id", "?")
        captions = scene.get("captions") or []
        if not captions:
            raise AlignmentError(
                f"scene '{scene_id}' has no captions — every scene needs its "
                "portion of the narration in 'captions'"
            )
        spans: list[tuple[str, float, float]] = []
        for chunk in captions:
            expected = _chunk_words(chunk)
            if not expected:
                continue
            result = _match_chunk(expected, flat, pos)
            if result is None:
                # Show the ORIGINAL spoken words around the failure point so
                # the re-split feedback tells the LLM exactly what to copy.
                wi = flat[min(pos, len(flat) - 1)]["wi"]
                context = " ".join(
                    w["text"] for w in words[wi : wi + _CHUNK_LOOKAHEAD]
                )
                raise AlignmentError(
                    f"scene '{scene_id}': caption chunk \"{chunk}\" does not match "
                    f'the spoken audio near "...{context}..." — captions must copy '
                    "the transcript (the words actually spoken) verbatim, in order"
                )
            start, end, pos = result
            spans.append((chunk, start, end))
        if not spans:
            raise AlignmentError(f"scene '{scene_id}': no alignable caption words")
        scene_spans.append(spans)

    # Scene boundaries: each scene starts where its first chunk starts
    # (scene 0 pinned to 0); duration runs to the next scene's start so the
    # timeline tiles the full audio with no gaps.
    starts = [spans[0][1] for spans in scene_spans]
    starts[0] = 0.0
    for i, (scene, spans) in enumerate(zip(aligned, scene_spans)):
        scene_start = starts[i]
        scene_end = starts[i + 1] if i + 1 < len(starts) else max(total_duration, spans[-1][2])
        scene["start"] = round(scene_start, 2)
        scene["duration"] = round(scene_end - scene_start, 2)
        scene["captionTiming"] = [
            {
                "text": chunk,
                "start": round(max(0.0, s - scene_start), 2),
                "end": round(max(0.0, e - scene_start), 2),
            }
            for chunk, s, e in spans
        ]
    return aligned
