"""Python-native word alignment (no LLM, no subprocess).

Greedy sequential matcher: walks the whisper word list once, matching each
scene's caption chunks in order with a small lookahead window. Produces
absolute scene start/duration plus scene-local captionTiming, including a
words[] entry per chunk ({text,start,end} per whitespace token) for the
karaoke highlight.

Best-effort by design (split-first pipeline): captions come from the clean
script while the timing signal is Whisper of the TTS, so some drift is normal.
A chunk that can't be anchored becomes a timing GAP that is interpolated from
its neighbors rather than a hard failure — alignment never raises for coverage
and never asks for a re-split. The word normalization below is unchanged; only
the failure handling is tolerant. A coverage summary is logged as a warning.
"""
import copy
import difflib
import logging
import re

logger = logging.getLogger(__name__)

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


def _interp_series(
    values: list[float | None], lo: float, hi: float
) -> list[float]:
    """Fill None entries in a monotonic time series by linear interpolation
    between the nearest known neighbours; leading/trailing Nones spread toward
    `lo`/`hi`. With no known value at all, spread evenly across [lo, hi]."""
    n = len(values)
    if n == 0:
        return []
    known = [(i, v) for i, v in enumerate(values) if v is not None]
    if not known:
        return [lo + (hi - lo) * i / n for i in range(n)]

    out: list[float] = [0.0] * n
    first_i, first_v = known[0]
    for i in range(first_i):
        out[i] = lo + (first_v - lo) * (i / first_i if first_i else 0.0)
    for k, (idx, val) in enumerate(known):
        out[idx] = val
        if k + 1 < len(known):
            nidx, nval = known[k + 1]
            gap = nidx - idx
            for j in range(1, gap):
                out[idx + j] = val + (nval - val) * j / gap
    last_i, last_v = known[-1]
    tail = n - last_i
    for i in range(last_i + 1, n):
        out[i] = last_v + (hi - last_v) * ((i - last_i) / tail if tail else 0.0)

    # Enforce non-decreasing so karaoke never runs backwards.
    for i in range(1, n):
        if out[i] < out[i - 1]:
            out[i] = out[i - 1]
    return out


def _proportional_fill(aligned: list[dict], total_duration: float) -> list[dict]:
    """No usable audio words: lay scenes end-to-end across total_duration,
    weighted by each scene's caption word count, and spread each scene's chunks
    and words evenly. Best-effort timing so the video still renders."""
    weights = [
        max(1, sum(len(c.split()) for c in (s.get("captions") or [])))
        for s in aligned
    ]
    total_w = sum(weights) or 1
    cursor = 0.0
    for scene, w in zip(aligned, weights):
        dur = total_duration * w / total_w
        start = cursor
        cursor += dur
        scene["start"] = round(start, 2)
        scene["duration"] = round(dur, 2)
        captions = scene.get("captions") or []
        n_chunks = len(captions) or 1
        timing = []
        for ci, chunk in enumerate(captions):
            c_start = dur * ci / n_chunks
            c_end = dur * (ci + 1) / n_chunks
            toks = chunk.split() or [chunk]
            timing.append({
                "text": chunk,
                "start": round(c_start, 2),
                "end": round(c_end, 2),
                "words": [
                    {
                        "text": t,
                        "start": round(c_start + (c_end - c_start) * ti / len(toks), 2),
                        "end": round(c_start + (c_end - c_start) * (ti + 1) / len(toks), 2),
                    }
                    for ti, t in enumerate(toks)
                ],
            })
        scene["captionTiming"] = timing
    return aligned


def align_scenes(
    scenes: list[dict], words: list[dict], total_duration: float
) -> list[dict]:
    """Returns deep-copied scenes with start/duration/captionTiming (incl.
    per-word timing for karaoke) filled in. Best-effort: unanchored chunks are
    interpolated, never raised on."""
    aligned = copy.deepcopy(scenes)
    flat = _flatten_words(words) if words else []
    if not flat:
        logger.warning("alignment: no usable transcript words — proportional fill")
        return _proportional_fill(aligned, total_duration)

    pos = 0
    # Per scene: list of [chunk_text, start_abs|None, end_abs|None, tokens].
    scene_spans: list[list[list]] = []
    for scene in aligned:
        captions = scene.get("captions") or []
        spans: list[list] = []
        for chunk in captions:
            marked = _split_marks(chunk)
            tokens = marked.split()
            expected_per_token = [_expand_token(t) for t in tokens]
            expected = [w for sub in expected_per_token for w in sub]
            if not expected:
                continue
            result = _match_chunk(expected, flat, pos)
            if result is None:
                # Unanchored chunk: a timing gap. Keep `pos` where it is so the
                # NEXT chunk can still anchor at its true position, and leave
                # this chunk's times None for interpolation below.
                chunk_tokens = [{"text": t, "start": None, "end": None} for t in tokens]
                spans.append([chunk, None, None, chunk_tokens])
                continue
            positions, start, end, pos = result

            chunk_tokens = []
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
                chunk_tokens.append({"text": tok_text, "start": t_start, "end": t_end})
            spans.append([chunk, start, end, chunk_tokens])
        scene_spans.append(spans)

    report = _coverage_report(aligned, words)
    if report:
        logger.warning("alignment coverage (best-effort timing applied):\n%s", report)

    # Scene starts: first anchored span start per scene, interpolated where a
    # scene had nothing anchor.
    raw_starts: list[float | None] = [
        next((s[1] for s in spans if s[1] is not None), None) for spans in scene_spans
    ]
    last_anchor = next(
        (s[2] for spans in reversed(scene_spans) for s in reversed(spans) if s[2] is not None),
        total_duration,
    )
    horizon = max(total_duration, last_anchor)
    starts = _interp_series(raw_starts, 0.0, horizon)
    starts[0] = 0.0

    for i, (scene, spans) in enumerate(zip(aligned, scene_spans)):
        scene_start = starts[i]
        scene_end = starts[i + 1] if i + 1 < len(starts) else horizon
        if scene_end < scene_start:
            scene_end = scene_start
        # Fill any gap spans' start/end across this scene, then distribute their
        # tokens; interpolate any interior token Nones from matched chunks.
        span_starts = _interp_series([s[1] for s in spans], scene_start, scene_end)
        span_ends = _interp_series([s[2] for s in spans], scene_start, scene_end)
        token_stream: list[dict] = []
        for si, span in enumerate(spans):
            if span[1] is None:
                span[1] = span_starts[si]
            if span[2] is None or span[2] < span[1]:
                span[2] = max(span_ends[si], span[1])
            toks = span[3]
            for ti, tok in enumerate(toks):
                if tok["start"] is None:
                    # spread this chunk's unmatched tokens across its span
                    frac = ti / len(toks)
                    nfrac = (ti + 1) / len(toks)
                    tok["start"] = span[1] + (span[2] - span[1]) * frac
                    tok["end"] = span[1] + (span[2] - span[1]) * nfrac
            token_stream.extend(toks)
        _interpolate_missing(token_stream)

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
