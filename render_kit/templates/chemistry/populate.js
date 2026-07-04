#!/usr/bin/env node
import { readFileSync, writeFileSync, mkdirSync, copyFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { FRAME_DEFAULTS, validateData } from './frame-defaults.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');

function substituteTokens(template, data) {
  return template.replace(/\{\{(\w+)\}\}/g, (_match, key) => {
    if (data[key] === undefined || data[key] === null) return '';
    const val = data[key];
    if (typeof val === 'object') return JSON.stringify(val);
    return String(val);
  });
}

function generateSceneClips(scenes) {
  return scenes.map((scene, i) => {
    const trackIndex = i % 2;
    return `      <div
        id="el-${scene.id}"
        class="clip"
        data-composition-id="${scene.id}"
        data-composition-src="compositions/frames/${scene.id}.html"
        data-start="${scene.start}"
        data-duration="${scene.duration}"
        data-track-index="${trackIndex}"
      ></div>`;
  }).join('\n\n');
}

function generateAudio(config, scenes) {
  const parts = [];

  if (config.bgm) {
    parts.push(`      <audio
        id="el-bgm"
        src="${config.bgm}"
        data-start="0"
        data-duration="${config.totalDuration}"
        data-track-index="11"
        data-volume="0.9"
      ></audio>`);
  }

  scenes.forEach((scene, i) => {
    if (scene.sfx == null) return; // missing or null = no sfx; omit element entirely
    parts.push(`      <audio
        id="el-sfx-${i}"
        src="${scene.sfx}"
        data-start="${scene.start}"
        data-duration="${scene.duration}"
        data-track-index="${20 + i}"
        data-volume="0.35"
      ></audio>`);
  });

  // Single full-script narration track (replaces per-scene TTS files).
  // Scene-level caption timing (captionTiming, see resolveCaptionTiming) is
  // derived from this track's real word timestamps by scripts/align-captions.mjs.
  if (config.audio) {
    parts.push(`      <audio
        id="el-narration"
        src="${config.audio}"
        data-start="0"
        data-duration="${config.totalDuration}"
        data-track-index="30"
        data-volume="1.0"
      ></audio>`);
  }

  return parts.join('\n\n');
}

// scene.captionTiming ([{text,start,end}], scene-local seconds) is normally
// written by scripts/align-captions.mjs from real Whisper word timestamps.
// When it's absent (test-*.json fixtures, or a scene authored before running
// that pipeline), fall back to the old evenly-spaced approximation so every
// frame template can rely on one payload shape regardless of source.
function resolveCaptionTiming(scene) {
  if (Array.isArray(scene.captionTiming)) return scene.captionTiming;
  const chunks = Array.isArray(scene.captions) ? scene.captions : [];
  if (chunks.length === 0) return [];
  const dur = scene.duration || 4;
  const step = dur / chunks.length;
  return chunks.map((text, i) => ({
    text,
    start: Number(Math.min(dur, 0.3 + i * step).toFixed(3)),
    end: Number(Math.min(dur, 0.3 + (i + 1) * step).toFixed(3)),
  }));
}

function generateTransitions(scenes, width) {
  const lines = [];
  for (let i = 0; i < scenes.length - 1; i++) {
    const curr = scenes[i], next = scenes[i + 1], at = next.start;
    const type = curr.transition ?? 'cut';
    if (type === 'fade') {
      lines.push(`        tl.to("#el-${curr.id}", { opacity: 0, duration: 0.2, ease: "power2.inOut" }, ${at});`);
      lines.push(`        tl.fromTo("#el-${next.id}", { opacity: 0 }, { opacity: 1, duration: 0.2, ease: "power2.inOut" }, ${at});`);
    } else if (type === 'slide') {
      lines.push(`        tl.to("#el-${curr.id}", { x: ${width}, duration: 0.25, ease: "power3.inOut" }, ${at});`);
      lines.push(`        tl.fromTo("#el-${next.id}", { x: ${-width} }, { x: 0, duration: 0.25, ease: "power3.inOut" }, ${at});`);
    } else if (type === 'punch') {
      lines.push(`        tl.fromTo("#el-${next.id}", { scale: 1.12 }, { scale: 1, duration: 0.22, ease: "back.out(2)" }, ${at});`);
    } else if (type === 'shake') {
      lines.push(`        tl.fromTo("#el-${next.id}", { x: -24 }, { x: 0, duration: 0.18, ease: "elastic.out(1,0.4)" }, ${at});`);
    }
    // 'cut' → hard cut, nothing emitted
  }
  return lines.join('\n');
}

