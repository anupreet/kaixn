// kaixn — playbook eval loop (Claude Workflow)
//
// Closed-loop eval grounded in real PR review comments:
//   Agent 1 (Critique) — LLM-as-judge audits the playbook for zero-comment code-gen.
//   Agent 2 (Classify) — per PR, pull inline review comments and map each to the
//                        catalog: should-be-guideline? caught or MISSING?
//   Loop — append missed should-be-guideline comments as new axes, re-eval, until
//          coverage >= target (or no new gaps). Converges the playbook to the
//          team's real review behaviour.
//
// args: { repo: "owner/name", nPrs?: 10, coverageTarget?: 0.95, maxRounds?: 4 }

export const meta = {
  name: 'playbook-eval-loop',
  description: 'Critique the engineering playbook + close the loop against a repo\'s last N PR review comments',
  phases: [
    { title: 'Critique', detail: 'LLM-judge audits the playbook' },
    { title: 'Discover', detail: 'list the last N merged PRs' },
    { title: 'Classify', detail: 'per PR: map review comments to catalog axes' },
    { title: 'Augment',  detail: 'append missed comments as new axes' },
    { title: 'Synthesize', detail: 'coverage report + verdict' },
  ],
}

const repo = args?.repo ?? 'langchain-ai/langchain'
const N = args?.nPrs ?? 30
const TARGET = args?.coverageTarget ?? 0.95
const MAX_ROUNDS = args?.maxRounds ?? 2

const CRITIQUE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['scores', 'false_invariants', 'vague_rules', 'missing_axes', 'top_actions'],
  properties: {
    scores: { type: 'object' },
    false_invariants: { type: 'array', items: { type: 'object' } },
    vague_rules:      { type: 'array', items: { type: 'object' } },
    missing_axes:     { type: 'array', items: { type: 'object' } },
    top_actions:      { type: 'array', items: { type: 'string' } },
  },
}

const PRS_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['prs'],
  properties: { prs: { type: 'array', items: {
    type: 'object', required: ['number'], properties: {
      number: { type: 'number' }, title: { type: 'string' } } } } },
}

const ROWS_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['rows'],
  properties: { rows: { type: 'array', items: {
    type: 'object', additionalProperties: false,
    required: ['body', 'should_be_guideline', 'axis', 'would_catch'],
    properties: {
      body: { type: 'string' },
      path: { type: 'string' },
      should_be_guideline: { type: 'boolean' },
      axis: { type: 'string' },                 // catalog id or "MISSING"
      enforceable: { type: 'string' },          // invariant | convention | NA
      would_catch: { type: 'boolean' },         // axis != MISSING
      proposed_axis: { type: ['object', 'null'] }, // {name,category,rule,enforce}
      note: { type: 'string' },
    } } } },
}

// ── Phase 1: critique ─────────────────────────────────────────────────────
phase('Critique')
const critique = await agent(
  `You are a staff engineer auditing an engineering playbook used to generate code ` +
  `with ZERO review comments. Read docs/engineering-playbook.md and docs/axis-catalog.yaml. ` +
  `Be skeptical and specific. Flag: FALSE invariants (axis labelled governed/invariant ` +
  `but only a human could catch a violation — no linter/type-checker/test); vague rules ` +
  `an agent could read 2+ ways; missing axes a senior reviewer would want. Score ` +
  `concreteness, enforcement_honesty, coverage, overall (0-10).`,
  { label: 'critique-playbook', phase: 'Critique', schema: CRITIQUE_SCHEMA })

// ── Phase 2: discover PRs ─────────────────────────────────────────────────
phase('Discover')
const discovered = await agent(
  `Run exactly: gh pr list --repo ${repo} --state merged --limit ${N} --json number,title ` +
  `and return the parsed PRs. If the command errors (auth/repo), return {"prs":[]}.`,
  { label: `discover:${repo}`, phase: 'Discover', schema: PRS_SCHEMA })

