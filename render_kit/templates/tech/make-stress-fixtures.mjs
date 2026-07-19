#!/usr/bin/env node
// Regenerates test-stress-v.json / test-stress-h.json — 21 scenes, one per
// frame type, with content AT the layout limits documented in schema.json.
// These fixtures are the empirical proof behind every numeric limit: if a
// limit is loosened in schema.json, push the content here to the new limit
// and re-run populate + `npm run check` in both generated projects.
import { writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DUR = 6;

// Caption chunks stress the karaoke path: multi-word **emphasis**, 2-line
// wraps near the ~60-char chunk ceiling, punctuation-only tokens.
const CAPS = [
  'Stress testing **every single** frame type,',
  'with labels — and annotations at the limit.',
];

const scenes = [
  { id: 'st-cover', type: 'cover', eyebrow: '~/stress/limits-check-24', headline: 'Forty-four characters of **headline** text!!' },
  { id: 'st-stats', type: 'stats', stat: '1024', prefix: '~', suffix: 'ms', statLabel: 'characters of stat label right at the cap.' },
  { id: 'st-quote', type: 'quote', quote: 'A pull quote stretched to one hundred and thirty characters so the quote card proves it can wrap without ever clipping.', attribution: 'A. Long-Winded Attribution Name' },
  { id: 'st-bullets', type: 'bullet-list', title: 'Twenty-eight char list title', items: [
    'First **point** at thirty-eight characters',
    'Second point also at thirty-eight chars',
    'Third point stretched to the same cap!',
    'Fourth point stretched to the cap too!',
    'Fifth point rides the thirty-eight cap',
  ] },
  { id: 'st-concept', type: 'concept-card', term: 'Vector Embedding X', tagline: 'text → numbers → meaning..', definition: 'A one-sentence plain-language **definition** stretched right up to one hundred and ten characters to test.', glyph: '🧭' },
  { id: 'st-code', type: 'code-snippet', filename: 'stress_test_layout.py', language: 'python', code: [
    'docs = db.search(query, top_k=3)  # 48 chars!!',
    'context = "\\n".join(d.text for d in all_docs)',
    '# a comment line stretched to the char limit..',
    'answer = llm.ask(prompt=f"{context}{query}")',
    'for doc in docs: print(doc.score, doc.title)',
    'if not answer: raise ValueError("no answer")',
    'result = postprocess(answer, max_tokens=512)',
    'cache.store(query, result, ttl_seconds=3600)',
    'metrics.log("rag_call", latency_ms=elapsed)',
    'return result  # ten lines of code in total',
  ], highlightLines: [1, 4, 10] },
  { id: 'st-terminal', type: 'terminal', commands: [
    { cmd: 'pip install my-agent-kit --upgrade', output: ['Collecting my-agent-kit (2.4.1)...', '✓ Installed in 2.1s (14 packages)'] },
    { cmd: 'my-agent deploy --env prod --ap-1', output: ['Building bundle... done (3.2 MB).', '✓ Deployed to agent.example.com!!'] },
    { cmd: 'my-agent logs --tail 2 --compact!', output: ['12:01 refund approved in 204ms..', '12:02 lookup complete in 88ms...'] },
  ] },
  { id: 'st-chat', type: 'chat', title: 'Support thread, at limits', messages: [
    { role: 'user', text: 'I need a full refund for order #482 — it arrived two weeks late, help!' },
    { role: 'assistant', text: 'Orders reported within 30 days qualify — you are **eligible** for one.' },
    { role: 'user', text: 'Great — how long until the money is actually back on my card, then?' },
    { role: 'assistant', text: 'Refunds settle in 3-5 business days. Submitted now — ref **RF-2291**.' },
  ] },
  { id: 'st-pipeline', type: 'pipeline', title: 'Pipeline title at the cap.', nodes: [
    { label: 'Query Parser', sublabel: 'tokenize + clean', glyph: '❓' },
    { label: 'Retriever V2', sublabel: 'vector search..', glyph: '🔍' },
    { label: 'Vector Store', sublabel: 'top-k documents', glyph: '🗄️' },
    { label: 'Re-Ranker XL', sublabel: 'cross-encoder..', glyph: '⚖️' },
    { label: 'LLM Composer', sublabel: 'grounded answer', glyph: '🧠' },
  ], highlightNode: 2 },
  { id: 'st-compare', type: 'comparison', leftTitle: 'RAG Pipelines!', rightTitle: 'Fine-tuning XL', leftItems: [
    'Fresh data at query time now', 'Cheap to update the indexes.', 'Citable sources, every reply',
  ], rightItems: [
    'Bakes style and skills in :)', 'Costly retraining every run.', 'Frozen knowledge at cut-off.',
  ], verdict: 'Facts → RAG. Behavior → fine-tune. Use **both** wisely.' },
  { id: 'st-roadmap', type: 'roadmap', title: 'Roadmap title at the cap!', steps: [
    { label: 'Python foundations 1', sublabel: 'three whole months...' },
    { label: 'APIs and prompting 2', sublabel: 'LLM 101 fundamentals.' },
    { label: 'RAG and embeddings 3', sublabel: 'vector search basics.' },
    { label: 'Agents and tooling 4', sublabel: 'function calling 101.' },
    { label: 'Evals and shipping 5', sublabel: 'observability suite..' },
  ] },
  { id: 'st-stack', type: 'stack-layers', title: 'Stack title at the cap!!', layers: [
    { label: 'Product and UI Lay', sublabel: 'what users touch, daily.' },
    { label: 'Orchestration Tier', sublabel: 'agents · RAG · evals · ci' },
    { label: 'Model API Gateway!', sublabel: 'GPT · Claude · Llama · +2' },
    { label: 'Inference Cluster!', sublabel: 'GPUs · serving · scaling' },
    { label: 'Storage and Cache!', sublabel: 'vectors · blobs · queues' },
  ], highlightIndex: 0, annotation: 'you work RIGHT here!!!' },
  { id: 'st-vector', type: 'vector-space', title: 'Vector plot title at cap', clusterLabels: ['billing document 18', 'api reference docs.', 'faq and onboarding.'], queryLabel: 'your question!' },
  { id: 'st-neural', type: 'neural-net', title: 'Network title at the cap', layerLabels: ['input tokens', 'hidden layer', 'output logit'], outputLabel: 'prediction :)' },
  { id: 'st-task', type: 'task-breakdown', title: 'Task list title at cap!!', goal: 'Plan a 3-city product launch', subtasks: [
    { label: 'Pick three candidate dates', sublabel: 'check the calendars' },
    { label: 'Compare venues and prices!', sublabel: 'web search + sheets' },
    { label: 'Draft the agenda documents', sublabel: 'one-day format only' },
    { label: 'Send invites to attendees.', sublabel: 'email tool, batched' },
    { label: 'Book travel for the crew..', sublabel: 'flights and hotels.' },
  ] },
  { id: 'st-thought', type: 'thought-chain', question: 'Should we refund order #482 today, now?', thoughts: [
    'My first goal is to check the refund policy before promising anything.',
    'Policy says thirty days maximum — this order is only twelve days old.',
    'Customer is therefore eligible; the next step is issuing the refund.',
    'I should also flag the damaged-box photo for the warehouse QA team..',
  ], conclusion: 'Refund **approved** — well within the thirty-day window, reference RF-2291.' },
  { id: 'st-tool', type: 'tool-use', title: 'Tool rig title at limit!', agentLabel: 'Support Agent', tools: [
    { glyph: '🔍', label: 'Web Searcher', sublabel: 'live info feed' },
    { glyph: '🧮', label: 'Calculator 2', sublabel: 'exact math ops' },
    { glyph: '🗄️', label: 'Database Hub', sublabel: 'your data lake' },
    { glyph: '✉️', label: 'Email Sender', sublabel: 'take action!!!' },
    { glyph: '🗓️', label: 'Calendar Ops', sublabel: 'availability..' },
  ] },
  { id: 'st-memory', type: 'memory', title: 'Memory title at the cap!', shortItems: [
    'User asked about order #482 refunds.', 'Wants a refund and an apology note.', 'Shared a photo of the damaged boxes.',
  ], longItems: [
    'Prefers email replies over the phone', 'VIP customer since early 2023 (Q1)..', 'Ships everything to the Denver office',
  ] },
  { id: 'st-reflect', type: 'reflection-loop', title: 'Reflection loop at cap!!', steps: [
    { label: 'Attempt!!', glyph: '✍️' }, { label: 'Check :))', glyph: '🔎' }, { label: 'Fix bugs!', glyph: '🔧' }, { label: 'Retry it!', glyph: '🔁' },
  ], failLabel: 'error found!', passLabel: 'all fixed :)' },
  { id: 'st-mcp', type: 'mcp-hub', title: 'MCP hub title at limit!!', agentLabel: 'Your Agent', hubLabel: 'MCP', apps: [
    { glyph: '💬', label: 'Slack Pro!' }, { glyph: '📁', label: 'DriveSync!' }, { glyph: '💳', label: 'StripePay!' }, { glyph: '🗓️', label: 'Calendars!' }, { glyph: '📧', label: 'Email Hub!' },
  ] },
  { id: 'st-cta', type: 'cta', headline: 'Forty-four characters of **headline** text!!', subheadline: 'A secondary line stretched right up to the sixty char cap..' },
];

for (const orientation of ['vertical', 'horizontal']) {
  const suffix = orientation === 'vertical' ? 'v' : 'h';
  const data = {
    config: {
      slug: `test-tech-stress-${suffix}`,
      topic: `tech template stress test (${orientation})`,
      totalDuration: scenes.length * DUR,
      orientation,
    },
    scenes: scenes.map((s, i) => ({
      ...s,
      start: i * DUR,
      duration: DUR,
      transition: 'fade',
      captions: s.captions ?? CAPS,
    })),
  };
  const out = join(__dirname, `test-stress-${suffix}.json`);
  writeFileSync(out, JSON.stringify(data, null, 2) + '\n');
  console.log('wrote', out);
}
