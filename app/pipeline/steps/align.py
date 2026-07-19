"""Python-native word alignment (no LLM, no subprocess).

Greedy sequential matcher: walks the whisper word list once, matching each
scene's caption chunks in order with a small lookahead window. Produces
absolute scene start/duration plus scene-local captionTiming, including a
words[] entry per chunk ({text,start,end} per whitespace token) for the
karaoke highlight.

Raises AlignmentError with a precise description of what diverged — the
orchestrator uses that message (and .scene_ids) to drive a corrective
re-scene-split before failing the job.
"""
import copy
import difflib
import re

# How far ahead (in transcript words) we search for the start of a chunk /
# the next expected word. Absorbs TTS hiccups and filler without letting a
# match run away from its true position.
_CHUNK_LOOKAHEAD = 12
_WORD_LOOKAHEAD = 6
_MIN_MATCH_RATIO = 0.5


class AlignmentError(Exception):
    def __init__(self, message: str, scene_ids: list[str] | None = None) -> None:
        super().__init__(message)
        self.scene_ids = scene_ids or []


_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

_EMPHASIS_RE = re.compile(r"\*\*(.+?)\*\*")


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


def _split_marks(text: str) -> str:
    """Re-mark multi-word **emphasis** spans word-by-word so each token
    carries balanced markers ("**a b**" -> "**a** **b**") — the frame's
    karaoke highlight needs each rendered word to be individually well-formed
    (a chip spanning multiple words would occlude anything beside it)."""
    return _EMPHASIS_RE.sub(
        lambda m: " ".join(f"**{w}**" for w in m.group(1).split()), text
    )


def _flatten_for_diff(tokens: list[str]) -> tuple[list[str], list[int]]:
    """Expand tokens to comparable sub-words, remembering which token each came
    from. Diffing must happen at sub-word granularity, not token granularity:
    Whisper emits "fine" and "tuning" as two words where a caption writes one
    hyphenated "fine-tuning", and a token-level diff would flag that identical
    speech as both a deletion and an insertion."""
    keys: list[str] = []
    owners: list[int] = []
    for i, token in enumerate(tokens):
        for sub in _expand_token(token):
            keys.append(sub)
            owners.append(i)
    return keys, owners


def _raw_span(tokens: list[str], owners: list[int], i1: int, i2: int) -> str:
    ordered: list[int] = []
    for owner in owners[i1:i2]:
        if not ordered or ordered[-1] != owner:
            ordered.append(owner)
    return " ".join(tokens[i] for i in ordered)


def _coverage_report(scenes: list[dict], words: list[dict]) -> str:
    """Name what the captions dropped or invented versus the recorded audio.

    The chunk that fails to anchor is usually NOT the mistake — when a sentence
    is silently dropped, the next chunk is perfectly good, it just lands where
    the audio hasn't got to yet. Pointing the model at that chunk sends it to
    fix something that isn't broken, so this reports the real defect instead:
    the exact words that were spoken but never captioned.
    """
    spoken_tokens = [w["text"] for w in words]
    caption_tokens = [
        token
        for scene in scenes
        for chunk in (scene.get("captions") or [])
        for token in chunk.split()
    ]
    spoken_key, spoken_owner = _flatten_for_diff(spoken_tokens)
    caption_key, caption_owner = _flatten_for_diff(caption_tokens)
    if not spoken_key or not caption_key:
        return ""

    matcher = difflib.SequenceMatcher(a=spoken_key, b=caption_key, autojunk=False)
    missing: list[str] = []
    invented: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i1 != i2:
            missing.append(_raw_span(spoken_tokens, spoken_owner, i1, i2))
        if j1 != j2:
            invented.append(_raw_span(caption_tokens, caption_owner, j1, j2))

    lines: list[str] = []
    if missing:
        listed = "; ".join(f'"{m}"' for m in missing[:5])
        lines.append(
            f"SPOKEN IN THE AUDIO BUT MISSING FROM YOUR CAPTIONS ({len(missing)} "
            f"span(s), {sum(len(m.split()) for m in missing)} word(s)): {listed}. "
            "You dropped this text — put it back, word for word, in the scene "
            "whose narration it belongs to."
        )
    if invented:
        listed = "; ".join(f'"{i}"' for i in invented[:5])
        lines.append(
            f"IN YOUR CAPTIONS BUT NEVER SPOKEN ({len(invented)} span(s)): "
            f"{listed}. Remove or correct these — they are not in the audio."
        )
    return "\n".join(lines)


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
) -> tuple[list[int | None], float, float, int] | None:
    """Match one caption chunk starting at flattened-transcript index `pos`.
    Returns (positions, start_time, end_time, next_pos) or None if it can't
    anchor. `positions` has one entry per `expected` subword: the matched
    flat-transcript index, or None if that subword wasn't found within its
    lookahead window. `words` entries are already normalized (see
    _flatten_words)."""
    anchor = None
    for i in range(pos, min(pos + _CHUNK_LOOKAHEAD, len(words))):
        if words[i]["text"] == expected[0]:
            anchor = i
            break
    if anchor is None:
        return None

    positions: list[int | None] = [anchor]
    cursor = anchor + 1
    for exp in expected[1:]:
        found = None
        for i in range(cursor, min(cursor + _WORD_LOOKAHEAD, len(words))):
            if words[i]["text"] == exp:
                found = i
                cursor = i + 1
                break
        positions.append(found)
        # unmatched word: skip it, keep going — ratio check catches drift

    matched = [p for p in positions if p is not None]
    if len(matched) / len(expected) < _MIN_MATCH_RATIO:
        return None
    return positions, words[matched[0]]["start"], words[matched[-1]]["end"], matched[-1] + 1


