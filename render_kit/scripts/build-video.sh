#!/usr/bin/env bash
set -euo pipefail

# Build a video end-to-end from a subject template data.json:
#   1. stage a provided full-script narration file into videos/<slug>/
#   2. populate the data.json into videos/<slug>/
#   3. run npm run check (lint + validate + inspect) in that project
#   4. render the composition to MP4 (default path, or --out_path if given)
#   5. print the path to the rendered video on stdout
#
# Usage: scripts/build-video.sh <path-to-data.json> [--template <template-name>] [--out_path <output-video-path>] [--audio_file <narration-audio-file>]
#
# --audio_file points at the single full-script narration file (as referenced
# by data.json's config.audio, e.g. "assets/tts/narration.mp3"). It's staged
# to that path before populate.js runs, so its silence-stub fallback never
# fires for it. config.audio and each scene's start/duration/captionTiming
# are normally produced beforehand by:
#   node scripts/generate-captions.mjs <audio-file> <captions.json>
#   node scripts/align-captions.mjs    <data.json>  <captions.json>

INVOKE_DIR="$(pwd)"
OUT_PATH=""
AUDIO_FILE=""
TEMPLATE="lab-management"
POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    --template)
      TEMPLATE="${2:-}"
      if [ -z "$TEMPLATE" ]; then
        echo "Error: --template requires a value" >&2
        exit 1
      fi
      shift 2
      ;;
    --template=*)
      TEMPLATE="${1#*=}"
      shift
      ;;
    --out_path)
      OUT_PATH="${2:-}"
      if [ -z "$OUT_PATH" ]; then
        echo "Error: --out_path requires a value" >&2
        exit 1
      fi
      shift 2
      ;;
    --out_path=*)
      OUT_PATH="${1#*=}"
      shift
      ;;
    --audio_file)
      AUDIO_FILE="${2:-}"
      if [ -z "$AUDIO_FILE" ]; then
        echo "Error: --audio_file requires a value" >&2
        exit 1
      fi
      shift 2
      ;;
    --audio_file=*)
      AUDIO_FILE="${1#*=}"
      shift
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [ ${#POSITIONAL[@]} -ne 1 ]; then
  echo "Usage: $0 <path-to-data.json> [--template <template-name>] [--out_path <output-video-path>] [--audio_file <narration-audio-file>]" >&2
  exit 1
fi

DATA_JSON="${POSITIONAL[0]}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$DATA_JSON" ]; then
  echo "Error: data file not found: $DATA_JSON" >&2
  exit 1
fi

POPULATE_SCRIPT="$ROOT_DIR/templates/$TEMPLATE/populate.js"
if [ ! -f "$POPULATE_SCRIPT" ]; then
  echo "Error: template populate script not found: $POPULATE_SCRIPT" >&2
  exit 1
fi

if [ -n "$AUDIO_FILE" ] && [ ! -f "$AUDIO_FILE" ]; then
  echo "Error: --audio_file not found: $AUDIO_FILE" >&2
  exit 1
fi

SLUG="$(node -e "console.log(JSON.parse(require('fs').readFileSync(process.argv[1],'utf8')).config.slug ?? '')" "$DATA_JSON")"
if [ -z "$SLUG" ]; then
  echo "Error: config.slug missing in $DATA_JSON" >&2
  exit 1
fi

VIDEO_DIR="$ROOT_DIR/videos/$SLUG"

if [ -n "$AUDIO_FILE" ]; then
  AUDIO_REL="$(node -e "console.log(JSON.parse(require('fs').readFileSync(process.argv[1],'utf8')).config.audio ?? '')" "$DATA_JSON")"
  if [ -z "$AUDIO_REL" ]; then
    echo "Error: --audio_file given but config.audio missing in $DATA_JSON (run scripts/align-captions.mjs first)" >&2
    exit 1
  fi
  echo "==> Staging narration audio from $AUDIO_FILE to $AUDIO_REL" >&2
  mkdir -p "$VIDEO_DIR/$(dirname "$AUDIO_REL")"
  cp "$AUDIO_FILE" "$VIDEO_DIR/$AUDIO_REL"
fi

echo "==> Populating template ($TEMPLATE, slug: $SLUG)" >&2
node "$POPULATE_SCRIPT" "$DATA_JSON" >&2

echo "==> Checking composition" >&2
(cd "$VIDEO_DIR" && npm run check) >&2

echo "==> Rendering video" >&2
if [ -n "$OUT_PATH" ]; then
  # Resolve relative to the directory the script was invoked from (not VIDEO_DIR).
  case "$OUT_PATH" in
    /*) OUTPUT_PATH="$OUT_PATH" ;;
    *)  OUTPUT_PATH="$INVOKE_DIR/$OUT_PATH" ;;
  esac
  mkdir -p "$(dirname "$OUTPUT_PATH")"
else
  OUTPUT_PATH="$VIDEO_DIR/renders/${SLUG}.mp4"
fi

(cd "$VIDEO_DIR" && npx --yes hyperframes@0.7.18 render -o "$OUTPUT_PATH") >&2

if [ ! -f "$OUTPUT_PATH" ]; then
  echo "Error: expected render output not found at $OUTPUT_PATH" >&2
  exit 1
fi

echo "$OUTPUT_PATH"
