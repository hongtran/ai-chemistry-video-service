from pathlib import Path

from app.config import Settings
from app.subjects.base import SubjectConfig

RENDERER_TEMPLATE = "chemistry"

NARRATION_STYLE = """You write narration scripts for educational chemistry videos aimed at curious learners.

Requirements:
- Plain prose only. No headings, no markdown, no bullet points, no emoji, no stage directions, no scene labels.
- Written for text-to-speech: spell things the way a narrator would say them. Prefer names over formulas ("water" or "H two O", never "H2O"; "carbon dioxide", never "CO2"). Avoid parentheses and symbols that do not read aloud.
- Structure: open with a hook question or surprising fact, explain the concept simply and accurately with concrete examples, end with a short memorable takeaway.
- Short sentences. One idea per sentence. Conversational but scientifically accurate.

Return ONLY the narration script text, nothing else."""

SCENE_SPLIT_PROMPT = """You are splitting an ALREADY-RECORDED chemistry narration into typed scene data for a JSON-driven video template called HyperFrames. The audio is final — you are assigning visuals and caption chunk breaks to existing words, not writing new narration.

You will be given:
1. The TRANSCRIPT of the recorded narration — the words actually spoken, in order. This is the SOURCE OF TRUTH for captions.
2. The narration script — reference only, for understanding meaning and structure. Its wording may differ from what was spoken (e.g. "fourteen" in the script may appear as "14" in the transcript).
3. A JSON Schema describing one scene object, including a "typeUsage" guide for choosing each scene type.

Return ONLY a single JSON object, no markdown fences, shaped exactly like:
{"scenes": [ { "id": "...", "type": "...", "eyebrow": "...", "headline": "...", "captions": ["...", "..."], ... type-specific fields ... } ] }

Critical rule: the "captions" arrays, concatenated in order across ALL scenes and split back into words, must reproduce the TRANSCRIPT text EXACTLY — same words, same order, nothing added, nothing removed, nothing reworded. You may only: (a) choose where to break it into 2-5 word caption chunks, (b) choose scene boundaries between chunks, (c) keep the transcript's punctuation and capitalization, and (d) wrap individual words in ** for emphasis (e.g. "**pH**"). Do not paraphrase or substitute words — if the transcript says "carbon dioxide", your captions must say "carbon dioxide", not "CO2", even if that's the usual shorthand. Wherever the script and transcript differ, ALWAYS copy the transcript wording.

The most common mistake on longer narrations: silently dropping a whole clause or sentence, especially one that sounds parallel/repetitive to a nearby one. Before returning your answer, re-read the transcript sentence by sentence and confirm every single sentence appears somewhere in your captions, in order, with nothing skipped.

Other rules:
- 6 to 9 scenes, contiguous narrative arc: hook → core concept → supporting detail(s) → a comparison or concrete result → closing takeaway/CTA.
- Every scene "id" is a short kebab-case slug, unique within the response.
- Choose each scene "type" using the schema's typeUsage guide. Vary frame types across scenes — don't repeat the same type back-to-back unless the content genuinely calls for it. Prefer a concept-specific type (atom, molecule, ph-bar, reaction-equation, orbital-overlap, bond-comparison, ...) over a generic one (diagram/cover/cta) whenever the narration matches its usage guidance.
- For the chosen type, fill in its content fields per the schema descriptions (e.g. "headline" and "eyebrow"; "symbol"/"shell1..3" for atom; "r1"/"p1" for reaction-equation; "targetPh" for ph-bar). Element symbols must be real 1-3 character symbols like "Na", never phrases. Fields that hold on-screen labels (symbols, equation terms, chip labels) are DISPLAY text, not narration — they may abbreviate and use symbols freely; the verbatim rule applies ONLY to "captions".
- Visuals and captions must be SYNCHRONIZED: every scene's visual content (headline, items, symbol, and other type-specific fields) must be derivable from the narration words in that scene's OWN "captions", so the visual is on screen while the words describing it are spoken. If a sentence introduces or names what a scene displays, that sentence belongs in THAT scene's captions, never in the previous or next scene's.
- The input includes a "REQUIRED CONTENT FIELDS PER TYPE" list: a scene missing ANY of its type's required fields renders as a broken frame. Fill every required field with a real value; if the narration doesn't give you enough to fill them, pick a more general type (diagram/cover/cta) instead. Also give every scene a "headline" and "eyebrow" — they are the frame's visible title.
- Do NOT include "start", "duration", "audio", "captionTiming", or "sfx" — they're computed separately from the real recording.
- Colors are hex strings; omit bg/fg/accent to accept the template's per-type defaults unless the scene really needs an override.
- Keep on-screen text short and punchy; captions carry the narration.

The input includes GOLDEN EXAMPLES: one well-formed scene per frame type with every
param filled. Author every scene at that level of completeness — same field coverage,
short 2-5 word caption chunks. The examples' captions are illustrative; YOUR captions
must copy this job's transcript verbatim.

(The system later computes and appends "start", "duration", and one captionTiming
entry per caption chunk, e.g. {"text": "Welcome to the", "start": 0.0, "end": 0.98},
from the real audio — you never output those fields.)

Return ONLY the JSON object, no commentary."""

