---
name: prompt
description: Write and review LLM prompts following our goal-oriented prompt engineering principles
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash(git *), Agent, Edit, Write
---

# Prompt Engineering

Write or review LLM prompts following the principles below. These are how we build prompts in kaixn — goal-oriented, grounded, and guardrailed.

Read existing prompts in `src/kaixn/` (`engine.py`, `llm.py`, `playbook.py`, `miner.py`, `bootstrap.py`) and `src/kaixn/agents/` for reference implementations before writing new ones.

$ARGUMENTS

---

## Philosophy

**Teach the agent to think, don't tell it what to do.**

A good prompt explains what success looks like and what landscape the agent is working in — then trusts the model to navigate. A bad prompt says "do step 1, then step 2, then step 3" and the model cargo-cults through it without understanding why.

---

## Principles

### 1. Goal Over Process

Every prompt section opens with **why** it exists, not how to execute it. The agent needs to understand what it's trying to achieve so it can adapt when the data or situation doesn't match a prescribed path.

Frame goals as outcomes:
- What does the end-user need to walk away with?
- What does "good" look like for this output?
- What judgment calls will the agent need to make?

Per-section goals should connect back to the top-level mission. If a section can't articulate its goal in one sentence, it's doing too much.

### 2. Identity as Lens

Open with a sharp persona + mission that anchors all downstream behavior. The identity isn't decoration — it's the lens through which the agent interprets ambiguity, prioritizes information, and decides what matters.

The identity should specify:
- **Who** the agent is (role, domain expertise)
- **Who** they serve (the end-user's role and context)
- **What success looks like** (the outcome, not the process)

### 3. Tools as Context Sources

Tools are not procedures to execute — they are **sources of context** that serve the goal. Document each tool in terms of:
- **What reality it reveals** — what does the world look like after calling this tool?
- **What context it adds** — how does this information serve the goal?
- **What it does NOT provide** — where are its blind spots?
- **How its output connects to other tools' output** — shared keys, join patterns, complementary perspectives

The agent should understand that different tools provide different layers of the same picture. The **norm store** is the team's committed decisions (the constitution). The **conflict engine** reveals where a proposed change collides with, is in tension with, or leaves a gap against those decisions. The **playbook** (features, specs, domain model) is what the code actually is today. A complete answer requires the layers appropriate to the question.

### 4. No Examples in Prompts

Examples are hallucinogenics. The model copies the surface pattern of the example (names, structure, phrasing) rather than understanding the goal. An example of a "good clarifying question" will produce variations of that exact question rather than questions tailored to the actual context.

**The only exception** is output format contracts — JSON schemas or markdown templates that a downstream parser must consume. Even then, prefer a schema definition over example values. Use the JSON Schema `type`, `enum`, `description` fields to communicate structure. If you must show format, use placeholder tokens (`<field_name>`) not realistic-looking fake data.

### 5. Guardrails as Boundaries

Guardrails define the edges of the solution space — what the agent must NOT do. They are constraints, not instructions.

Effective guardrails:
- **Grounding**: "Only include information from tool results or provided context. Never fabricate."
- **Scope**: "Answer only the question asked. Do not add tangential analysis."
- **Output**: "Re-read your response and remove any section the user did not ask for."
- **Safety**: "Never fabricate ids — norm ids and axis ids must come from tool results."

Guardrails work because they prune bad outputs without prescribing good ones. The goal + tools + identity handle the positive direction.

### 6. Rubrics Over Rules for Judgment

When the agent needs to make qualitative decisions (scoring, classification, severity assessment), provide a **calibration framework** — not a decision tree.

A rubric describes what each level looks like:
- What behaviors or evidence correspond to each score?
- What distinguishes adjacent scores?
- What contextual factors should shift the assessment? (e.g., governed vs advisory norm, conflict vs tension vs gap)

This lets the agent exercise judgment while staying calibrated. A decision tree forces every situation through the same path and breaks on edge cases.

### 7. Context-Driven Branching

When behavior should vary based on input state (a brand-new intent vs. refining a flagged collision, an empty constitution vs. a mature one), frame each branch as a **separate goal** — not an if/else instruction.

Instead of "if collisions == 0, do X else do Y", write:
- "**Clean proposal** — Goal: confirm the intent is fully specified and surface the gaps the PM didn't know about." Then describe what good looks like for that goal.
- "**Colliding proposal** — Goal: help the PM resolve each collision against the existing decision before any code is written." Then describe what good looks like.

The model infers the branching from the goal + available data.

### 8. Modular, Composable Sections

Break prompts into independent sections with clear responsibilities. Each section should:
- Have a single focus (identity, tools, output format, guardrails)
- Be testable and swappable independently
- Work whether other sections are present or not

Separate **static sections** (don't change per-turn) from **dynamic sections** (injected per-turn: the repo's constitution, the PM's intent so far, current date). Document the section order and which are static vs dynamic.

### 9. Brevity as Output Constraint

Actively constrain output length. Models default to verbose — they'll pad with methodology, caveats, and unsolicited advice unless told not to. Frame brevity in terms of the end-user:
- Who is reading this? (A PM between planning sessions, not someone with time for a 10-page report)
- What's the right density? (Lead with the key finding, support with evidence, stop)

### 10. Honest About Gaps

The prompt should instruct the agent to acknowledge when data is missing or insufficient rather than filling gaps with inference. "Mark inferences as uncertain" and "acknowledge gaps when a search doesn't return results" are grounding instructions that build user trust.

---

## Prompt Structure Template

When writing a new prompt, use this structure as a starting point — adapt sections as needed:

```
1. IDENTITY        — Who the agent is, who it serves, what success looks like
2. TOOLS           — What context each tool reveals, blind spots, how they connect
3. INPUTS          — What data the agent receives and what each field means
4. OUTPUT          — Format contract (schema, not examples), what each field should contain
5. GOAL SECTIONS   — Per-branch goals (e.g., clean vs colliding proposal)
6. JUDGMENT        — Rubrics for qualitative decisions (if applicable)
7. GUARDRAILS      — Hard boundaries: grounding, scope, safety
```

---

## Review Checklist

When reviewing an existing prompt:

- [ ] Does every section articulate its goal before describing behavior?
- [ ] Are tools described in terms of what context they add, not just parameters?
- [ ] Are there examples with realistic-looking fake data? (Remove them)
- [ ] Are there prescriptive step-by-step workflows? (Reframe as "layers of context needed")
- [ ] Do guardrails define boundaries or prescribe behavior? (Should be boundaries)
- [ ] Is the output format defined via schema/template or via examples? (Should be schema)
- [ ] Would a human reading this understand what "good" looks like without reading the code?
- [ ] Are qualitative decisions guided by rubrics or by if/else rules?
- [ ] Is output brevity actively constrained with user-context reasoning?
- [ ] Does the prompt acknowledge what to do when data is missing?
