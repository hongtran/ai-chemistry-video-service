from pathlib import Path

from app.config import Settings
from app.subjects.base import SubjectConfig

RENDERER_TEMPLATE = "tech"

NARRATION_STYLE = """You are a tech-education video narrator covering software engineering, programming, and AI topics (RAG, agents, LLMs, tooling, careers). Write a single narration script, meant to be read aloud once as continuous natural speech — no scene markers, no stage directions, no headings, just the spoken words. Plain text only.

Tone: clear, energetic, general developer audience, not a textbook. Technical facts must be accurate. Avoid reading out symbols or code verbatim — say things the way a person would speak them (e.g. "top k" not "top_k"). Prefer natural spoken phrasing over written/formal phrasing — this text will be spoken by a TTS voice.

Return ONLY the narration script text, nothing else."""

SCENE_SPLIT_PROMPT = """You are splitting an ALREADY-RECORDED tech narration into typed scene data for a JSON-driven video template called HyperFrames (dark developer aesthetic). The audio is final — you are assigning visuals and caption chunk breaks to existing words, not writing new narration.

You will be given:
1. The TRANSCRIPT of the recorded narration — the words actually spoken, in order. This is the SOURCE OF TRUTH for captions.
2. The narration script — reference only, for understanding meaning and structure. Its wording may differ from what was spoken (e.g. "fourteen" in the script may appear as "14" in the transcript).
3. A JSON Schema describing one scene object, including a "typeUsage" guide for choosing each scene type.

Return ONLY a single JSON object, no markdown fences, shaped exactly like:
{"scenes": [ { "id": "...", "type": "...", "eyebrow": "...", "headline": "...", "captions": ["...", "..."], ... type-specific fields ... } ] }

Critical rule: the "captions" arrays, concatenated in order across ALL scenes and split back into words, must reproduce the TRANSCRIPT text EXACTLY — same words, same order, nothing added, nothing removed, nothing reworded. You may only: (a) choose where to break it into 2-5 word caption chunks, (b) choose scene boundaries between chunks, (c) keep the transcript's punctuation and capitalization, and (d) wrap individual words in ** for emphasis (e.g. "**RAG**"). Do not paraphrase or substitute words — if the transcript says "retrieval augmented generation", your captions must say "retrieval augmented generation", not "RAG", even if that's the usual abbreviation.

The most common mistake on longer narrations: silently dropping a whole clause or sentence, especially one that sounds parallel/repetitive to a nearby one. Before returning your answer, re-read the transcript sentence by sentence and confirm every single sentence appears somewhere in your captions, in order, with nothing skipped.

Other rules:
- 6 to 9 scenes, contiguous narrative arc: hook → core concept → supporting detail(s) → a comparison or concrete result → closing takeaway/CTA.
- Every scene "id" is a short kebab-case slug, unique within the response.
- Choose each scene "type" using the schema's typeUsage guide. Vary frame types across scenes — don't repeat the same type back-to-back unless the content genuinely calls for it. Prefer a concept-specific type (pipeline, tool-use, memory, vector-space, thought-chain, ...) over a generic one (bullet-list) whenever the narration matches its usage guidance.
- Fields that hold on-screen labels (node/tool/app/step labels, code lines, terminal commands) are DISPLAY text, not narration — they may abbreviate and use symbols freely; the verbatim rule applies ONLY to "captions".
- Visuals and captions must be SYNCHRONIZED: every scene's visual content (headline, items, steps, and other type-specific fields) must be derived from the narration words in that scene's OWN "captions", so the visual is on screen while the words describing it are spoken. Place scene boundaries accordingly — if a sentence introduces or enumerates what a scene displays, that sentence belongs in THAT scene's captions, never in the previous or next scene's.
- The input includes a "REQUIRED CONTENT FIELDS PER TYPE" list: a scene missing ANY of its type's required fields renders as a broken frame. Fill every required field with a real value; if the narration doesn't give you enough to fill them, pick a more general type (bullet-list/concept-card/cta) instead. Also give every scene a "headline" or "title" where the type has one — it is the frame's visible title.
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
  "note": "GOLDEN EXAMPLES — one well-formed scene per frame type with ALL content params filled. bg/fg/accent are omitted to take each type's native default palette. 'captions' here are ILLUSTRATIVE ONLY: real captions must copy the job's transcript verbatim, in order, in 2-5 word chunks. start/duration/captionTiming are always omitted — the system computes them from the audio.",
  "examples": [
    {
      "id": "example-cover",
      "type": "cover",
      "eyebrow": "~/ai-engineering",
      "headline": "Why your AI agent **forgets**",
      "captions": ["Your AI agent has a **memory problem**.", "Here's how RAG fixes it."]
    },
    {
      "id": "example-chat",
      "type": "chat",
      "title": "The **amnesia** problem",
      "messages": [
        { "role": "user", "text": "What did we decide about the refund policy?" },
        { "role": "assistant", "text": "I don't have information about **your** refund policy." }
      ],
      "captions": ["Models only know their training data.", "Your docs? **Never seen them.**"]
    },
    {
      "id": "example-stats",
      "type": "stats",
      "eyebrow": "CONTEXT WINDOW",
      "stat": "200",
      "suffix": "K",
      "statLabel": "tokens — still **not enough** for your whole wiki",
      "captions": ["You can't paste the whole company", "into every prompt."]
    },
    {
      "id": "example-concept-card",
      "type": "concept-card",
      "term": "RAG",
      "tagline": "Retrieval-Augmented Generation",
      "glyph": "📚",
      "definition": "Fetch the **right** documents first, then let the model answer with them.",
      "captions": ["Retrieval. Augmentation. Generation.", "An **open-book exam** for your agent."]
    },
    {
      "id": "example-vector-space",
      "type": "vector-space",
      "title": "Step 1 — **embed** everything",
      "clusterLabels": ["refund docs", "api docs", "hr wiki"],
      "queryLabel": "agent's question",
      "captions": ["Every document becomes a point in space.", "Similar meaning lands **close together**."]
    },
    {
      "id": "example-pipeline",
      "type": "pipeline",
      "title": "Step 2 — the **pipeline**",
      "nodes": [
        { "label": "Question", "glyph": "❓" },
        { "label": "Retriever", "sublabel": "nearest neighbors", "glyph": "🔍" },
        { "label": "Vector DB", "sublabel": "top-k chunks", "glyph": "🗄️" },
        { "label": "LLM", "sublabel": "grounded answer", "glyph": "🧠" }
      ],
      "highlightNode": 1,
      "captions": ["The agent asks, the retriever fetches,", "the model answers **with receipts**."]
    },
    {
      "id": "example-code-snippet",
      "type": "code-snippet",
      "headline": "The whole trick in **5 lines**",
      "filename": "agent.py",
      "language": "python",
      "code": ["docs = db.search(question, top_k=3)", "context = \\"\\\\n\\".join(d.text for d in docs)", "# the model reads YOUR docs first", "answer = llm.ask(", "    f\\"{context}\\\\n\\\\nQ: {question}\\"", ")"],
      "highlightLines": [1, 3],
      "captions": ["Search your data,", "stuff it in the prompt. **That's RAG.**"]
    },
    {
      "id": "example-comparison",
      "type": "comparison",
      "headline": "RAG or **fine-tuning**?",
      "leftTitle": "RAG",
      "rightTitle": "Fine-tuning",
      "leftItems": ["Fresh data instantly", "Cites its sources", "Cheap to update"],
      "rightItems": ["New skills & style", "Slow, costly retrains", "Knowledge freezes"],
      "verdict": "For agent memory → **RAG wins**.",
      "captions": ["Facts change daily.", "Retrieval keeps up. Retraining **can't**."]
    },
    {
      "id": "example-cta",
      "type": "cta",
      "headline": "Give your agent a **library**",
      "subheadline": "Retrieve. Augment. Generate.",
      "eyebrow": "@ai.decoded",
      "captions": ["That's RAG for AI agents.", "Follow for more **AI engineering**."]
    },
    {
      "id": "example-quote",
      "type": "quote",
      "quote": "The hottest new programming language is **English**.",
      "attribution": "Andrej Karpathy",
      "captions": ["Prompting became a real skill overnight."]
    },
    {
      "id": "example-bullet-list",
      "type": "bullet-list",
      "title": "5 skills of an **AI engineer**",
      "items": ["Prompt & context design", "**RAG** pipelines", "Evals & observability", "Fine-tuning basics", "Product sense"],
      "captions": ["It is less about math,", "more about **systems**."]
    },
    {
      "id": "example-terminal",
      "type": "terminal",
      "headline": "Ship it in **one command**",
      "eyebrow": "~/my-agent",
      "commands": [
        { "cmd": "pip install langchain", "output": ["Collecting langchain...", "✓ Installed in 2.1s"] },
        { "cmd": "python agent.py", "output": ["Agent ready on :8000"] }
      ],
      "captions": ["Two commands,", "and the agent is **live**."]
    },
    {
      "id": "example-roadmap",
      "type": "roadmap",
      "title": "Path to **AI engineer**",
      "steps": [
        { "label": "Python basics", "sublabel": "3 months" },
        { "label": "APIs & prompts", "sublabel": "LLM 101" },
        { "label": "Build with RAG", "sublabel": "ship a project" },
        { "label": "Evals & agents", "sublabel": "production" }
      ],
      "captions": ["You can walk this path", "in under a **year**."]
    },
    {
      "id": "example-stack-layers",
      "type": "stack-layers",
      "title": "Where does an **AI engineer** work?",
      "layers": [
        { "label": "Product & UI", "sublabel": "what users touch" },
        { "label": "Orchestration", "sublabel": "agents · RAG · evals" },
        { "label": "Model APIs", "sublabel": "GPT · Claude · Llama" },
        { "label": "Infra", "sublabel": "GPUs · serving" }
      ],
      "highlightIndex": 1,
      "annotation": "you work HERE",
      "captions": ["Right **between** the product", "and the models."]
    },
    {
      "id": "example-neural-net",
      "type": "neural-net",
      "title": "Inside a **neural network**",
      "layerLabels": ["tokens in", "layers", "next word"],
      "outputLabel": "prediction",
      "captions": ["Signals ripple through the layers", "until a **prediction** comes out."]
    },
    {
      "id": "example-task-breakdown",
      "type": "task-breakdown",
      "title": "Agents make a **to-do list**",
      "goal": "Plan a team offsite",
      "subtasks": [
        { "label": "Pick 3 candidate dates", "sublabel": "check calendars" },
        { "label": "Compare venues & prices", "sublabel": "web search" },
        { "label": "Draft the agenda", "sublabel": "1-day format" },
        { "label": "Send invites", "sublabel": "email tool" }
      ],
      "captions": ["Big goal in,", "small **finishable** steps out."]
    },
    {
      "id": "example-thought-chain",
      "type": "thought-chain",
      "title": "Thinking **out loud**",
      "question": "Should we refund order #482?",
      "thoughts": [
        "My goal is to check the refund policy first.",
        "Policy says 30 days — order is 12 days old.",
        "Customer is eligible. Next: issue the refund."
      ],
      "conclusion": "Refund **approved** — within the 30-day window.",
      "captions": ["Plan first,", "**then** act."]
    },
    {
      "id": "example-tool-use",
      "type": "tool-use",
      "title": "Agents use **tools**",
      "agentLabel": "Agent",
      "tools": [
        { "glyph": "🔍", "label": "Web search", "sublabel": "live info" },
        { "glyph": "🧮", "label": "Calculator", "sublabel": "exact math" },
        { "glyph": "🗄️", "label": "Database", "sublabel": "your data" },
        { "glyph": "✉️", "label": "Email", "sublabel": "take action" }
      ],
      "captions": ["Not trapped in a **chat box** —", "it reaches out."]
    },
    {
      "id": "example-memory",
      "type": "memory",
      "title": "Agents **remember**",
      "shortItems": ["Asked about order #482", "Wants a refund"],
      "longItems": ["Prefers email replies", "VIP customer since 2023"],
      "captions": ["No more starting **from scratch**", "every single chat."]
    },
    {
      "id": "example-reflection-loop",
      "type": "reflection-loop",
      "title": "Agents **check their work**",
      "steps": [
        { "label": "Attempt", "glyph": "✍️" },
        { "label": "Check", "glyph": "🔎" },
        { "label": "Fix", "glyph": "🔧" }
      ],
      "failLabel": "error found",
      "passLabel": "fixed",
      "captions": ["Fail once,", "**learn**, retry."]
    },
    {
      "id": "example-mcp-hub",
      "type": "mcp-hub",
      "title": "One plug: **MCP**",
      "agentLabel": "Your Agent",
      "hubLabel": "MCP",
      "apps": [
        { "glyph": "💬", "label": "Slack" },
        { "glyph": "📁", "label": "Drive" },
        { "glyph": "💳", "label": "Stripe" },
        { "glyph": "🗓️", "label": "Calendar" }
      ],
      "captions": ["No custom code", "per **integration**."]
    }
  ]
}"""