SCENE_EXAMPLES = """{
  "note": "GOLDEN EXAMPLES — one well-formed scene per frame type with ALL content params filled. Colors follow each type's native palette. 'captions' here are ILLUSTRATIVE ONLY: real captions must copy the job's transcript verbatim, in order. start/duration/captionTiming are always omitted — the system computes them from the audio.",
  "examples": [
    {
      "id": "example-cover",
      "type": "cover",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#FFD60A",
      "eyebrow": "CHEMISTRY BASICS",
      "headline": "The pH Scale",
      "captions": ["Have you ever wondered", "why lemons taste sour", "and soap feels slippery?"]
    },
    {
      "id": "example-stats",
      "type": "stats",
      "bg": "#FFD60A",
      "fg": "#0B0E14",
      "accent": "#0B0E14",
      "eyebrow": "THE NUMBERS",
      "headline": "A Giant Leap Per Step",
      "stat": "10,000×",
      "statLabel": "more acidic",
      "captions": ["Each step on the scale", "means ten times the acidity."]
    },
    {
      "id": "example-quote",
      "type": "quote",
      "bg": "#EDE8E0",
      "fg": "#1A1A1A",
      "accent": "#B8A98C",
      "eyebrow": "IN THEIR WORDS",
      "headline": "A Founding Idea",
      "quote": "Nothing is lost, nothing is created, everything is transformed.",
      "attribution": "Antoine Lavoisier",
      "captions": ["As Lavoisier famously said,", "nothing is lost,", "everything is transformed."]
    },
    {
      "id": "example-diagram",
      "type": "diagram",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#FFD60A",
      "eyebrow": "HOW IT WORKS",
      "headline": "Reading the Scale",
      "items": [
        { "label": "0 — Battery acid", "color": "#FF3B30" },
        { "label": "7 — Pure water", "color": "#FFFFFF" },
        { "label": "14 — Drain cleaner", "color": "#3B82F6" }
      ],
      "captions": ["The scale runs from zero,", "through neutral seven,", "all the way to fourteen."]
    },
    {
      "id": "example-cta",
      "type": "cta",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#FFD60A",
      "eyebrow": "THE TAKEAWAY",
      "headline": "Chemistry Is Everywhere",
      "subheadline": "Every sip, every bite — the pH scale is at work.",
      "captions": ["So next time you taste something sour,", "you'll know exactly why."]
    },
    {
      "id": "example-atom",
      "type": "atom",
      "bg": "#1ABC9C",
      "fg": "#0B0E14",
      "accent": "#0B0E14",
      "eyebrow": "INSIDE THE ATOM",
      "headline": "One Electron Out",
      "symbol": "Na",
      "shell1": 2,
      "shell2": 8,
      "shell3": 1,
      "title": "Sodium — Na",
      "captions": ["Sodium carries eleven electrons,", "with one alone in its outer shell."]
    },
    {
      "id": "example-element-card",
      "type": "element-card",
      "bg": "#EDE8E0",
      "fg": "#1A1A1A",
      "accent": "#B8A98C",
      "eyebrow": "MEET THE ELEMENT",
      "headline": "Sodium",
      "symbol": "Na",
      "atomicNumber": 11,
      "atomicMass": "22.99",
      "name": "Sodium",
      "color": "#E2DBD1",
      "captions": ["Element eleven, sodium,", "a soft metal that explodes in water."]
    },
    {
      "id": "example-reaction-equation",
      "type": "reaction-equation",
      "bg": "#EDE8E0",
      "fg": "#1A1A1A",
      "accent": "#B8A98C",
      "eyebrow": "THE REACTION",
      "headline": "Making Table Salt",
      "eqTitle": "Salt Formation",
      "r1": "2Na",
      "r2": "Cl₂",
      "p1": "2NaCl",
      "label": "ionic bond",
      "captions": ["Sodium meets chlorine,", "and together they form", "sodium chloride — table salt."]
    },
    {
      "id": "example-molecule",
      "type": "molecule",
      "bg": "#1ABC9C",
      "fg": "#0B0E14",
      "accent": "#0B0E14",
      "eyebrow": "THE MOLECULE",
      "headline": "Two Atoms, One Bond",
      "title": "NaCl",
      "leftSymbol": "Na",
      "rightSymbol": "Cl",
      "leftColor": "#3B82F6",
      "rightColor": "#10B981",
      "angle": 180,
      "bondOrder": 1,
      "captions": ["One sodium, one chlorine,", "locked side by side."]
    },
    {
      "id": "example-ph-bar",
      "type": "ph-bar",
      "bg": "#34C759",
      "fg": "#0B0E14",
      "accent": "#0B0E14",
      "eyebrow": "WHERE IT SITS",
      "headline": "Perfectly Neutral",
      "title": "THE pH SCALE",
      "targetPh": 7,
      "captions": ["Pure water sits at seven —", "the exact middle of the scale."]
    },
    {
      "id": "example-orbital-overlap",
      "type": "orbital-overlap",
      "bg": "#1ABC9C",
      "fg": "#0B0E14",
      "accent": "#0B0E14",
      "eyebrow": "SHARING ELECTRONS",
      "headline": "Orbitals Overlap",
      "atom1": "H",
      "atom2": "H",
      "captions": ["Two hydrogen atoms drift close,", "and their orbitals begin to overlap."]
    },
    {
      "id": "example-bond-comparison",
      "type": "bond-comparison",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#FFD60A",
      "eyebrow": "TWO KINDS OF BONDS",
      "headline": "Give vs. Share",
      "ionicAtom1": "Na",
      "ionicAtom2": "Cl",
      "covalentAtom1": "H",
      "covalentAtom2": "H",
      "captions": ["Ionic bonds hand electrons over.", "Covalent bonds share them."]
    },
    {
      "id": "example-particle-count",
      "type": "particle-count",
      "bg": "#FF3B30",
      "fg": "#FFFFFF",
      "accent": "#FFD60A",
      "eyebrow": "COUNTING IONS",
      "headline": "Packed With Protons",
      "particleLabel": "H⁺",
      "particleColor": "#FFFFFF",
      "targetCount": 12,
      "containerLabel": "Lemon juice, pH 2",
      "result": "Highly acidic",
      "captions": ["Lemon juice swarms", "with hydrogen ions."]
    },
    {
      "id": "example-tug-of-war",
      "type": "tug-of-war",
      "bg": "#1ABC9C",
      "fg": "#0B0E14",
      "accent": "#0B0E14",
      "eyebrow": "THE CONTEST",
      "headline": "Chlorine Wins",
      "leftSymbol": "Na",
      "rightSymbol": "Cl",
      "leftColor": "#3B82F6",
      "rightColor": "#10B981",
      "mode": "unequal",
      "electrons": 1,
      "verb": "winning",
      "captions": ["Chlorine pulls far harder,", "and rips the electron away."]
    }
  ]
}"""

