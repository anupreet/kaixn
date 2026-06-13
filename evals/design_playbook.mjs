// kaixn — "rigor for free" demo: generate a repo's architecture/design playbook
//
// Point at any repo → for each ARCHITECTURE/DESIGN-tier axis (not lint), an agent
// reads the real source and determines the repo's value + evidence + consistency.
// Demonstrates the value prop: a well-architected repo's design playbook, produced
// for free, with no standards authored by the team.
//
//   Fetch     — shallow-clone the repo, build a skeleton
//   Mine      — fan out per axis-group: evaluate each design axis vs the source
//   Synthesize— assemble the architecture playbook + patterns we don't yet catalog
//
// args: { repo: "owner/name" }

export const meta = {
  name: 'design-playbook',
  description: 'Generate a repo\'s architecture/design playbook for free (design-tier axes only)',
  phases: [
    { title: 'Fetch', detail: 'shallow-clone + skeleton' },
    { title: 'Mine',  detail: 'evaluate design axes against the source' },
    { title: 'Synthesize', detail: 'write the architecture playbook' },
  ],
}

const repo = args?.repo ?? 'encode/starlette'
const clonePath = '/tmp/kaixn-design-' + repo.replace(/[^A-Za-z0-9]+/g, '-')
const outFile = 'docs/playbooks/' + repo.replace(/[^A-Za-z0-9]+/g, '-') + '.md'

// Architecture/design tier only — the moat. Lint/naming/formatting deliberately excluded.
const GROUPS = [
  { name: 'architecture-layering',
    axes: ['module-responsibility', 'layering-direction', 'seam-pattern',
           'dependency-injection', 'impl-pairing', 'offline-fallback',
           'package-layout', 'public-surface', 'config-management'] },
  { name: 'error-correctness',
    axes: ['error-signaling', 'error-propagation', 'input-validation',
           'none-handling', 'state-mutation', 'idempotency', 'resource-lifecycle'] },
  { name: 'concurrency-reliability',
    axes: ['concurrency-model', 'blocking-io', 'timeouts',
           'graceful-degradation', 'retry-backoff', 'caching'] },
  { name: 'data-persistence',
    axes: ['data-access', 'transaction-boundaries', 'serialization', 'schema-sot'] },
  { name: 'api-interface-design',
    axes: ['response-shape', 'error-response', 'versioning', 'pagination',
           'status-codes', 'backward-compat'] },
  { name: 'security-boundaries',
    axes: ['trust-boundary', 'authz-boundary', 'authn-pattern',
           'injection-safety', 'secrets-handling'] },
]

const MINE_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['axes'],
  properties: { axes: { type: 'array', items: {
    type: 'object', additionalProperties: false,
    required: ['axis', 'applies', 'value', 'rationale'],
    properties: {
      axis: { type: 'string' },
      applies: { type: 'boolean' },          // false = dimension irrelevant to this repo
      value: { type: 'string' },             // the repo's choice on this axis
      evidence: { type: 'array', items: { type: 'string' } },
      consistency: { type: 'string' },       // high | medium | low
      tier: { type: 'string' },              // advisory | governed
      rationale: { type: 'string' },
    } } } },
}

// ── Fetch ─────────────────────────────────────────────────────────────────
phase('Fetch')
const fetched = await agent(
  `Shallow-clone ${repo} for analysis:\n` +
  `  rm -rf ${clonePath}; git clone --depth 1 https://github.com/${repo} ${clonePath}\n` +
  `Then build a compact skeleton: the package layout (top dirs), the main source ` +
  `modules with a one-line purpose each, and which files look central (by size / ` +
  `how often they're imported). Return {cloned:true, skeleton:"..."}.`,
  { label: `clone:${repo}`, phase: 'Fetch',
    schema: { type: 'object', additionalProperties: false, required: ['cloned'],
      properties: { cloned: { type: 'boolean' }, skeleton: { type: 'string' } } } })

if (!fetched?.cloned) return { error: `failed to clone ${repo}` }
log(`cloned ${repo}`)

// ── Mine (fan out per group) ──────────────────────────────────────────────
phase('Mine')
const mined = await parallel(GROUPS.map(g => () => agent(
  `Evaluate the ARCHITECTURE/DESIGN of the repo cloned at ${clonePath} along these ` +
  `axes: ${g.axes.join(', ')}. The axis definitions (the reviewer question each ` +
  `answers) are in docs/axis-catalog.yaml — read it. Then READ THE ACTUAL SOURCE ` +
  `at ${clonePath} (ls/grep/cat the relevant modules) and for EACH axis report:\n` +
  `- applies: false if the dimension is genuinely irrelevant to this repo (e.g. a ` +
  `  DB axis for a DB-less library, pagination for a non-API library) — be honest, ` +
  `  do NOT force a value;\n` +
  `- value: the repo's actual choice on this axis (concise);\n` +
  `- evidence: 2-4 real file paths (relative to repo root) that show it;\n` +
  `- consistency: high|medium|low across the codebase;\n` +
  `- tier: advisory (observed convention) | governed (load-bearing principle);\n` +
  `- rationale: one sentence.\n` +
  `Skeleton for orientation:\n${fetched.skeleton || ''}`.slice(0, 6000),
  { label: `mine:${g.name}`, phase: 'Mine', schema: MINE_SCHEMA })
  .then(r => (r?.axes ?? []).map(a => ({ ...a, group: g.name })))))

const rows = mined.filter(Boolean).flat()
const applicable = rows.filter(r => r.applies)
log(`evaluated ${rows.length} axes; ${applicable.length} apply to ${repo}`)

// ── Synthesize → write the playbook ───────────────────────────────────────
phase('Synthesize')
const summary = await agent(
  `Write the ARCHITECTURE/DESIGN PLAYBOOK for ${repo} to ${outFile} (create dirs). ` +
  `This is the "rigor for free" artifact: a reviewer-grade map of the repo's design, ` +
  `produced with no standards authored by the team.\n\n` +
  `Use this evaluated axis data (applies=false ones are dimensions that don't apply ` +
  `— list them briefly under an "N/A" note, don't pad the playbook with them):\n` +
  `${JSON.stringify(applicable, null, 2)}\n\n` +
  `N/A axes: ${JSON.stringify(rows.filter(r => !r.applies).map(r => r.axis))}\n\n` +
  `Structure the file: (1) a short architecture narrative (what kind of system, the ` +
  `defining design choices); (2) a per-group table [axis | value | tier | ` +
  `consistency | evidence]; (3) "Patterns worth noting" — 3-6 strong design patterns ` +
  `this repo exhibits, flagging any that are NOT yet in our axis catalog (candidate ` +
  `new design-tier axes — meta-discovery). Keep it tight and concrete. ` +
  `After writing, return a 6-line summary of the repo's architecture + the candidate ` +
  `new axes.`,
  { label: 'synthesize-playbook', phase: 'Synthesize' })

return { repo, outFile, axes_total: rows.length,
         axes_applicable: applicable.length, summary }
