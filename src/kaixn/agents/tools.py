"""The PM copilot's tools — five read-only sources of context over kaixn's engine.

Each tool reveals a different layer of the same picture: what the team has
*decided* (norms/principles), what the repo *is* (the playbook), and whether a
proposed feature *collides* (the conflict engine). None of them write — committing
and resolving stay on the proposal-review surface; chat only discusses.

Collision detection is NOT re-implemented here: ``check_feature_conflicts`` wraps
the existing ``Kaixn.synthesize`` → ``synthesize_proposal`` → ``ConflictEngine``
path verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass

from kaixn.types import NormCandidate

_DOMAINS = ["product", "technical", "product_design", "ux"]


@dataclass
class PMContext:
    """Opaque ctx threaded to every tool by the agent loop."""
    service: object   # Kaixn
    repo: str


def _norm(n) -> dict:
    return {"id": n.id, "kind": n.kind, "domain": n.domain, "scope": n.scope,
            "statement": n.statement, "rationale": n.rationale}


class SearchNorms:
    name = "search_norms"
    description = (
        "Semantic search of the repo's constitution for the principles and decisions "
        "related to a feature concept — the primary grounding layer. Reveals what the "
        "team has already committed to near this idea. Returns only ACTIVE norms in ONE "
        "domain, so call it once per relevant domain. Blind to anything not yet ratified.")
    input_schema = {"type": "object", "properties": {
        "query": {"type": "string", "description": "the feature concept or topic to find related norms for"},
        "domain": {"type": "string", "enum": _DOMAINS},
        "top_k": {"type": "integer", "default": 8}},
        "required": ["query", "domain"]}

    def execute(self, args: dict, ctx: PMContext):
        cand = NormCandidate(statement=args["query"], domain=args["domain"])
        hits = ctx.service.store.neighbors(cand, top_k=int(args.get("top_k", 8) or 8))
        return [_norm(n) for n in hits]


class GetGoverningPrinciples:
    name = "get_governing_principles"
    description = (
        "List the PRINCIPLES that govern a scope — the hard constraints that can block "
        "a feature (as opposed to revisable decisions). Use when the feature touches a "
        "known area, e.g. scope 'all.product.billing'. Reveals the non-negotiables; says "
        "nothing about whether the feature actually violates them (use check_feature_conflicts).")
    input_schema = {"type": "object", "properties": {
        "domain": {"type": "string", "enum": _DOMAINS},
        "scope": {"type": "string", "default": "all", "description": "ltree path, e.g. all.product.billing"}},
        "required": ["domain"]}

    def execute(self, args: dict, ctx: PMContext):
        hits = ctx.service.store.active_principles(args["domain"], args.get("scope", "all") or "all")
        return [_norm(n) for n in hits]


class ReadPlaybookDoc:
    name = "read_playbook_doc"
    description = (
        "Read what the repo ACTUALLY is today — its generated PRDs, Tech Specs, and "
        "domain model. With no slug: the playbook index (doc list + domain entities). "
        "With kind+slug: that document's full markdown. Grounds the discussion in real "
        "features, not just abstract norms. Blind spot: this is generated knowledge — "
        "use search_norms for the team's ratified decisions.")
    input_schema = {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["prd", "spec"], "description": "doc kind; omit for the index"},
        "slug": {"type": "string", "description": "doc slug from the index; omit to list the index"}},
        "required": []}

    def execute(self, args: dict, ctx: PMContext):
        from kaixn import playbook_store as ps   # fresh store (own conn) per call

        store = ps.from_env()
        kind, slug = args.get("kind"), args.get("slug")
        if kind and slug:
            return store.get_doc(ctx.repo, kind, slug) or {"error": "document not found"}
        pb = store.get_playbook(ctx.repo)
        if not pb:
            return {"error": "repo not indexed yet"}
        return {
            "entities": pb.get("entities") or [],
            "docs": [{"kind": d.get("kind"), "slug": d.get("slug"), "title": d.get("title"),
                      "summary": d.get("summary"), "group": d.get("grp")}
                     for d in (pb.get("docs") or [])],
        }


class CheckFeatureConflicts:
    name = "check_feature_conflicts"
    description = (
        "Test the PM's described feature for COLLISIONS with the constitution. Runs "
        "kaixn's synthesis + conflict engine and returns per-operation verdicts "
        "(consistent / conflict / tension) plus proposal-level GAPs, and the proposal_id. "
        "This is how you find out whether an idea fits before any code is written. When "
        "the semantic engine is offline, only structural verdicts are returned (the "
        "result's conflict_report will be null — say so rather than imply certainty).")
    input_schema = {"type": "object", "properties": {
        "feature_description": {"type": "string",
            "description": "the feature, phrased as a concrete intent the engine can decompose"}},
        "required": ["feature_description"]}

    def execute(self, args: dict, ctx: PMContext):
        return ctx.service.synthesize(args["feature_description"])


class GetProposal:
    name = "get_proposal"
    description = (
        "Re-read a proposal you synthesized earlier (by proposal_id) without "
        "re-running synthesis — for continuity when the PM refers back to a collision "
        "you already found.")
    input_schema = {"type": "object", "properties": {
        "proposal_id": {"type": "string"}}, "required": ["proposal_id"]}

    def execute(self, args: dict, ctx: PMContext):
        return ctx.service.get_proposal(args["proposal_id"]) or {"error": "unknown proposal"}


def all_tools() -> list:
    """The PM copilot's full, read-only tool set."""
    return [SearchNorms(), GetGoverningPrinciples(), ReadPlaybookDoc(),
            CheckFeatureConflicts(), GetProposal()]
