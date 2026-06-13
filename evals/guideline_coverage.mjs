// kaixn — guideline coverage eval (Claude Workflow)
//
// Ground truth = a high-standard repo's WRITTEN coding standards (CONTRIBUTING,
// coding-style docs, conventions, style guides) — denser and more authoritative
// than sparse PR comments. Measures: what fraction of the standard's rules does
// our axis catalog cover, and what are we missing?
//
//   Discover  — find the repo's guideline/standard docs
//   Extract   — per doc, pull each discrete coding rule (tag language-specific)
//   Map       — each rule → catalog axis or MISSING (coverage over AGNOSTIC rules)
//   Augment   — append agnostic misses as new axes; loop
//   Synthesize— coverage report + the standards we don't yet encode
//
// args: { repo: "owner/name", maxDocs?: 8, coverageTarget?: 0.9, maxRounds?: 2 }

export const meta = {
  name: 'guideline-coverage',
  description: 'Measure how much of a high-standard repo\'s written coding guidelines our axis catalog covers',
  phases: [
    { title: 'Discover', detail: 'find the repo\'s guideline/standard docs' },
    { title: 'Extract',  detail: 'per doc: pull discrete coding rules' },
    { title: 'Map',      detail: 'map each rule to a catalog axis or MISSING' },
    { title: 'Augment',  detail: 'append agnostic misses as new axes' },
    { title: 'Synthesize', detail: 'coverage + uncovered standards' },
  ],
}

const repo = args?.repo ?? 'torvalds/linux'
const MAX_DOCS = args?.maxDocs ?? 8
const TARGET = args?.coverageTarget ?? 0.9
const MAX_ROUNDS = args?.maxRounds ?? 2

const DOCS_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['docs'],
  properties: { docs: { type: 'array', items: {
    type: 'object', required: ['path'], properties: {
      path: { type: 'string' }, why: { type: 'string' } } } } },
}

const RULES_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['rules'],
  properties: { rules: { type: 'array', items: {
    type: 'object', additionalProperties: false, required: ['rule', 'lang_specific'],
    properties: {
      rule: { type: 'string' },
      category: { type: 'string' },
      lang_specific: { type: 'boolean' },  // true = C/Swift-ism not portable to a Python catalog
    } } } },
}

const MAP_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['rows'],
  properties: { rows: { type: 'array', items: {
    type: 'object', additionalProperties: false,
    required: ['rule', 'lang_specific', 'axis', 'covered'],
    properties: {
      rule: { type: 'string' },
      lang_specific: { type: 'boolean' },
      axis: { type: 'string' },              // catalog id or "MISSING"
      covered: { type: 'boolean' },          // axis != MISSING
      proposed_axis: { type: ['object', 'null'] },
      note: { type: 'string' },
    } } } },
}

// ── Discover guideline docs ───────────────────────────────────────────────
phase('Discover')
const discovered = await agent(
  `Find the WRITTEN coding-standard / contribution / style-guide documents in ` +
  `the repo ${repo}. Use the gh CLI to browse likely locations: root ` +
  `(CONTRIBUTING*, CODING*, STYLE*), docs/, Documentation/ (e.g. ` +
  `Documentation/process/ for the kernel), .github/, and any *.rst/*.md whose ` +
  `name contains style/guideline/convention/coding/contributing. ` +
  `Useful: \`gh api repos/${repo}/contents/<dir>\` to list, ` +
  `\`gh api repos/${repo}/git/trees/HEAD?recursive=1 --jq '.tree[].path'\` (may be ` +
  `large — grep it). Return up to ${MAX_DOCS} of the MOST authoritative coding-standard ` +
  `docs (prefer coding-style/conventions over generic contributing boilerplate).`,
  { label: `discover-docs:${repo}`, phase: 'Discover', schema: DOCS_SCHEMA })

const docs = (discovered?.docs ?? []).slice(0, MAX_DOCS)
if (!docs.length) {
  return { error: `no guideline docs found in ${repo}` }
}
log(`found ${docs.length} guideline docs`)