const prs = (discovered?.prs ?? []).filter(p => p && p.number)
if (!prs.length) {
  return { error: `no merged PRs found for ${repo} (check gh auth account + repo access)`, critique }
}

// ── Phase 3-4: classify → augment, looped until coverage >= target ─────────
const coverageByRound = []
const addedAxes = []
let finalRows = []

for (let round = 1; round <= MAX_ROUNDS; round++) {
  phase('Classify')
  const perPr = await parallel(prs.map(pr => () => agent(
    `Gather ALL review feedback for PR #${pr.number} of ${repo} from three sources:\n` +
    `  inline:        gh api repos/${repo}/pulls/${pr.number}/comments --paginate\n` +
    `  review bodies: gh api repos/${repo}/pulls/${pr.number}/reviews --jq '[.[]|select(.body!="")]'\n` +
    `  conversation:  gh api repos/${repo}/issues/${pr.number}/comments --paginate\n` +
    `Treat each non-empty comment/review body as one item. ` +
    `Read docs/axis-catalog.yaml (the current axes). For EACH item classify:\n` +
    `- should_be_guideline: would a good team encode this as a reusable rule? ` +
    `(false for typos, questions, praise, or one-off context-specific notes)\n` +
    `- axis: the catalog axis id that captures it, or "MISSING"\n` +
    `- enforceable: invariant | convention | NA\n` +
    `- would_catch: true iff axis != "MISSING"\n` +
    `- proposed_axis: if MISSING AND should_be_guideline, {name,category,rule,enforce}; else null\n` +
    `If the PR has no review feedback in any source, return {"rows":[]}.`,
    { label: `classify:PR#${pr.number}`, phase: 'Classify', schema: ROWS_SCHEMA })
    .then(r => r?.rows ?? [])))

  finalRows = perPr.filter(Boolean).flat()
  const guidelines = finalRows.filter(r => r.should_be_guideline)
  const caught = guidelines.filter(r => r.would_catch)
  const missed = guidelines.filter(r => !r.would_catch)
  const coverage = guidelines.length ? caught.length / guidelines.length : 1
  coverageByRound.push({ round, guidelines: guidelines.length,
                         caught: caught.length, missed: missed.length,
                         coverage: Math.round(coverage * 100) / 100 })
  log(`round ${round}: coverage ${Math.round(coverage * 100)}% ` +
      `(${caught.length}/${guidelines.length}), ${missed.length} missed`)

  const proposed = missed.map(r => r.proposed_axis).filter(Boolean)
  if (coverage >= TARGET || !proposed.length) break
  if (round === MAX_ROUNDS) break

  phase('Augment')
  const added = await agent(
    `Append these eval-discovered axes to docs/axis-catalog.yaml under a new section ` +
    `comment "# === eval-discovered axes (round ${round}) ===". Use the same per-axis ` +
    `YAML shape used in the generation-grade section (id, rule, enforce, enforceable). ` +
    `Dedup against axes already in the file. Then list the ids you added.\n\n` +
    `PROPOSED:\n${JSON.stringify(proposed, null, 2)}`,
    { label: `augment:round${round}`, phase: 'Augment' })
  addedAxes.push({ round, added })
}

// ── Phase 5: synthesize ───────────────────────────────────────────────────
phase('Synthesize')
const summary = await agent(
  `Summarize this playbook eval loop for ${repo}.\n` +
  `Coverage by round: ${JSON.stringify(coverageByRound)}\n` +
  `Axes added: ${JSON.stringify(addedAxes)}\n` +
  `Playbook critique scores: ${JSON.stringify(critique?.scores ?? {})}\n` +
  `Give: (1) does the playbook now catch the should-be-guideline comments? ` +
  `(2) any still-missed comments + why; (3) the 3 highest-value next actions.`,
  { label: 'synthesize', phase: 'Synthesize' })

return { repo, coverageByRound, addedAxes, critique, summary,
         finalRowCount: finalRows.length }