// ── Main ──────────────────────────────────────────────────────────────────────

const dataPath = process.argv[2] ?? join(__dirname, 'data.json');
let data;
try {
  data = JSON.parse(readFileSync(dataPath, 'utf8'));
} catch (e) {
  console.error(`Error reading ${dataPath}: ${e.message}`);
  process.exit(1);
}

const errors = validateData(data);
if (errors.length) {
  console.error('Validation errors:\n' + errors.map(e => `  • ${e}`).join('\n'));
  process.exit(1);
}

const { config, scenes } = data;
const width  = config.width  ?? 1080;
const height = config.height ?? 1920;
const outDir = join(ROOT, 'videos', config.slug);
const framesDir = join(outDir, 'compositions', 'frames');

mkdirSync(framesDir, { recursive: true });
mkdirSync(join(outDir, 'assets', 'bgm'), { recursive: true });
mkdirSync(join(outDir, 'assets', 'tts'), { recursive: true });
mkdirSync(join(outDir, 'assets', 'sfx'), { recursive: true });

// Generate per-scene frame files
for (const scene of scenes) {
  const frameTpl = readFileSync(join(__dirname, 'frames', `${scene.type}.html`), 'utf8');
  const tokenData = {
    width, height,
    ...FRAME_DEFAULTS[scene.type],
    ...scene,
    captionTiming: resolveCaptionTiming(scene),
    ...(scene.overrides ?? {}),
  };
  writeFileSync(join(framesDir, `${scene.id}.html`), substituteTokens(frameTpl, tokenData));
}

// Generate root index.html
const indexTpl = readFileSync(join(__dirname, 'index.template.html'), 'utf8');
const indexHtml = substituteTokens(
  indexTpl
    .replace('<!-- SCENES_PLACEHOLDER -->', generateSceneClips(scenes))
    .replace('<!-- AUDIO_PLACEHOLDER -->', generateAudio(config, scenes))
    .replace('// TRANSITIONS_PLACEHOLDER', generateTransitions(scenes, width)),
  { totalDuration: config.totalDuration, width, height }
);
writeFileSync(join(outDir, 'index.html'), indexHtml);

// Write meta.json
writeFileSync(join(outDir, 'meta.json'), JSON.stringify({
  id: config.slug,
  name: config.topic,
  createdAt: new Date().toISOString(),
}, null, 2));

// Copy package.json from repo root
copyFileSync(join(ROOT, 'package.json'), join(outDir, 'package.json'));

// Stub missing audio files with a silence placeholder so lint passes before
// real TTS/BGM is generated. Existing files (real audio) are never overwritten.
const STUB_AUDIO = join(__dirname, 'stubs', 'silence.mp3');
function stubAudioIfMissing(relPath) {
  const dest = join(outDir, relPath);
  if (!existsSync(dest) && existsSync(STUB_AUDIO)) {
    copyFileSync(STUB_AUDIO, dest);
  }
}

if (config.bgm)   stubAudioIfMissing(config.bgm);
if (config.audio) stubAudioIfMissing(config.audio);
stubAudioIfMissing('assets/sfx/silence.mp3');
for (const scene of scenes) {
  if (scene.sfx) stubAudioIfMissing(scene.sfx);
}

console.log(`\n✓ Generated: videos/${config.slug}/`);
console.log(`\nNext steps:`);
console.log(`  cd videos/${config.slug} && npm run check`);
console.log(`  npm run render`);