def _interpolate_missing(tokens: list[dict]) -> None:
    """Fill start/end for tokens whose subwords never matched (punctuation-
    only tokens, or interior words the transcript search skipped) by
    borrowing the nearest matched neighbor's boundary — a zero-width slot
    between the two nearest known timings, or pinned to whichever single
    neighbor exists at a scene edge. Mutates `tokens` in place."""
    n = len(tokens)
    for i in range(n):
        if tokens[i]["start"] is not None:
            continue
        prev = next((tokens[j] for j in range(i - 1, -1, -1) if tokens[j]["start"] is not None), None)
        nxt = next((tokens[j] for j in range(i + 1, n) if tokens[j]["start"] is not None), None)
        if prev is not None and nxt is not None:
            mid = (prev["end"] + nxt["start"]) / 2
            tokens[i]["start"] = tokens[i]["end"] = mid
        elif prev is not None:
            tokens[i]["start"] = tokens[i]["end"] = prev["end"]
        elif nxt is not None:
            tokens[i]["start"] = tokens[i]["end"] = nxt["start"]
        else:
            tokens[i]["start"] = tokens[i]["end"] = 0.0


def align_scenes(
    scenes: list[dict], words: list[dict], total_duration: float
) -> list[dict]:
    """Returns deep-copied scenes with start/duration/captionTiming (incl.
    per-word timing for karaoke) filled in."""
    if not words:
        raise AlignmentError("transcript has no words")

    flat = _flatten_words(words)
    if not flat:
        raise AlignmentError("transcript has no alignable words")

    aligned = copy.deepcopy(scenes)
    pos = 0
    # Per scene: list of (chunk_text, chunk_start_abs, chunk_end_abs, token_stream)
    scene_spans: list[list[tuple[str, float, float, list[dict]]]] = []
    print(f"flat : {flat}")
    for scene_index, scene in enumerate(aligned):
        scene_id = scene.get("id", "?")
        captions = scene.get("captions") or []
        if not captions:
            raise AlignmentError(
                f"scene '{scene_id}' has no captions — every scene needs its "
                "portion of the narration in 'captions'",
                scene_ids=[scene_id],
            )
        spans: list[tuple[str, float, float, list[dict]]] = []
        scene_token_stream: list[dict] = []
        print(f"scene : {scene_id} : {captions}")
        for chunk in captions:
            marked = _split_marks(chunk)
            tokens = marked.split()
            expected_per_token = [_expand_token(t) for t in tokens]
            expected = [w for sub in expected_per_token for w in sub]
            if not expected:
                continue
            print(f"chunk : {chunk} : {expected}")
            result = _match_chunk(expected, flat, pos)
            if result is None:
                # Show the ORIGINAL spoken words around the failure point so
                # the re-split feedback tells the LLM exactly what to copy.
                wi = flat[min(pos, len(flat) - 1)]["wi"]
                context = " ".join(
                    w["text"] for w in words[wi : wi + _CHUNK_LOOKAHEAD]
                )
                message = (
                    f"scene '{scene_id}': caption chunk \"{chunk}\" does not match "
                    f'the spoken audio near "...{context}..." — captions must copy '
                    "the transcript (the words actually spoken) verbatim, in order"
                )
                report = _coverage_report(aligned, words)
                if report:
                    message += "\n" + report
                # Dropped text sits on a boundary: it belongs either at the end
                # of the previous scene or the start of this one, so blame both
                # — otherwise a long-form retry can re-split a section that had
                # nothing to do with the omission.
                blamed = [scene_id]
                if scene_index > 0:
                    previous = aligned[scene_index - 1].get("id")
                    if previous:
                        blamed.insert(0, previous)
                raise AlignmentError(message, scene_ids=blamed)
            positions, start, end, pos = result

            chunk_tokens: list[dict] = []
            offset = 0
            for tok_text, sub in zip(tokens, expected_per_token):
                n = len(sub)
                tok_positions = [p for p in positions[offset : offset + n] if p is not None]
                offset += n
                if tok_positions:
                    t_start = min(flat[p]["start"] for p in tok_positions)
                    t_end = max(flat[p]["end"] for p in tok_positions)
                else:
                    t_start = t_end = None
                token_entry = {"text": tok_text, "start": t_start, "end": t_end}
                chunk_tokens.append(token_entry)
                scene_token_stream.append(token_entry)

            spans.append((chunk, start, end, chunk_tokens))
        if not spans:
            raise AlignmentError(
                f"scene '{scene_id}': no alignable caption words", scene_ids=[scene_id]
            )
        _interpolate_missing(scene_token_stream)
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
                "words": [
                    {
                        "text": tok["text"],
                        "start": round(max(0.0, tok["start"] - scene_start), 2),
                        "end": round(max(0.0, tok["end"] - scene_start), 2),
                    }
                    for tok in chunk_tokens
                ],
            }
            for chunk, s, e, chunk_tokens in spans
        ]
    return aligned