// ── Extract rules per doc (fan-out) ───────────────────────────────────────
phase('Extract')
const perDoc = await parallel(docs.map(d => () => agent(
  `Read the coding-standard document at ${repo}:${d.path} — fetch it with ` +
  `\`gh api repos/${repo}/contents/${d.path} -H "Accept: application/vnd.github.raw"\`. ` +
  `Extract EACH discrete, checkable coding rule/standard it states (one per item — ` +
  `split compound rules). For each, set lang_specific=true if it is a C/Swift/` +
  `language-specific mechanic that would NOT apply to a Python codebase (e.g. ` +
  `"use tabs not spaces", "K&R braces", "no typedefs", "kmalloc over malloc"); ` +
  `false if it is a language-agnostic engineering standard (naming, function length, ` +
  `commenting rationale, error handling, locking/concurrency discipline, commit ` +
  `hygiene, testing, security). Give a short category.`,
  { label: `extract:${d.path}`, phase: 'Extract', schema: RULES_SCHEMA })
  .then(r => r?.rules ?? [])))
const allRules = perDoc.filter(Boolean).flat()
log(`extracted ${allRules.length} rules`)

// ── Map → coverage, looped ────────────────────────────────────────────────
const coverageByRound = []
const addedAxes = []
let lastRows = []

for (let round = 1; round <= MAX_ROUNDS; round++) {
  phase('Map')
  // chunk rules so each mapping agent stays focused
  const chunks = []
  for (let i = 0; i < allRules.length; i += 25) chunks.push(allRules.slice(i, i + 25))
  const mapped = await parallel(chunks.map((chunk, ci) => () => agent(
    `Map each of these coding-standard rules to our axis catalog. Read ` +
    `docs/axis-catalog.yaml. For EACH rule:\n` +
    `- axis: the catalog axis id that already covers it, or "MISSING"\n` +
    `- covered: axis != "MISSING"\n` +
    `- proposed_axis: if MISSING AND not lang_specific, {name,category,rule,enforce}; else null\n` +
    `Keep the lang_specific flag from the input.\n\n` +
    `RULES:\n${JSON.stringify(chunk, null, 2)}`,
    { label: `map:chunk${ci + 1}/${chunks.length}`, phase: 'Map', schema: MAP_SCHEMA })
    .then(r => r?.rows ?? [])))
  lastRows = mapped.filter(Boolean).flat()

  const agnostic = lastRows.filter(r => !r.lang_specific)
  const covered = agnostic.filter(r => r.covered)
  const missed = agnostic.filter(r => !r.covered)
  const coverage = agnostic.length ? covered.length / agnostic.length : 1
  coverageByRound.push({ round, agnostic_rules: agnostic.length,
                         covered: covered.length, missed: missed.length,
                         lang_specific: lastRows.length - agnostic.length,
                         coverage: Math.round(coverage * 100) / 100 })
  log(`round ${round}: coverage ${Math.round(coverage * 100)}% ` +
      `(${covered.length}/${agnostic.length} agnostic), ${missed.length} missed`)

  const proposed = missed.map(r => r.proposed_axis).filter(Boolean)
  if (coverage >= TARGET || !proposed.length || round === MAX_ROUNDS) break

  phase('Augment')
  const added = await agent(
    `Append these guideline-derived axes to docs/axis-catalog.yaml under a section ` +
    `comment "# === guideline-derived axes: ${repo} (round ${round}) ===". Use the ` +
    `generation-grade per-axis YAML shape (id, rule, enforce, enforceable). Dedup ` +
    `against existing axes. Be honest about enforce: most will be enforce:human / ` +
    `enforceable:convention unless a real linter rule applies. List ids added.\n\n` +
    `PROPOSED:\n${JSON.stringify(proposed, null, 2)}`,
    { label: `augment:round${round}`, phase: 'Augment' })
  addedAxes.push({ round, added })
}

// ── Synthesize ────────────────────────────────────────────────────────────
phase('Synthesize')
const missedAgnostic = lastRows.filter(r => !r.lang_specific && !r.covered)
const summary = await agent(
  `Summarize this guideline-coverage eval of ${repo}.\n` +
  `Coverage by round: ${JSON.stringify(coverageByRound)}\n` +
  `Axes added: ${JSON.stringify(addedAxes)}\n` +
  `Top uncovered AGNOSTIC standards (our real gaps):\n` +
  `${JSON.stringify(missedAgnostic.slice(0, 25).map(r => r.rule), null, 2)}\n\n` +
  `Give: (1) how complete is our catalog vs this authoritative standard? ` +
  `(2) the highest-value agnostic standards we're missing; ` +
  `(3) which proposed axes are genuinely enforceable (invariant) vs convention.`,
  { label: 'synthesize', phase: 'Synthesize' })

return { repo, docs: docs.map(d => d.path), coverageByRound, addedAxes,
         missedAgnostic: missedAgnostic.map(r => r.rule), summary }
