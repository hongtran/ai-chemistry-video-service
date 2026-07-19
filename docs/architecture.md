# Architecture Note

FastAPI backend: subject-scoped educational query → rendered explainer video.
Chemistry is the only enabled subject today, but prompts, schema selection, and
render template selection are routed through a subject registry.

## Job lifecycle

`POST /videos` is a **synchronous gate** (length + LLM subject guard); only on pass does it create a `PENDING` job, enqueue the id, and return `202`. The client polls. An async video generation job — `RealVideoPipeline.run`
— which advances `PROCESSING → COMPLETED/FAILED` and bumps `current_step` through `narration → tts → transcription → scene_split → alignment → compose → render`.

**Decisions & trade-offs**
- **Guard is sync in the request, not a pipeline step.** Bad queries get a fast `400` and narrow scope keeps prompts + frame templates tuned and safe, client dont need check status. Trade-off: one LLM call on the request path (guard down → `503`).
- **Two failure layers.** Expected step failures → `FAILED` with a client-safe `"<step>: <reason>"`; a worker-level catch-all handles genuine bugs so the worker never dies, clearly LLM failure. Trade-off: extra plumbing for a clean status/500 split.
- **One targeted retry, not generic.** Alignment failure → *one* re-scene-split fed the alignment error as feedback to LLM can resolve the error, then re-align; second failure is terminal (config number retries).
Trade-off: an extra LLM round-trip, bounded so jobs can't loop.

## Persistence / artifact boundary

**Rule: the `Job` model holds metadata; the disk holds bytes.** Job state lives behind `JobRepository`; artifacts live under `<artifacts_dir>/<job_id>/<name>` behind `ArtifactStore`. Both are Protocols with one prototype impl each (`InMemory…`, `Local…`); swapping to Postgres/Redis or S3 = implement the protocol + change **one wiring line in `main.py`**.

**Decisions & trade-offs**
- **Nothing large on the model** — `video_path` is a pointer, re-checked on disk at download. Keeps job state cheap to move to a real DB.
- **Artifacts written per-step**, so a `FAILED` job can tracked and debuggable
- **In-memory state is ephemeral** (dies with the process). Accepted for the prototype; the Protocol is the seam to make it durable.

## Video-generation pipeline boundary
  ```text
 Query
    │
    ▼
narration script      ──▶ script.txt
    │
    tts               ──▶ narration.mp3
    │
 transcription        ──▶ transcript.json
    │
 scene_split(base on schema)  ──▶ scenes.json
    │
    ▼
 alignment ───ok──────────────────────────┐
    │ fail                                 │
    ▼                                      │
 re-scene-split (+ alignment feedback)     │
    │                                      │
    ▼                                      │
 alignment #2 ──ok───────────────────────▶ │
    │ fail                                 │
    ▼                                      ▼
  FAILED                            compose + validate ──▶ data.json
                                           │
                                           ▼
                                  render (subprocess build-video.sh)
                                           │
                        ┌──────────────────┴───────────────┐
                        │ non-zero exit / no file           │ ok
                        ▼                                    ▼
                      FAILED                          video.mp4
                                                             │
                                                             ▼
                                              status = COMPLETED, video_path set
```
**Decisions & trade-offs**
- **LLM authors data, not code.** Each run produces a schema-constrained
  `data.json`; the subprocess renders by populating it into a pre-defined set of
  frame HTML templates (hyperframes, headless Chrome). The LLM never generates
  render code.
  - Trade-off: more moving parts, but it **scales by extending subject config and
    frame templates** — adding a subject should not require changing pipeline
    orchestration. Rendering stays **deterministic, low-error, and controllable**,
    unlike letting the LLM emit render code.
  - Cost: **4 LLM calls on a lightweight model** per video — cheaper than
    end-to-end AI-video generators.
- **Captions come from script + transcript, not guesswork.** Scene-split captions
  are authored from the original script and the transcript text.
  - Trade-off: one extra LLM/transcription call to get **word-level tim
- **Timing is system-computed, never LLM-authored.** The LLM authors scene
  *content* against a relaxed "authoring" schema (no start/duration); alignment +
  `compose` compute the timing and validate the final `data.json` against the full
  schema.
  - Trade-off: two schemas to keep in sync, but it removes a whole class of LLM
    errors (bad/overlapping timings) from the output.
- **Fail loud, no silent fallback.** A persistent alignment failure ends in
  `FAILED` with a clear message rather than shipping a video with mismatched or
  empty caption timing.
  - Trade-off: more outright failures, fewer "completed but subtly wrong" videos —
    a deliberate correctness-over-completion call.
