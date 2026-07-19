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

  // SFX tracks start at 40, numbered by a counter over sfx-bearing scenes only —
  // indexing by scene position would collide with the narration track (30) once
  // the video has 11+ scenes (long-form horizontal easily exceeds that).
  let sfxCounter = 0;
  scenes.forEach((scene, i) => {
    if (scene.sfx == null) return; // missing or null = no sfx; omit element entirely
    parts.push(`      <audio
        id="el-sfx-${i}"
        src="${scene.sfx}"
        data-start="${scene.start}"
        data-duration="${scene.duration}"
        data-track-index="${40 + sfxCounter++}"
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

// scene.captionTiming ([{text,start,end,words}], scene-local seconds) is
// normally written by the alignment step from real Whisper word timestamps.
// When it's absent (test-*.json fixtures, or a scene authored before running
// that pipeline), fall back to evenly spacing captions[] across the scene so
// every frame template can rely on one payload shape regardless of source.
// Every chunk is guaranteed a words[] array ({text,start,end} per word) for the
// karaoke highlight — synthesized by evenly dividing the chunk span when the
// aligner didn't provide real per-word timestamps.
function synthesizeWords(text, start, end) {
  // Re-mark multi-word **emphasis** spans word-by-word so each token carries
  // balanced markers ("**a b**" → "**a** **b**") — same rule as the aligner.
  const marked = String(text).replace(/\*\*(.+?)\*\*/g, (_m, inner) =>
    inner.split(/\s+/).map((w) => '**' + w + '**').join(' ')
  );
  const tokens = marked.split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return [];
  const step = Math.max(0, end - start) / tokens.length;
  return tokens.map((w, i) => ({
    text: w,
    start: Number((start + i * step).toFixed(3)),
    end: Number((start + (i + 1) * step).toFixed(3)),
  }));
}

function resolveCaptionTiming(scene) {
  if (Array.isArray(scene.captionTiming)) {
    return scene.captionTiming.map((c) =>
      Array.isArray(c.words) && c.words.length > 0
        ? c
        : { ...c, words: synthesizeWords(c.text, c.start, c.end) }
    );
  }
  const chunks = Array.isArray(scene.captions) ? scene.captions : [];
  if (chunks.length === 0) return [];
  const dur = scene.duration || 4;
  const step = dur / chunks.length;
  return chunks.map((text, i) => {
    const start = Number(Math.min(dur, 0.3 + i * step).toFixed(3));
    const end = Number(Math.min(dur, 0.3 + (i + 1) * step).toFixed(3));
    return { text, start, end, words: synthesizeWords(text, start, end) };
  });
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
const orientation = config.orientation ?? 'vertical';
const width  = config.width  ?? (orientation === 'horizontal' ? 1920 : 1080);
const height = config.height ?? (orientation === 'horizontal' ? 1080 : 1920);
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
    width, height, orientation,
    // Karaoke spoken-word highlight color — deliberately independent of each
    // frame's accent so captions read as one system across the video.
    capHighlight: config.capHighlight ?? '#FFD24A',
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

// Write meta.json — description/hashtags/tags (from the scene-split metadata
// step) feed a future YouTube-upload step's defaults; omitted entirely when
// absent so hand-authored videos keep a minimal meta.json.
writeFileSync(join(outDir, 'meta.json'), JSON.stringify({
  id: config.slug,
  name: config.topic,
  createdAt: new Date().toISOString(),
  ...(config.description ? { description: config.description } : {}),
  ...(Array.isArray(config.hashtags) && config.hashtags.length ? { hashtags: config.hashtags } : {}),
  ...(Array.isArray(config.tags) && config.tags.length ? { tags: config.tags } : {}),
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

console.log(`\n✓ Generated: videos/${config.slug}/ (${orientation}, ${width}×${height})`);
console.log(`\nNext steps:`);
console.log(`  cd videos/${config.slug} && npm run check`);
console.log(`  npm run render`);
