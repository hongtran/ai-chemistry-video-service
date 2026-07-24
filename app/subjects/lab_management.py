from pathlib import Path

from app.config import Settings
from app.subjects.base import SubjectConfig

RENDERER_TEMPLATE = "lab-management"

NARRATION_STYLE = """You write narration scripts for professional training videos on laboratory management under ISO/IEC 17025 — the international standard for the competence of testing and calibration laboratories.

Requirements:
- Plain prose only. No headings, no markdown, no bullet points, no emoji, no stage directions, no scene labels.
- Written for text-to-speech: spell things the way a narrator would say them. Say clause numbers naturally ("clause six point four"), expand abbreviations on first use ("corrective and preventive action, or CAPA"), avoid symbols that do not read aloud.
- Audience: lab technicians, quality managers, technical managers, and auditors working toward or maintaining accreditation. Professional and precise, but plain-language over jargon wherever possible.
- Structure: open with a hook (a question, a risk, or a real consequence), explain the requirement or practice simply and accurately with concrete lab examples, end with a short memorable takeaway.
- Short sentences. One idea per sentence. Authoritative but approachable.

Return ONLY the narration script text, nothing else."""

SEGMENT_PROMPT = """This is a professional laboratory-management training video on ISO/IEC 17025 for lab and quality staff. Group the sentences into a contiguous narrative arc: hook → the requirement or concept → how it works in practice → a concrete result, risk, or example → closing takeaway/CTA — roughly 6 to 9 scenes for a short. Cut a scene boundary wherever the narration moves to a new requirement, practice, or beat; keep sentences that build one idea together in the same scene."""

