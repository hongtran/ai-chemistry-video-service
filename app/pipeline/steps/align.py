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

from app.languages import DEFAULT_LANGUAGE

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

# Vietnamese digit names for 0-9, used to build spoken numbers. These carry
# diacritics; _fold() strips them to the same ascii skeleton the transcript
# tokens get (see _expand_token), so they compare against a Whisper transcript.
_ONES_VI = [
    "không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín",
]

# Word inserted before the fractional part of a decimal, per language.
_POINT = {"en": "point", "vi": "phẩy"}
# Digit table used for individual fractional digits ("3.14" → three one four).
_ONES_DIGIT = {"en": _ONES, "vi": _ONES_VI}
# Spoken form of a trailing "%".
_PERCENT = {"en": "percent", "vi": "phần trăm"}
# Languages that write the decimal separator as a comma and thousands as a dot
# (the opposite of English). Only these get comma→decimal-point rewriting.
_COMMA_DECIMAL = {"vi"}

_EMPHASIS_RE = re.compile(r"\*\*(.+?)\*\*")


def _num_words_en(n: int) -> list[str]:
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10]] + ([_ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        words = [_ONES[n // 100], "hundred"]
        return words + (_num_words_en(n % 100) if n % 100 else [])
    if n < 1_000_000:
        words = _num_words_en(n // 1000) + ["thousand"]
        return words + (_num_words_en(n % 1000) if n % 1000 else [])
    return [str(n)]


def _num_words_vi(n: int) -> list[str]:
    """Spoken Vietnamese for n. Applies the common speech variants (mười vs
    mươi for the tens place, mốt/lăm/tư for trailing 1/5/4) so the words match
    how Whisper transcribes the TTS. Grammar need not be perfect — alignment
    folds and only needs a decent overlap to anchor."""
    if n < 10:
        return [_ONES_VI[n]]
    if n < 20:  # 10-19: "mười" + unit, 5 → "lăm"
        u = n % 10
        if u == 0:
            return ["mười"]
        return ["mười", "lăm" if u == 5 else _ONES_VI[u]]
    if n < 100:  # tens: <tens> "mươi" [+ unit: 1→mốt, 4→tư, 5→lăm]
        t, u = divmod(n, 10)
        out = [_ONES_VI[t], "mươi"]
        if u == 1:
            out.append("mốt")
        elif u == 4:
            out.append("tư")
        elif u == 5:
            out.append("lăm")
        elif u:
            out.append(_ONES_VI[u])
        return out
    if n < 1000:  # hundreds: <h> "trăm" [+ "linh" unit | + rest]
        h, r = divmod(n, 100)
        out = [_ONES_VI[h], "trăm"]
        if r == 0:
            return out
        if r < 10:
            return out + ["linh", _ONES_VI[r]]
        return out + _num_words_vi(r)
    if n < 1_000_000:  # thousands: <t...> "nghìn" [+ rest]
        th, r = divmod(n, 1000)
        return _num_words_vi(th) + ["nghìn"] + (_num_words_vi(r) if r else [])
    return [str(n)]


_NUM_WORDS = {"en": _num_words_en, "vi": _num_words_vi}


def _num_words(n: int, language: str = DEFAULT_LANGUAGE) -> list[str]:
    """Spoken form of n in `language` (falls back to English for unknowns)."""
    return _NUM_WORDS.get(language, _num_words_en)(n)


def _fold(word: str) -> list[str]:
    """Reduce a spoken-number word to the same ascii skeleton the transcript
    tokens get (diacritics dropped, non-ascii removed), so language number
    words compare against a diacritic-stripped Whisper transcript. A word may
    fold to zero or several runs; empties are dropped by the caller."""
    cleaned = re.sub(r"[^a-z0-9.]", "", word.lower())
    return re.findall(r"[a-z]+|\d+", cleaned)


def _expand_token(token: str, language: str = DEFAULT_LANGUAGE) -> list[str]:
    """Normalize one token into comparable words. Digits become their spoken
    form in `language` so Whisper's "14" matches a caption's "fourteen" (and,
    for vi, "một" matches a caption's "1"); mixed tokens split ("h2o" → h two
    o), hyphens split ("twenty-five"); a trailing "%" becomes its spoken word.
    Spoken number words are run through _fold so they share the transcript's
    ascii skeleton."""
    token = token.lower()
    if language in _COMMA_DECIMAL:
        # vi writes decimals with a comma ("99,995" = 99.995) and thousands with
        # a dot — the opposite of English. Rewrite a comma between digits to the
        # "." the decimal branch below expects. Scribe reads the fraction
        # digit-by-digit ("chín mươi chín phẩy chín..."), which is exactly how
        # that branch expands it, so the two line up.
        token = re.sub(r"(?<=\d),(?=\d)", ".", token)
    out: list[str] = []
    for part in re.split(r"[-–—/]", token):
        cleaned = re.sub(r"[^a-z0-9.]", "", part)
        for run in re.findall(r"[a-z]+|\d+\.\d+|\d+", cleaned):
            if run[0].isdigit():
                if "." in run:
                    intp, frac = run.split(".", 1)
                    for w in _num_words(int(intp), language):
                        out += _fold(w)
                    out += _fold(_POINT.get(language, "point"))
                    ones = _ONES_DIGIT.get(language, _ONES)
                    for d in frac:
                        out += _fold(ones[int(d)])
                else:
                    for w in _num_words(int(run), language):
                        out += _fold(w)
            else:
                out.append(run)
        if "%" in part:
            # "100%" is spoken "một trăm phần trăm" / "one hundred percent" —
            # the number, then the unit word(s). Fold per word so a multi-word
            # unit ("phần trăm") stays two tokens matching the transcript.
            for w in _PERCENT.get(language, "percent").split():
                out += _fold(w)
    return out


def _split_marks(text: str) -> str:
    """Re-mark multi-word **emphasis** spans word-by-word so each token
    carries balanced markers ("**a b**" -> "**a** **b**") — the frame's
    karaoke highlight needs each rendered word to be individually well-formed
    (a chip spanning multiple words would occlude anything beside it)."""
    return _EMPHASIS_RE.sub(
        lambda m: " ".join(f"**{w}**" for w in m.group(1).split()), text
    )


def _flatten_for_diff(
    tokens: list[str], language: str = DEFAULT_LANGUAGE
) -> tuple[list[str], list[int]]:
    """Expand tokens to comparable sub-words, remembering which token each came
    from. Diffing must happen at sub-word granularity, not token granularity:
    Whisper emits "fine" and "tuning" as two words where a caption writes one
    hyphenated "fine-tuning", and a token-level diff would flag that identical
    speech as both a deletion and an insertion."""
    keys: list[str] = []
    owners: list[int] = []
    for i, token in enumerate(tokens):
        for sub in _expand_token(token, language):
            keys.append(sub)
            owners.append(i)
    return keys, owners


def _raw_span(tokens: list[str], owners: list[int], i1: int, i2: int) -> str:
    ordered: list[int] = []
    for owner in owners[i1:i2]:
        if not ordered or ordered[-1] != owner:
            ordered.append(owner)
    return " ".join(tokens[i] for i in ordered)


def _coverage_report(
    scenes: list[dict], words: list[dict], language: str = DEFAULT_LANGUAGE
) -> str:
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
    spoken_key, spoken_owner = _flatten_for_diff(spoken_tokens, language)
    caption_key, caption_owner = _flatten_for_diff(caption_tokens, language)
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


def _flatten_words(
    words: list[dict], language: str = DEFAULT_LANGUAGE
) -> list[dict]:
    """Expand transcript words into normalized sub-words; each sub-word keeps
    its source word's timing and index (wi) for error context."""
    flat: list[dict] = []
    for wi, w in enumerate(words):
        for sub in _expand_token(w["text"], language):
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
    _flatten_words).

    Anchoring tries `expected[0]` first, but falls back to the earliest later
    subword that appears within the window when the leading one doesn't (a
    normalized number like "một"/"one" that Whisper heard differently, or a
    dropped filler word). The skipped leading subwords get None positions and
    the ratio check below still rejects an anchor that skips too much."""
    anchor = None
    e_idx = 0
    for k, exp in enumerate(expected):
        for i in range(pos, min(pos + _CHUNK_LOOKAHEAD, len(words))):
            if words[i]["text"] == exp:
                anchor = i
                e_idx = k
                break
        if anchor is not None:
            break
    if anchor is None:
        return None

    positions: list[int | None] = [None] * e_idx + [anchor]
    cursor = anchor + 1
    for exp in expected[e_idx + 1:]:
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
    scenes: list[dict],
    words: list[dict],
    total_duration: float,
    language: str = DEFAULT_LANGUAGE,
) -> list[dict]:
    """Returns deep-copied scenes with start/duration/captionTiming (incl.
    per-word timing for karaoke) filled in. Best-effort: unanchored chunks are
    interpolated, never raised on. `language` selects how caption digits are
    expanded to spoken words so they match a Whisper transcript of that
    language (e.g. vi: "1" → "một")."""
    aligned = copy.deepcopy(scenes)
    flat = _flatten_words(words, language) if words else []
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
            expected_per_token = [_expand_token(t, language) for t in tokens]
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

    report = _coverage_report(aligned, words, language)
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