REQUIRED_CONTENT_FIELDS: dict[str, list[str]] = {
    "cover": [],
    "stats": ["stat", "statLabel"],
    "quote": ["quote", "attribution"],
    "bullet-list": ["title", "items"],
    "cta": ["subheadline"],
    "concept-card": ["term", "definition"],
    "code-snippet": ["code"],
    "terminal": ["commands"],
    "chat": ["messages"],
    "pipeline": ["title", "nodes"],
    "comparison": ["leftTitle", "rightTitle", "leftItems", "rightItems"],
    "roadmap": ["title", "steps"],
    "stack-layers": ["title", "layers"],
    "vector-space": ["title", "clusterLabels"],
    "neural-net": ["title"],
    "task-breakdown": ["goal", "subtasks"],
    "thought-chain": ["question", "thoughts", "conclusion"],
    "tool-use": ["tools"],
    "memory": ["shortItems", "longItems"],
    "reflection-loop": ["steps"],
    "mcp-hub": ["apps"],
}


def schema_path(settings: Settings) -> Path:
    return settings.hyperframes_dir / "templates" / RENDERER_TEMPLATE / "schema.json"


def get_config(settings: Settings) -> SubjectConfig:
    return SubjectConfig(
        name="tech",
        display_name="tech",
        topic_label="Tech topic",
        guard_description=(
            "Tech covers software engineering, programming languages, AI/ML "
            "and LLMs, agents and RAG, developer tooling, cloud/infra, "
            "computer science fundamentals, and tech careers. Queries that "
            "are primarily another subject (pure math without a software "
            "angle, hardware electronics, general business, history, etc.) "
            "or are not educational topics at all are not tech."
        ),
        narration_style=NARRATION_STYLE,
        scene_split_prompt=SCENE_SPLIT_PROMPT,
        scene_examples=SCENE_EXAMPLES,
        scene_schema_path=schema_path(settings),
        renderer_template=RENDERER_TEMPLATE,
        required_content_fields=REQUIRED_CONTENT_FIELDS,
    )