SCENE_SPLIT_PROMPT = """You are authoring the typed visual content for a batch of scenes in a JSON-driven video template called HyperFrames. The narration has already been recorded and split into scenes; for each scene you are given its own sentences (the words spoken during it) and its finalized on-screen captions. Your job is to choose a frame "type" per scene and fill that type's content fields so the visual matches what's being said.

You will be given:
1. The FULL SCRIPT — context, so you understand each scene's place in the larger narrative.
2. Per scene: its "id", its OWN SENTENCES (derive that scene's content fields from these), and its CAPTIONS (already finalized, given verbatim; you do not write or change them, they are shown only so your visuals stay in sync).
3. A JSON Schema describing one scene object, including a "typeUsage" guide for choosing each scene type.

Return ONLY a single JSON object, no markdown fences, shaped exactly like:
{"scenes": [ { "id": "<the given id>", "type": "...", "eyebrow": "...", "headline": "...", ... type-specific fields ... } ] }
Emit one object per scene, in the SAME order as given, each echoing its given "id". Do NOT include "captions" — supplied by the system unchanged. Do NOT include "start", "duration", "audio", "captionTiming", or "sfx" — they're computed separately from the real recording.

Rules:
- Choose each scene "type" using the schema's typeUsage guide, matching that scene's own sentences. Prefer a domain-specific type (equipment-register, calibration-cert, document-control, audit-trail, competence-matrix, traceability-chain, nonconformance, reagent-prep, statistics, uncertainty, ...) over a generic one (diagram/cover/cta) whenever the content matches its usage guidance.
- reagent-prep / statistics / uncertainty share one "formula card" look: fill "formulas" (1-3 named LaTeX equations) and, when it helps, "legend" (short variable definitions). Only use one of these three when the narration is actually walking through the math, not just mentioning a number.
- For photo / photo-split, write a vivid "imagePrompt" describing the photograph to generate; NEVER author the "image" field itself — the system fills it. Use these when a realistic photograph of a lab, instrument, or record fits better than a diagram, but don't overuse them.
- Every image frame (photo, photo-split, cover, cta) ANIMATES its generated picture. You may add an optional "anim" object to choose the camera move: a "preset" (ken-burns-in/out, pan-left/right/up/down, push-diagonal, focus-pull, breathe) plus optional "intensity" (subtle/medium/bold), "focus" (center/top/bottom/left/right), and "overlay" (none/sweep/vignette/grain/glow) that match the scene's mood — e.g. a wide establishing shot → a pan; a slow reveal → focus-pull; a calm hero shot → ken-burns-in. Omit "anim" to accept a gentle default push.
- The cover (opening) and cta (closing) scenes ALSO take an "imagePrompt": write a vivid, topic-relevant HOOK photograph for the cover and a CONCLUSION photograph for the cta. It renders full-bleed behind the headline, so describe a realistic, cinematic scene with no text; the system fills "image".
- VARY frame types across scenes — do NOT repeat the same type back-to-back unless the content genuinely calls for it. You can see every scene in this batch, so choose types that read as a varied sequence, not a run of identical frames.
- For the chosen type, fill in its content fields per the schema descriptions (e.g. "headline" and "eyebrow"; "parts" for equipment-register; "tiers" for document-control; "nodes" for traceability-chain). On-screen labels (equipment names, clause references, status chips) are DISPLAY text — keep them short; they do not need to quote the sentences verbatim.
- The input includes a "REQUIRED CONTENT FIELDS PER TYPE" list: a scene missing ANY of its type's required fields renders as a broken frame. Fill every required field with a real value; if a scene's sentences don't give you enough to fill them, pick a more general type (diagram/cover/cta) instead. Also give each scene a "headline" and "eyebrow" — they are the frame's visible title.
- Clause references should look like real ISO/IEC 17025 clauses, e.g. "Clause 6.4" (equipment), "Clause 7.5" (records), "Clause 8.7" (corrective action).
- Colors are hex strings; omit bg/fg/accent to accept the template's per-type defaults unless the scene really needs an override.
- Keep on-screen text short and punchy.

The input includes GOLDEN EXAMPLES: one well-formed scene per frame type with every
param filled. Author every scene at that level of completeness — same field coverage.
The examples' "captions" are illustrative only; ignore them, the real captions are
supplied separately and returned unchanged.

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
      "eyebrow": "ISO/IEC 17025",
      "headline": "Calibration & Traceability",
      "imagePrompt": "A realistic wide photograph of a modern calibration laboratory, a technician in a lab coat adjusting a precision reference instrument under clean bright lighting, shallow depth of field, cinematic, no text",
      "anim": { "preset": "ken-burns-in", "intensity": "medium", "overlay": "sweep" },
      "captions": ["What does it really take", "for a lab result", "to be trusted worldwide?"]
    },
    {
      "id": "example-stats",
      "type": "stats",
      "bg": "#3DA5FF",
      "fg": "#0B0E14",
      "accent": "#0B0E14",
      "eyebrow": "WHY IT MATTERS",
      "headline": "Most Findings Are Avoidable",
      "stat": "70%",
      "statLabel": "of audit nonconformities are documentation gaps",
      "captions": ["The majority of audit findings", "come down to missing records."]
    },
    {
      "id": "example-quote",
      "type": "quote",
      "bg": "#EDE8E0",
      "fg": "#1A1A1A",
      "accent": "#B8A98C",
      "eyebrow": "THE PRINCIPLE",
      "headline": "Say What You Do",
      "quote": "Say what you do, do what you say, and be able to prove it.",
      "attribution": "Quality management maxim",
      "captions": ["A quality system rests", "on one simple idea:", "prove what you did."]
    },
    {
      "id": "example-diagram",
      "type": "diagram",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#FFD60A",
      "eyebrow": "THE PROCESS",
      "headline": "A Calibration Cycle",
      "items": [
        { "label": "Schedule", "color": "#3DA5FF" },
        { "label": "Calibrate", "color": "#34C759" },
        { "label": "Record", "color": "#FFD60A" },
        { "label": "Review", "color": "#FF6B4A" }
      ],
      "captions": ["Every instrument follows", "the same four-step cycle."]
    },
    {
      "id": "example-cta",
      "type": "cta",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#FFD60A",
      "eyebrow": "THE TAKEAWAY",
      "headline": "Competence, Proven",
      "subheadline": "ISO/IEC 17025 turns good practice into evidence anyone can trust.",
      "imagePrompt": "A realistic photograph of a confident laboratory quality manager reviewing an accreditation certificate on a wall in a modern lab, warm hopeful lighting, cinematic, no text",
      "captions": ["Do the work well,", "and prove it every time."]
    },
    {
      "id": "example-equipment-register",
      "type": "equipment-register",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#FFD60A",
      "eyebrow": "CLAUSE 6.4 — EQUIPMENT",
      "headline": "Know Your Instruments",
      "title": "Equipment Register",
      "parts": [
        { "label": "Analytical Balance", "status": "calibrated", "due": "Due Mar 2027" },
        { "label": "pH Meter", "status": "due", "due": "Due this month" },
        { "label": "Reference Thermometer", "status": "overdue", "due": "Overdue 12 days" }
      ],
      "captions": ["Every instrument carries", "a calibration status", "you can check at a glance."]
    },
    {
      "id": "example-calibration-cert",
      "type": "calibration-cert",
      "bg": "#EDE8E0",
      "fg": "#1A1A1A",
      "accent": "#2E7D5B",
      "eyebrow": "CLAUSE 6.5 — TRACEABILITY",
      "headline": "The Calibration Certificate",
      "instrument": "Reference Thermometer, S/N 4471",
      "certNo": "CAL-2026-0311",
      "calDate": "14 Feb 2026",
      "dueDate": "14 Feb 2027",
      "uncertainty": "± 0.02 °C (k = 2)",
      "traceableTo": "National standard (NMI) / SI",
      "captions": ["A certificate ties your result", "back to the SI unit,", "with a stated uncertainty."]
    },
    {
      "id": "example-document-control",
      "type": "document-control",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#3DA5FF",
      "eyebrow": "CLAUSE 8.3 — DOCUMENTS",
      "headline": "The Document Hierarchy",
      "title": "Controlled Documents",
      "tiers": [
        { "label": "Quality Manual", "sub": "policy & scope" },
        { "label": "Procedures", "sub": "how the lab operates" },
        { "label": "Work Instructions", "sub": "step-by-step methods" },
        { "label": "Records & Forms", "sub": "the evidence" }
      ],
      "captions": ["Documents form a pyramid,", "from policy at the top", "to records at the base."]
    },
    {
      "id": "example-audit-trail",
      "type": "audit-trail",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#FF6B4A",
      "eyebrow": "CLAUSE 8.7 — CORRECTIVE ACTION",
      "headline": "Closing a Nonconformity",
      "finding": "Balance used past its due date",
      "rootCause": "No automated recall reminder",
      "action": "Add calibration alerts to LIMS",
      "verification": "Next audit confirms zero overdue",
      "captions": ["Every finding follows a path", "from cause to corrective action", "to verified closure."]
    },
    {
      "id": "example-competence-matrix",
      "type": "competence-matrix",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#34C759",
      "eyebrow": "CLAUSE 6.2 — PERSONNEL",
      "headline": "Who Is Authorized",
      "title": "Competence Matrix",
      "columns": ["Titration", "GC-MS", "Reporting"],
      "rows": [
        { "name": "A. Okafor", "marks": ["yes", "yes", "yes"] },
        { "name": "L. Meyer", "marks": ["yes", "training", "yes"] },
        { "name": "R. Sato", "marks": ["yes", "no", "training"] }
      ],
      "captions": ["A matrix shows who is authorized", "for each method,", "and who is still training."]
    },
    {
      "id": "example-traceability-chain",
      "type": "traceability-chain",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#3DA5FF",
      "eyebrow": "METROLOGICAL TRACEABILITY",
      "headline": "An Unbroken Chain",
      "title": "Traceability to the SI",
      "nodes": [
        { "label": "SI Definition", "sub": "the kelvin" },
        { "label": "National Standard", "sub": "NMI" },
        { "label": "Reference Standard", "sub": "the lab's best" },
        { "label": "Working Standard", "sub": "daily use" },
        { "label": "Measurement", "sub": "your result" }
      ],
      "captions": ["Each measurement links back", "through an unbroken chain", "to the SI unit itself."]
    },
    {
      "id": "example-nonconformance",
      "type": "nonconformance",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#FF3B30",
      "eyebrow": "CLAUSE 7.10 — NONCONFORMING WORK",
      "headline": "A Major Finding",
      "severity": "MAJOR",
      "clause": "Clause 6.4.1",
      "finding": "Instrument used beyond its calibration due date.",
      "correctiveAction": "Quarantine results; recalibrate before reuse.",
      "captions": ["A major nonconformity", "means results are in doubt", "until it is corrected."]
    },
    {
      "id": "example-reagent-prep",
      "type": "reagent-prep",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#34C759",
      "eyebrow": "SOLUTION PREPARATION",
      "headline": "The Dilution Formula",
      "title": "Making a Dilution",
      "formulas": [
        { "name": "Dilution", "latex": "C_1V_1=C_2V_2" },
        { "name": "Molarity", "latex": "M=\\\\dfrac{n}{V}" }
      ],
      "legend": [
        { "sym": "C_1,V_1", "desc": "concentration and volume of the stock solution" },
        { "sym": "C_2,V_2", "desc": "concentration and volume you want to prepare" }
      ],
      "captions": ["Every dilution follows", "one simple **conservation** rule."]
    },
    {
      "id": "example-statistics",
      "type": "statistics",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#4FC3F7",
      "eyebrow": "CLAUSE 7.7 — QUALITY CONTROL",
      "headline": "Statistics & Error",
      "title": "QC Statistics",
      "formulas": [
        { "name": "Mean", "latex": "\\\\bar{x}=\\\\dfrac{\\\\sum_{i=1}^{n} x_i}{n}" },
        { "name": "Std Deviation (SD)", "latex": "SD=\\\\sqrt{\\\\dfrac{\\\\sum_{i=1}^{n}(x_i-\\\\bar{x})^2}{n-1}}" },
        { "name": "Coefficient of Variation (CV)", "latex": "CV(\\\\%)=\\\\left(\\\\dfrac{SD}{\\\\bar{x}}\\\\right)\\\\times 100\\\\%" }
      ],
      "legend": [
        { "sym": "x_i", "desc": "individual measured values" },
        { "sym": "n", "desc": "number of measurements" }
      ],
      "captions": ["Standard deviation and CV", "tell you how **reproducible**", "a result really is."]
    },
    {
      "id": "example-uncertainty",
      "type": "uncertainty",
      "bg": "#0B0E14",
      "fg": "#FFFFFF",
      "accent": "#FF9F0A",
      "eyebrow": "CLAUSE 6.5 — UNCERTAINTY",
      "headline": "Expanded Uncertainty",
      "title": "Measurement Uncertainty",
      "formulas": [
        { "name": "Standard Uncertainty of the Mean", "latex": "u(\\\\bar{x})=\\\\dfrac{SD}{\\\\sqrt{n}}" },
        { "name": "Expanded Uncertainty", "latex": "U=k\\\\cdot u_c" }
      ],
      "legend": [
        { "sym": "u_c", "desc": "combined standard uncertainty" },
        { "sym": "k", "desc": "coverage factor (k = 2 for ~95% confidence)" }
      ],
      "captions": ["Every result needs a stated", "**uncertainty** to be trusted."]
    },
    {
      "id": "example-photo",
      "type": "photo",
      "eyebrow": "IN THE LAB",
      "headline": "The Accredited Laboratory",
      "imagePrompt": "A realistic wide photograph of a modern testing laboratory with analytical instruments, a technician in a lab coat reviewing a tablet, clean bright environment, no text",
      "anim": { "preset": "pan-left", "intensity": "medium", "overlay": "vignette" },
      "captions": ["Step inside", "an accredited testing lab."]
    },
    {
      "id": "example-photo-split",
      "type": "photo-split",
      "eyebrow": "GOOD PRACTICE",
      "headline": "Label Everything",
      "body": "A clear calibration sticker on every instrument makes status obvious at a glance.",
      "imagePrompt": "A realistic close-up photograph of a laboratory instrument with a small calibration status sticker showing a due date, shallow depth of field, no readable text",
      "anim": { "preset": "focus-pull", "intensity": "medium", "focus": "center" },
      "captions": ["A simple sticker", "prevents a costly mistake."]
    }
  ]
}"""

