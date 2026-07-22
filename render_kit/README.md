# render_kit

Self-contained rendering toolkit for the video service. The Phase-2
render step (`app/pipeline/steps/render.py`) shells out to
`render_kit/scripts/build-video.sh` — this directory holds everything that script
needs, so the project can be cloned and rendered without the original
`my-video-hyperframes` repo.

Vendored from `my-video-hyperframes` (copy — re-sync from there if the template
changes).

## Contents

```
scripts/build-video.sh          entry point the service calls
templates/lab-management/       ISO/IEC 17025 subject (also: templates/tech/)
  populate.js                   turns a data.json into a renderable videos/<slug>/ project
  frame-defaults.mjs            valid frame types + per-type defaults + validation
  frames/*.html                 per-scene-type templates (cover, equipment-register, calibration-cert, …)
  index.template.html           root composition template
  schema.json                   data.json schema
  stubs/silence.mp3             silence fallback so lint passes before real audio exists
  data-*.json / test-*.json     small example inputs (reference / manual testing)
package.json                    copied into each generated videos/<slug>/ project
```

## Prerequisites

- **Node 18+** and **npm**
- **Internet access** on first run — the hyperframes CLI is fetched on demand via
  `npx hyperframes@0.7.18` (not vendored here). It downloads a headless Chromium
  and manages ffmpeg for the render.

## Usage

The service invokes this automatically. To render manually / verify the kit:

```bash
# from the project root
bash render_kit/scripts/build-video.sh \
  render_kit/templates/lab-management/data.json \
  --out_path /tmp/render-check.mp4
```

With no `--audio_file`, `populate.js` stubs silent audio, so this produces a real
(silent) mp4 and proves the toolkit is complete. Pass
`--audio_file <narration.mp3>` to stage real narration (as the service does).

Generated projects land in `render_kit/videos/<slug>/` and are gitignored.
