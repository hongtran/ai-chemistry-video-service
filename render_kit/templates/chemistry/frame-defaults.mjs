// Shared source of truth for the chemistry template's scene shape — the set
// of valid frame types, their per-type fields, and structural validation.
// Consumed by both populate.js (rendering) and scripts/generate-script.mjs
// (LLM script generation) so the two never drift apart.

export const SHARED = { bg: '#0B0E14', fg: '#FFFFFF', accent: '#FFD60A', eyebrow: '', headline: '', captions: [] };

export const FRAME_DEFAULTS = {
  cover:               { ...SHARED, headlineFontSize: '120px' },
  stats:               { ...SHARED, bg: '#FFD60A', fg: '#0B0E14', accent: '#0B0E14', stat: '', statLabel: '' },
  // quote / element-card / reaction-equation share a distinct "editorial"
  // look (cream backdrop, warm gold accent, serif type) — a deliberately
  // different register from the bold dark cover/stat frames, kept as their
  // default palette (still fully overridable per scene like every type).
  quote:               { ...SHARED, bg: '#EDE8E0', fg: '#1A1A1A', accent: '#B8A98C', quoteFontSize: '56px' },
  diagram:             { ...SHARED, items: [] },
  cta:                 { ...SHARED },
  atom:                { ...SHARED, bg: '#1ABC9C', fg: '#0B0E14', accent: '#0B0E14', title: '' },
  'element-card':      { ...SHARED, bg: '#EDE8E0', fg: '#1A1A1A', accent: '#B8A98C', color: '#E2DBD1' },
  'reaction-equation': { ...SHARED, bg: '#EDE8E0', fg: '#1A1A1A', accent: '#B8A98C', r2: '', r3: '', p2: '', label: '', eqTitle: '' },
  molecule:            { ...SHARED, bg: '#1ABC9C', fg: '#0B0E14', accent: '#0B0E14', centerSymbol: '', centerColor: '' },
  'ph-bar':            { ...SHARED, bg: '#34C759', fg: '#0B0E14', accent: '#0B0E14', title: '' },
  'orbital-overlap':   { ...SHARED, bg: '#1ABC9C', fg: '#0B0E14', accent: '#0B0E14' },
  'bond-comparison':   { ...SHARED },
  'particle-count':    { ...SHARED, bg: '#FF3B30', accent: '#FFD60A', particleLabel: 'H⁺', particleColor: '#FFFFFF', targetCount: 12, containerLabel: '', result: '' },
  'tug-of-war':        { ...SHARED, bg: '#1ABC9C', fg: '#0B0E14', accent: '#0B0E14', leftSymbol: 'H', rightSymbol: 'H', leftColor: '#0B0E14', rightColor: '#0B0E14', mode: 'equal', electrons: 2, verb: '' },
};

export const VALID_TYPES = Object.keys(FRAME_DEFAULTS);

// Fields that distinguish a type from the shared base (bg/fg/accent/eyebrow/
// headline/captions) — i.e. what a scene of this type actually needs to set
// beyond the common fields, used to prompt/validate content generation.
export function typeSpecificFields(type) {
  return Object.keys(FRAME_DEFAULTS[type] ?? {}).filter((k) => !(k in SHARED));
}

// Every {{token}} each frames/*.html actually substitutes, beyond the shared
// base (bg/fg/accent/eyebrow/headline/captions/id/width/height/duration/
// captionTiming) — the authoritative field list per type, sourced directly
// from grepping {{...}} usage in each template (NOT from FRAME_DEFAULTS,
// which only covers cosmetic rendering defaults and omits fields that have
// no safe default, like ph-bar's targetPh or molecule's angle).
export const TYPE_CONTENT_FIELDS = {
  cover: [],
  stats: ['stat', 'statLabel'],
  quote: ['quote', 'attribution', 'quoteFontSize'],
  diagram: ['items'],
  cta: ['subheadline'],
  atom: ['symbol', 'shell1', 'shell2', 'shell3', 'title'],
  'element-card': ['symbol', 'atomicNumber', 'atomicMass', 'name', 'color'],
  'reaction-equation': ['r1', 'r2', 'r3', 'p1', 'p2', 'label', 'eqTitle'],
  molecule: ['leftSymbol', 'rightSymbol', 'leftColor', 'rightColor', 'centerSymbol', 'centerColor', 'angle', 'bondOrder', 'title'],
  'ph-bar': ['targetPh', 'title'],
  'orbital-overlap': ['atom1', 'atom2'],
  'bond-comparison': ['ionicAtom1', 'ionicAtom2', 'covalentAtom1', 'covalentAtom2'],
  'particle-count': ['containerLabel', 'result', 'targetCount', 'particleLabel', 'particleColor'],
  'tug-of-war': ['leftSymbol', 'rightSymbol', 'leftColor', 'rightColor', 'mode', 'electrons', 'verb'],
};

