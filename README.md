# AI Subject Video Request Service

Backend prototype: a learner submits a subject-scoped educational query, the service runs it as an
async video-generation job (LLM narration → TTS → word-level transcript → typed scene
split → data.json → hyperframes render), and the client polls status and downloads the
finished video. Laboratory Management (ISO/IEC 17025) is the primary enabled subject today; the pipeline is structured
so additional subjects can be added through subject config, prompts, schemas, and
renderer templates.

## Architecture

Two sections with clear boundaries (each behind a Protocol, swappable without touching
the rest):

| Boundary | Prototype impl | Swap target |
|---|---|---|
| `JobRepository` (job state) | in-memory dict + lock | Postgres / Redis |
| `ArtifactStore` (files) | local `artifacts/<job_id>/` | S3 |
| `JobQueue` (async work) | asyncio.Queue + worker task | Celery / SQS |
| `VideoPipeline` (processing) | `StubPipeline` (Phase 1) / real orchestrator (Phase 2) | — |

**Boundary rule:** job *state* lives only in the repository; *artifacts* live only on disk
under `artifacts/<job_id>/<name>` — resolvable from the job id alone. The Job model keeps a
single `video_path` pointer set on completion.

## Job lifecycle

`PENDING` → `PROCESSING` (with `current_step`: narration → tts → transcription →
scene_split → alignment → compose → render) → `COMPLETED` | `FAILED` (`error_message`
formatted `"<step>: <reason>"`).

Subject validation is **not** part of the job lifecycle: the LLM guard runs synchronously
in the POST handler — a query outside the requested subject gets an immediate `400` and
no job is created. Accepted subjects: `subject="lab-management"` (default) and `subject="tech"`.

## Run local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # stub mode is on by default; add OPENAI_API_KEY for real mode
uvicorn app.main:app --reload
```

`USE_STUB_PIPELINE=true` (default in `.env.example`) runs a credential-free demo: the guard
accepts any non-empty query and the pipeline walks all steps with placeholder artifacts.

## Rendering

The real render step shells out to `render_kit/scripts/build-video.sh` — a vendored,
self-contained copy of the hyperframes template toolkit (`HYPERFRAMES_DIR=./render_kit`,
the default). No external repo is required. The hyperframes CLI itself is fetched at
runtime via `npx hyperframes@0.7.18`, so rendering needs **Node 18+, npm, and internet**
(a headless Chromium is downloaded on first run). See `render_kit/README.md` for details
and a manual render command that produces a video with no OpenAI key.

## Docker

The image bundles everything — Python service **and** the render toolchain (Node,
system Chromium, ffmpeg) — so no host Node/Chromium is needed.

```bash
docker build -t chemistry-video-service .

# Stub mode: no credentials, walks the pipeline with placeholder artifacts
docker run --rm -p 8000:8000 -e USE_STUB_PIPELINE=true chemistry-video-service

# Real mode: pass your env (OPENAI_API_KEY, USE_STUB_PIPELINE=false, …)
docker run --rm -p 8000:8000 --env-file .env chemistry-video-service
```

Then hit `http://localhost:8000` (see the API section). `.env` is **not** baked into
the image (it's in `.dockerignore`) — credentials are passed at runtime.

To keep rendered videos on the host across restarts, mount the artifacts dir:
`-v "$(pwd)/artifacts:/app/artifacts"`.

## How to test on local
Access this API doc: http://0.0.0.0:8000/docs then click `Try it out` button for test on that

Notes:
- Chromium runs software-rendered (no GPU in the container), so rendering is slower
  than on a GPU host but produces identical output.
- `CONTAINER=true` (set in the image) makes the hyperframes engine apply the right
  Chromium sandbox flags automatically.

## API (base: `/api/v1`)

| Method & path | Purpose | Responses |
|---|---|---|
| `POST /videos` `{"query": "...", "subject": "lab-management"}` | request a video | `202` `{id, subject, status}` · `400` outside subject / bad query · `422` unsupported subject · `503` guard unavailable |
| `GET /videos?status=` | list jobs | `200` summaries · `400` bad status value |
| `GET /videos/{id}` | job detail + artifact names | `200` · `404` |
| `GET /videos/{id}/video` | download finished mp4 | `200` mp4 · `409` not ready (includes status/step/error) · `404` |
| `GET /videos/{id}/artifacts/{name}` | debug: download intermediate artifact | `200` · `400` unknown name · `404` |

### Example

```bash
curl -s -X POST localhost:8000/api/v1/videos -H 'content-type: application/json' \
  -d '{"query": "How does calibration traceability work?", "subject": "lab-management"}'
# → {"id": "…", "subject": "lab-management", "status": "PENDING"}

curl -s localhost:8000/api/v1/videos/<id>          # poll: watch current_step advance
curl -sO localhost:8000/api/v1/videos/<id>/video   # when COMPLETED
```

## Layout

```
app/
  main.py            app factory + lifespan (wires deps, starts/stops worker)
  config.py          env-driven settings (.env supported)
  domain/models.py   Job, JobStatus, PipelineStep
  api/               router.py (endpoints), schemas.py (DTOs)
  storage/           jobs.py (JobRepository), artifacts.py (ArtifactStore)
  worker/queue.py    JobQueue + asyncio worker loop
  llm/client.py      OpenAI wrapper, retry/backoff, subject guard
  subjects/          enabled subject registry and subject-owned prompts
  pipeline/          base.py (protocol), stub.py; orchestrator + steps arrive in Phase 2
render_kit/                 vendored render toolkit; templates/<subject>/schema.json is canonical
artifacts/<job_id>/         per-job files (gitignored)
```

## Pipeline (real mode)

narration (LLM, TTS-friendly script) → tts (OpenAI TTS) → transcription (whisper-1 word
timestamps) → scene_split (LLM, validated against the subject schema with one
corrective re-prompt on schema errors) → alignment (Python-native greedy word matcher; on
failure, ONE retry that re-runs scene_split with the alignment error as feedback, then a
clear FAILED) → compose (data.json, 1080×1920, validated against the subject schema) → render
(subprocess `build-video.sh`, timeout-guarded).

All OpenAI calls share one retry helper: exponential backoff + jitter, 3 attempts, only on
rate-limit / 5xx / timeout — never on 4xx.
