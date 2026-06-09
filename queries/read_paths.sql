-- kaixn — the read paths the schema exists to serve.  (v0.2)
-- The table design is driven by these; if a field serves no read path, drop it.
-- (Lesson from reviewing OpenJarvis: never build write-only memory.)
--
-- v0.2: change-bearing edges originate at `operation` (or `code_ref`), so
-- impact/provenance traverse operation -> proposal, not spec/spec_claim.

-- ===========================================================================
-- 1. active_norms(domain, scope)
-- The set the conflict engine checks an operation against: active norms whose
-- scope governs the target. ltree `@>` is ancestor-or-equal, so a norm at
-- all.product.billing applies to all.product.billing.subscriptions, and a
-- global norm at `all` applies everywhere.
-- Params: $1 = domain, $2 = target scope (ltree)
-- ---------------------------------------------------------------------------
SELECT n.id, n.kind, n.domain, n.statement, n.rationale, n.scope, n.version
FROM   norm n
WHERE  n.status = 'active'
  AND  n.domain = $1::domain
  AND  n.scope @> $2::ltree
ORDER BY n.kind,            -- principles before decisions
         nlevel(n.scope);   -- broadest (global) first
-- Variant: principles are small enough to always load in full, across scope:
--   ... WHERE status='active' AND kind='principle' AND scope @> $1::ltree;


-- ===========================================================================
-- 2. impact_of(norm_id)
-- Reverse impact — "if we change this norm, what's at risk?"
-- Walks: norm <- depends_on - operation - (proposal) - implements <- code_ref
-- Params: $1 = norm_id
-- ---------------------------------------------------------------------------
WITH dependent_ops AS (
  SELECT e.src_id AS operation_id
  FROM   edge e
  WHERE  e.dst_id   = $1
    AND  e.dst_type = 'norm'
    AND  e.rel_type = 'depends_on'
)
SELECT o.id             AS operation_id,
       o.op_type,
       o.statement      AS operation_statement,
       p.id             AS proposal_id,
       p.version        AS proposal_version,
       p.status         AS proposal_status,
       f.id             AS feature_id,
       f.title          AS feature_title,
       i.id             AS initiative_id,
       i.title          AS initiative_title,
       cr.repo, cr.ref_type, cr.ref
FROM   dependent_ops d
JOIN   operation o ON o.id = d.operation_id
JOIN   proposal  p ON p.id = o.proposal_id
JOIN   feature   f ON f.id = p.feature_id
LEFT   JOIN initiative i ON i.id = f.initiative_id
LEFT   JOIN edge ie ON ie.dst_id = p.id
                   AND ie.dst_type = 'proposal'
                   AND ie.rel_type = 'implements'
LEFT   JOIN code_ref cr ON cr.id = ie.src_id
ORDER  BY i.title, f.title, p.version, o.ord;


-- ===========================================================================
-- 3. provenance(code_ref_id)
-- Forward walk — "why is this code the way it is?"
--   code_ref - implements -> proposal -> operations - depends_on -> norm
-- Params: $1 = code_ref_id
-- ---------------------------------------------------------------------------
SELECT p.id        AS proposal_id,
       p.version,
       o.id        AS operation_id,
       o.op_type,
       o.statement AS operation_statement,
       n.id        AS norm_id,
       n.kind,
       n.domain,
       n.statement AS norm_statement,
       n.status    AS norm_status
FROM   edge impl
JOIN   proposal p   ON p.id = impl.dst_id AND impl.dst_type = 'proposal'
JOIN   operation o  ON o.proposal_id = p.id
LEFT   JOIN edge dep ON dep.src_id = o.id AND dep.src_type = 'operation'
                    AND dep.rel_type = 'depends_on'
LEFT   JOIN norm n   ON n.id = dep.dst_id
WHERE  impl.src_id   = $1
  AND  impl.src_type = 'code_ref'
  AND  impl.rel_type = 'implements'
ORDER  BY p.version, o.ord;


-- ===========================================================================
-- 4. supersede_chain(norm_id)
-- Full lineage of a decision — what it replaced and what replaced it.
-- Walks `supersedes` edges in both directions from the seed.
-- Params: $1 = norm_id
-- ---------------------------------------------------------------------------
WITH RECURSIVE
back AS (  -- ancestors: things this norm (transitively) superseded
  SELECT e.dst_id AS norm_id, 1 AS depth
  FROM   edge e
  WHERE  e.src_id = $1 AND e.rel_type = 'supersedes'
  UNION ALL
  SELECT e.dst_id, b.depth + 1
  FROM   edge e JOIN back b ON e.src_id = b.norm_id
  WHERE  e.rel_type = 'supersedes'
),
fwd AS (   -- descendants: things that (transitively) superseded this norm
  SELECT e.src_id AS norm_id, 1 AS depth
  FROM   edge e
  WHERE  e.dst_id = $1 AND e.rel_type = 'supersedes'
  UNION ALL
  SELECT e.src_id, f.depth + 1
  FROM   edge e JOIN fwd f ON e.dst_id = f.norm_id
  WHERE  e.rel_type = 'supersedes'
)
SELECT n.id, n.statement, n.status, n.version, n.created_at,
       CASE WHEN n.id = $1 THEN 'self'
            WHEN b.norm_id IS NOT NULL THEN 'superseded-by-this'
            ELSE 'supersedes-this' END AS relation
FROM   norm n
LEFT   JOIN back b ON b.norm_id = n.id
LEFT   JOIN fwd  f ON f.norm_id = n.id
WHERE  n.id = $1 OR b.norm_id IS NOT NULL OR f.norm_id IS NOT NULL
ORDER  BY n.created_at;


-- ===========================================================================
-- 5. proposal_review(proposal_id)   [NEW in v0.2 — the review surface]
-- Render a Proposal as change-against-state: every operation, its target,
-- its verdicts, and its recorded resolution. This replaces PR review.
-- Params: $1 = proposal_id
-- ---------------------------------------------------------------------------
SELECT o.id                AS operation_id,
       o.ord,
       o.kind,
       o.op_type,
       o.status            AS operation_status,
       o.statement,
       -- target norm (for modify/deprecate/supersede)
       tn.id               AS target_norm_id,
       tn.statement        AS target_norm_statement,
       tn.status           AS target_norm_status,
       -- produced norm (for assert/supersede)
       pn.id               AS produced_norm_id,
       pn.statement        AS produced_norm_statement,
       -- adjudication
       v.verdict,
       v.norm_id           AS verdict_norm_id,
       vn.statement        AS verdict_norm_statement,
       v.evidence,
       v.proposed_resolution,
       -- recorded conflict resolution (edge metadata)
       ce.metadata         AS resolution_metadata
FROM   operation o
LEFT   JOIN norm tn   ON tn.id = o.target_norm_id
LEFT   JOIN norm pn   ON pn.id = o.produced_norm_id
LEFT   JOIN verdict v ON v.operation_id = o.id
LEFT   JOIN norm vn   ON vn.id = v.norm_id
LEFT   JOIN edge ce   ON ce.src_id = o.id
                     AND ce.src_type = 'operation'
                     AND ce.rel_type = 'conflicts_with'
                     AND ce.dst_id = v.norm_id
WHERE  o.proposal_id = $1
ORDER  BY o.ord, v.verdict;
