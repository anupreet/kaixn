# Bootstrapping the constitution

A team adopting kaixn already has principles and decisions — scattered across
docs, ADRs, READMEs, design files, and the code itself. Bootstrap mines them
into `norm` records so the conflict engine has something to check against on
day one.

This is the same shape as the causality extraction we critiqued in OpenJarvis —
a generic LLM "extract structured facts from text" pass. We keep the shape but
fix every flaw that made theirs a write-only sink:

| OpenJarvis failure | kaixn fix |
|---|---|
| Extracted tuples never read back | every norm is on a defined read path (`active_norms`) |
| No provenance | every candidate carries an **evidence span** (source + offset) |
| No atomicity | atomicity lint splits/rejects compound statements |
| No dedup → never forms a graph | embedding-similarity dedup vs existing norms |
| Auto-trusted, silently | bootstrap writes `status='proposed'`; a human promotes to `active` |
| Constitution could self-contradict | self-consistency pass before promotion |

## Pipeline

```
sources ─▶ ingest ─▶ extract ─▶ lint+dedup ─▶ self-consistency ─▶ review queue ─▶ active
(repo,      (chunk)   (LLM:      (atomic?      (cluster +          (human          (promote)
 docs,                candidate   near-dup?)    contradiction      curates
 ADRs,                norms +                   detection)         proposed→active)
 Notion)              evidence)
```

1. **Ingest** — pull from repo (`README`, `docs/`, ADRs, `CONTRIBUTING`,
   rich code comments, design docs), Notion/Confluence, Linear. Chunk,
   keeping a stable `source_ref` (path + line/anchor) per chunk.

2. **Extract** — per chunk, an LLM emits candidate norms:
   `{kind, domain, statement, rationale, scope, evidence_span, polarity_examples}`.
   `statement` must be a single normative claim; `evidence_span` points back at
   the source text (non-negotiable — no provenance, no candidate).

3. **Lint + dedup** — reject compound statements (split them); embed and
   compare against existing `norm` rows; near-duplicates get flagged to merge
   rather than inserted.

4. **Self-consistency** — cluster candidates by scope+domain and run the
   *conflict engine on the candidates themselves*. The constitution must start
   internally consistent. (Same engine used for spec review — build once.)

5. **Review queue** — everything lands as `status='proposed'`. A curator
   promotes to `active`. Nothing the model invented becomes binding law without
   a human in the loop.

## Notes

- Bootstrap distinguishes `principle` vs `decision` by signal: durable/normative
  prose ("we always…", "never…") → principle; a specific recorded choice with
  context ("we chose X over Y because…") → decision.
- Decisions mined from history may already be superseded by later ones — the
  self-consistency pass should propose `supersedes` edges, not flatten them.