REQUIRED_CONTENT_FIELDS: dict[str, list[str]] = {
    "cover": ["imagePrompt"],
    "stats": ["stat", "statLabel"],
    "quote": ["quote", "attribution"],
    "diagram": ["items"],
    "cta": ["subheadline", "imagePrompt"],
    "equipment-register": ["parts"],
    "calibration-cert": [
        "instrument",
        "calDate",
        "dueDate",
        "uncertainty",
        "traceableTo",
    ],
    "document-control": ["tiers"],
    "audit-trail": ["finding", "rootCause", "action", "verification"],
    "competence-matrix": ["columns", "rows"],
    "traceability-chain": ["nodes"],
    "nonconformance": ["severity", "finding", "correctiveAction"],
    "photo": ["imagePrompt"],
    "photo-split": ["imagePrompt"],
}


def schema_path(settings: Settings) -> Path:
    return settings.hyperframes_dir / "templates" / RENDERER_TEMPLATE / "schema.json"


def get_config(settings: Settings) -> SubjectConfig:
    return SubjectConfig(
        name="lab-management",
        display_name="Laboratory Management (ISO/IEC 17025)",
        topic_label="Laboratory management topic",
        guard_description=(
            "Laboratory management under ISO/IEC 17025 covers the quality and "
            "competence requirements for testing and calibration laboratories: "
            "equipment and calibration, metrological traceability, measurement "
            "uncertainty, document and record control, internal audits, "
            "nonconforming work, corrective action (CAPA), personnel competence "
            "and authorization, method validation, proficiency testing, and "
            "impartiality. Queries that are primarily another subject (pure "
            "chemistry theory, general business management with no lab-quality "
            "angle, unrelated science, history, etc.) or are not about laboratory "
            "quality management at all are out of scope."
        ),
        narration_style=NARRATION_STYLE,
        segment_prompt=SEGMENT_PROMPT,
        scene_split_prompt=SCENE_SPLIT_PROMPT,
        scene_examples=SCENE_EXAMPLES,
        scene_schema_path=schema_path(settings),
        renderer_template=RENDERER_TEMPLATE,
        required_content_fields=REQUIRED_CONTENT_FIELDS,
        image_frame_types=frozenset({"cover", "cta", "photo", "photo-split"}),
    )