REQUIRED_CONTENT_FIELDS: dict[str, list[str]] = {
    "cover": [],
    "stats": ["stat", "statLabel"],
    "quote": ["quote", "attribution"],
    "diagram": ["items"],
    "cta": ["subheadline"],
    "atom": ["symbol", "shell1", "shell2", "shell3", "title"],
    "element-card": ["symbol", "atomicNumber", "atomicMass", "name"],
    "reaction-equation": ["r1", "p1"],
    "molecule": [
        "leftSymbol",
        "rightSymbol",
        "leftColor",
        "rightColor",
        "angle",
        "bondOrder",
        "title",
    ],
    "ph-bar": ["targetPh", "title"],
    "orbital-overlap": ["atom1", "atom2"],
    "bond-comparison": [
        "ionicAtom1",
        "ionicAtom2",
        "covalentAtom1",
        "covalentAtom2",
    ],
    "particle-count": ["containerLabel", "result", "targetCount"],
    "tug-of-war": ["verb"],
}


def schema_path(settings: Settings) -> Path:
    return settings.hyperframes_dir / "templates" / RENDERER_TEMPLATE / "schema.json"


def get_config(settings: Settings) -> SubjectConfig:
    return SubjectConfig(
        name="chemistry",
        display_name="chemistry",
        topic_label="Chemistry topic",
        guard_description=(
            "Chemistry includes atoms, molecules, bonds, reactions, acids/bases, "
            "thermochemistry, electrochemistry, organic/inorganic/physical/"
            "analytical chemistry, and everyday chemistry phenomena. Queries "
            "that are primarily another subject (pure physics, math, biology "
            "without a chemical angle, history, etc.) or are not educational "
            "topics at all are not chemistry."
        ),
        narration_style=NARRATION_STYLE,
        scene_split_prompt=SCENE_SPLIT_PROMPT,
        scene_examples=SCENE_EXAMPLES,
        scene_schema_path=schema_path(settings),
        renderer_template=RENDERER_TEMPLATE,
        required_content_fields=REQUIRED_CONTENT_FIELDS,
    )