// Types safe to use in a 9:16 vertical composition. Retained for back-compat;
// every type now also ships a 16:9 layout — see SUPPORTED_ORIENTATIONS.
export const VERTICAL_TYPES = Object.keys(TYPE_CONTENT_FIELDS);

// Canvas orientations. populate.js resolves width/height from
// config.orientation ('vertical' → 1080×1920, 'horizontal' → 1920×1080) and
// injects an {{orientation}} token every frame uses as a root class to switch
// between its two hand-tuned layouts.
export const ORIENTATIONS = ['vertical', 'horizontal'];

// Which orientations each type supports. All chemistry frames now ship with
// both layouts authored; kept as a map so a future type can opt out of one
// orientation without new machinery.
export const SUPPORTED_ORIENTATIONS = Object.fromEntries(
  VALID_TYPES.map((t) => [t, [...ORIENTATIONS]])
);

// Which of a type's content fields actually break/degrade visibly if left
// empty. Not derivable from FRAME_DEFAULTS alone — a blank default means
// different things for different fields (atom's title: '' means "always
// fill this in", reaction-equation's r2: '' means "genuinely optional,
// not every reaction has a second reactant") — so this is a direct,
// hand-judged list rather than a generic rule. Fields left out either have
// a real non-blank default (tug-of-war's leftSymbol: 'H') or are legitimately
// optional per-scene (molecule's centerSymbol/centerColor, only used for a
// 3-atom molecule).
export const REQUIRED_CONTENT_FIELDS = {
  cover: [],
  stats: ['stat', 'statLabel'],
  quote: ['quote', 'attribution'],
  diagram: ['items'],
  cta: ['subheadline'],
  atom: ['symbol', 'shell1', 'shell2', 'shell3', 'title'],
  'element-card': ['symbol', 'atomicNumber', 'atomicMass', 'name'],
  'reaction-equation': ['r1', 'p1'],
  molecule: ['leftSymbol', 'rightSymbol', 'leftColor', 'rightColor', 'angle', 'bondOrder', 'title'],
  'ph-bar': ['targetPh', 'title'],
  'orbital-overlap': ['atom1', 'atom2'],
  'bond-comparison': ['ionicAtom1', 'ionicAtom2', 'covalentAtom1', 'covalentAtom2'],
  'particle-count': ['containerLabel', 'result', 'targetCount'],
  'tug-of-war': ['verb'],
};

export function requiredContentFields(type) {
  return REQUIRED_CONTENT_FIELDS[type] ?? [];
}

export function validateData(data) {
  const errors = [];
  if (!data.config?.slug) errors.push('config.slug is required');
  if (!data.config?.totalDuration) errors.push('config.totalDuration is required');
  if (data.config?.orientation && !ORIENTATIONS.includes(data.config.orientation))
    errors.push(`config.orientation "${data.config.orientation}" is invalid — must be one of: ${ORIENTATIONS.join(', ')}`);
  if (!Array.isArray(data.scenes) || data.scenes.length === 0)
    errors.push('scenes[] must be a non-empty array');
  for (const scene of data.scenes ?? []) {
    if (!scene.id) errors.push('a scene is missing id');
    if (!VALID_TYPES.includes(scene.type))
      errors.push(`scene "${scene.id}" has invalid type "${scene.type}" — must be one of: ${VALID_TYPES.join(', ')}`);
    if (scene.start === undefined) errors.push(`scene "${scene.id}" missing start`);
    if (!scene.duration) errors.push(`scene "${scene.id}" missing duration`);
  }
  return errors;
}
