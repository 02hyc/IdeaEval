# Quality Layer Design - Autonomous AI Research-Ideation Agent

> **Status:** Quality-layer design for the implemented MVP prompt, skills, and optional subagents.
> **Last updated:** 2026-06-04
> **Concept source:** [`PROJECT_OVERVIEW.md`](../concept/PROJECT_OVERVIEW.md)
> **Architecture:** [`ARCHITECTURE.md`](./ARCHITECTURE.md)

This document designs the proposal-quality layer described in `ARCHITECTURE.md`. It specifies the startup prompt contract, the MVP skill set, optional subagent roles, and the boundary between task instructions, reusable procedures, and review-style assistance.

## Boundary

The quality layer shapes the behavior of the lead research-ideation agent. It does not implement run workspaces, SDK adapters, MCP configuration files, permission handling, mechanical validation, or repair-loop mechanics; those belong to the infrastructure layer.

The MVP quality layer has three design surfaces:

- **Startup prompt:** The minimal run contract that starts the lead agent.
- **Skills:** Reusable procedural guidance made available to the lead agent for selected runs.
- **Optional subagents:** Bounded review or critique helpers that the lead agent may call, while preserving lead-agent ownership of the final proposal.

The quality layer should improve proposal quality by changing instructions, reusable skills, or optional review prompts, not by adding Python research heuristics to the framework.

Skills and subagents are selected by run configuration. A run may expose all MVP core skills, a subset of skills, no optional skills, selected reviewer subagents, or no subagents. Unselected skills and subagents should not be packaged into the run workspace or exposed through the selected harness. In the current implementation, reviewer subagents are packaged into Claude's `.claude/agents/*.md` layout or Codex's `.codex/agents/*.toml` layout when selected.

## Foundational Principle

The startup prompt is the minimal sufficient contract for a complete run. If the lead agent receives only the startup prompt plus the configured tools, it should still be able to interpret the topic, search and inspect literature, select a gap, select a method, design experiments, write `proposal.md`, and perform a final self-check.

Skills and subagents are quality multipliers, not completion requirements. Skills provide reusable procedures for difficult research behaviors that the model may otherwise do shallowly or inconsistently. Subagents provide optional focused assistance, critique, or decomposition inside the lead-agent run. The lead agent remains responsible for final decisions, integration, writing, and stopping.

## Design Goals

- **One selected proposal:** The final artifact should develop one chosen research direction, not return a menu of alternatives.
- **Grounded literature use:** Literature claims, closest-work comparisons, and references should be based on retrieved or fetched sources that the agent inspected.
- **Candidate exploration before commitment:** The lead agent should compare multiple candidate gaps and multiple candidate methods before selecting one.
- **Proposal stance:** The output should design experiments and state hypotheses without claiming that empirical results already exist.
- **Useful decision criteria:** The proposal should help a reader decide whether the direction is worth pursuing, including success, refinement, stop criteria, assumptions, and risks.

## Responsibility Split

| Surface | Owns | Should not own |
|---|---|---|
| **Always-on guidance (`AGENTS.md` / `CLAUDE.md`)** | Stable run invariants: workspace contract, evidence discipline, artifact-on-disk requirement, tool/file boundaries, and final quality gate. | The concrete user topic, proposal template, detailed literature-review method, or one-run-only configuration. |
| **Startup prompt** | Run-specific task packet: topic, selected skills, available tool overview, workflow entry point, required note files, and stop condition. | Proposal template, cutoff/evidence-policy internals, long reusable research procedures, harness-specific implementation detail, optional review prompts, or any instruction that can safely be deferred to a skill. |
| **Skills** | Reusable "how to do the work" procedures: literature grounding, gap selection, method design, experiment design, proposal writing, and final checking. | The user topic, one-run-only details, always-on workspace constraints, or final ownership of decisions. |
| **Subagents** | Bounded critique, review, and challenge tasks inside the lead-agent run. | Autonomous ownership of stages, final proposal writing, final method selection, or fixed pipeline execution. |

Hard constraints that must survive the whole run belong in always-on guidance. The startup prompt should reference that contract and provide the run-specific topic and configuration. Proposal structure belongs in the proposal-writing skill to avoid template drift.

## Startup Prompt Design

The startup prompt is the run-specific launch packet. It should be compact but complete enough to tell the lead agent what this run is about, which assets are selected, which files must be produced, and where to find the detailed procedures.

The prompt should include these sections:

1. **Role and mission:** State that the agent is running one scientific hypothesis generation task and must produce one literature-grounded proposal.
2. **Topic block:** Quote the user's topic verbatim.
3. **Run configuration:** Include selected skills and available parallel exploration surfaces. Do not expose hidden evidence-policy details such as cutoff date to the agent.
4. **Workflow entry point:** Name the ordered stages and map them to selected skills.
5. **Persistent task contract:** Require a short `notes/task_contract.md` for continuation and repair.
6. **MCP overview:** Name the available search, fetch, extraction, and summarization tools at the level needed for correct use.
7. **Required files:** List the note files and final `proposal.md` that must exist on disk.
8. **Stopping condition:** Stop only after the required files exist and the proposal-writing/audit skill has been applied.

The startup prompt should not try to contain every detailed procedure, the proposal template, or the full evidence-discipline contract. It should point the lead agent toward the always-on guidance and selected skills.

## Startup Prompt Outline

The implemented startup prompt follows this outline. The exact wording should be iterated through experiments.

```text
You are the lead research-ideation agent for one autonomous run.

User topic:
<topic>

Mission:
Develop this topic into one literature-grounded AI research proposal. Do not ask the user for clarification. Do not return a brainstorm, survey-only answer, or multiple final alternatives.

Run ownership:
You own the full run: search decisions, source interpretation, gap selection, method selection, experiment design, proposal writing, final self-check, and stopping. Use available tools, selected skills, and optional subagents when useful, but make the final decisions yourself.

Available tools and guidance:
- Use the configured MCP tools for literature search, source fetch, and paper reading.
- Search results are leads, not sufficient evidence for final related-work claims.
- Fetch or read important sources before relying on them.
- Use selected skills for detailed procedures when they are relevant.
- Optional subagents may be used for bounded critique if available.

Required workflow:
1. Explore the literature from multiple search angles.
2. Inspect important sources before relying on them.
3. Form multiple candidate gaps and select one.
4. Generate multiple candidate methods for the selected gap and select one.
5. Design experiments that would test the central claim.
6. Write proposal.md.
7. Perform a semantic final check before stopping.

Always-on contract:
Use `AGENTS.md` or `CLAUDE.md` for evidence discipline, artifact-on-disk requirements, and final quality gate.

Quality target:
The proposal should identify the closest prior work and the delta, develop one concrete and worthwhile gap, describe one specific and feasible method, design experiments that test the central claim, explain impact, and define success, refinement, and stop criteria.

Final artifact:
Write `proposal.md` using the structure defined by the proposal-writing skill.
```

## What Should Not Go In The Startup Prompt

The startup prompt should stay short enough to remain a usable launch command. These belong in skills or subagent prompts instead:

- Detailed literature-search tactics, paper-note schemas, and source-registry procedures.
- Full gap-scoring or method-scoring rubrics.
- Full experiment-design checklists.
- Detailed proposal-section writing guidance.
- Reviewer prompts for source grounding, gap/method challenge, experiment critique, or final coherence review.
- Harness-specific implementation details such as SDK event formats, Python adapter classes, or validator regexes.

## Skill Design

Skills solve "how to do the work" problems. They should be small procedural assets that make the lead agent better at repeatable research work without changing the run contract. A skill is not a run request, not a separate agent, and not a required completion gate.

Canonical skill content is maintained as shared Markdown assets, then packaged into the selected harness's skill layout. Claude runs receive `.claude/skills/<skill-id>/SKILL.md`; Codex runs receive `.agents/skills/<skill-id>/SKILL.md`. Skills are selected through configuration, and the example configs expose all core skills unless an experiment intentionally tests a smaller set.

The MVP ships five core skills:

| Skill | Purpose | Main contribution to `proposal.md` |
|---|---|---|
| **literature-grounding** | Guide search, source inspection, paper reading, source-faithful notes, and uncertainty handling as one evidence-chain procedure. | Stronger Related Work, closest-work discovery, and verifiable References. |
| **gap-selection** | Help the lead agent form multiple candidate gaps and select one that is grounded, important, specific, testable, topic-faithful, and technically approachable. | Stronger Introduction, clearer research question, and better proposal thesis. |
| **method-design** | Help the lead agent generate multiple concrete method candidates and select one that is specific, feasible, meaningfully different from closest work, and plausibly addresses the selected gap. | More defensible and implementable Methods. |
| **experiment-design** | Help the lead agent design experiments that test the central claim with suitable hypotheses, tasks or datasets, baselines, metrics, ablations, analysis plans, failure modes, and resource assumptions. | Stronger Experiments Design and Success Criteria. |
| **proposal-writing-and-final-check** | Help the lead agent assemble one coherent proposal and review it for grounding, proposal stance, closest-work delta, method specificity, experiment sufficiency, impact, and success/refinement/stop criteria. | Better final structure, narrative coherence, and semantic self-checking. |

## Core Skill Contents

### literature-grounding

This skill combines literature exploration and source discipline because search, reading, note taking, and grounded citation are one evidence chain.

It should cover:

- Generate several search angles from the topic before committing to a subproblem.
- Distinguish field-defining papers, recent papers, closest work, datasets, benchmarks, surveys, and technical reports.
- Decide when a search result is worth fetching and when a full-text paper read is needed.
- Avoid relying on titles, abstracts, snippets, or model memory for final related-work claims.
- Preserve reference metadata: title, authors if available, year if available, URL or stable identifier, retrieval method, and claim-support notes.
- Separate what a source says from the agent's hypotheses, extrapolations, or uncertainty.
- State retrieval limits honestly when evidence is incomplete.

### gap-selection

This skill helps the lead agent avoid shallow gaps such as "nobody has combined X and Y" unless the gap is grounded, important, and testable.

It should cover:

- Generate multiple candidate gaps from source notes and topic interpretations.
- Check whether each gap is supported by inspected literature.
- Evaluate importance, specificity, novelty potential, testability, topic faithfulness, and plausible technical routes.
- Reject gaps that are too broad, already saturated, unsupported, trivial, or impossible to test with reasonable experiments.
- Select one gap and explain why it is better than plausible alternatives.

### method-design

This skill helps the lead agent move from a selected gap to one concrete method rather than generic labels such as "use RAG", "add RL", or "improve the architecture."

It should cover:

- Generate multiple method candidates for the selected gap.
- Describe each method's mechanism, not only its category.
- Compare each method against closest work and state the technical delta.
- Evaluate feasibility, resource assumptions, data requirements, failure modes, and compatibility with the selected gap.
- Reject methods that are generic, unsupported, infeasible, too close to existing work, or not actually targeted at the gap.
- Select one method and develop it enough to be implementable in principle.

### experiment-design

This skill helps the lead agent design tests that would actually evaluate the proposal rather than listing generic benchmarks.

It should cover:

- State the central hypothesis or claim the experiments should test.
- Choose tasks, datasets, environments, or evaluation settings appropriate to the subfield.
- Select baselines, including closest-work baselines when available.
- Define metrics that correspond to the central claim, not just common metrics.
- Specify ablations that isolate key mechanisms of the proposed method.
- Include analysis plans, failure modes, negative results, resource assumptions, and validity threats.
- Define success criteria, refinement criteria, and stop criteria without reporting executed results.

### proposal-writing-and-final-check

This skill helps the lead agent turn the selected gap, method, and experiment plan into one coherent proposal.

It should cover:

- Write the proposal as the front half of an AI conference paper plus Impact Statement and Success Criteria.
- Make the Introduction state the problem, motivation, concrete gap, and proposal thesis.
- Make Related Work synthesize prior work, identify closest work, and explain the delta.
- Make Methods specific enough to be implemented in principle.
- Make Experiments Design test the central claim with clear baselines, metrics, ablations, and analysis plans.
- Make Impact Statement and Success Criteria non-boilerplate and decision-relevant.
- Final-check for fabricated citations, uninspected source use, fake empirical results, unsupported novelty claims, weak closest-work comparison, generic methods, vague experiments, and missing section intent.

## Skill File Shape

Skill assets should contain reusable procedures, not just reminders. A good skill Markdown file should include:

- **When to use it:** The stage or decision point where the skill is relevant.
- **Inputs to consider:** Topic, source notes, candidate gaps, selected gap, candidate methods, draft proposal, or available MCP outputs.
- **Procedure:** A concise method for doing the work, including comparison criteria where relevant.
- **Internal outputs:** Notes, tables, or decision summaries the lead agent may create as scratch material.
- **Contribution to final proposal:** Which proposal sections the skill should improve.
- **Failure modes:** Common mistakes the agent should avoid.

Skills should not require persisted intermediate artifacts in the MVP. They may encourage scratch notes, candidate tables, or source registries, but those are optional run traces unless promoted by a later architecture decision.

## Subagent Design

Subagents solve "another perspective should challenge this" problems. They are optional quality-improvement tools, not required architecture. The MVP must still be able to complete with only the lead agent, startup prompt, selected skills, and MCP.

Subagents should be reviewers or challengers, not stage owners. They should critique bounded inputs and return concise findings. They should not own literature review, method design, proposal writing, or final decisions.

The MVP ships four optional reviewer subagents:

| Subagent | Review task | Expected output |
|---|---|---|
| **source-grounding-reviewer** | Check whether related-work and gap claims are supported by provided source notes or inspected source content. | Unsupported claims, weak citations, missing closest-work checks, overstrong novelty claims, and uncertainty that should be stated. |
| **gap-method-challenger** | Challenge the selected gap and proposed method for importance, novelty potential, specificity, feasibility, and topic faithfulness. | Risks, genericness warnings, too-close-to-existing-work warnings, missing rationale, and possible refinements. |
| **experiment-plan-reviewer** | Review whether the experiment plan would actually test the central claim. | Missing baselines, weak metrics, absent ablations, unclear failure modes, weak analysis plans, and unclear success/refinement/stop criteria. |
| **proposal-final-reviewer** | Review the near-final `proposal.md` for coherence, proposal stance, unsupported overclaims, and section quality. | Fake-result phrasing, disconnected argument flow, boilerplate impact, missing decision criteria, and final revision suggestions. |

## Subagent Prompt Shape

Subagent prompts should be bounded and input-specific. A good subagent prompt should include:

- The limited review role.
- The exact input to review.
- The criteria to apply.
- A request for critique, not a full rewrite.
- A concise output format such as findings, severity, and suggested fix.
- A reminder that the lead agent owns final decisions.

Subagents should return review findings that the lead agent can incorporate or reject. They should not produce a replacement `proposal.md` unless a later architecture decision explicitly changes their role.

## Subagent Use Policy

The lead agent may call subagents when they are available and useful, especially after selecting a gap, after choosing a method, after drafting the experiment plan, or before finalizing `proposal.md`. The example Claude and Codex configs expose the same reviewer subagent set, using harness-specific packaging.

The lead agent must remain responsible for final decisions. If a subagent disagrees with the selected direction, the lead agent should either revise the proposal or explicitly resolve the issue in its own reasoning before finalizing. The final artifact should not mention subagent deliberations unless they directly improve the proposal content.

Subagents should not be exposed as a fixed chain such as literature reviewer to method designer to writer. That would recreate an externally orchestrated pipeline and weaken lead-agent ownership.

## MCP Guidance Inside The Quality Layer

The infrastructure layer configures MCP tools, but the quality layer explains how the lead agent should use them.

The startup prompt should provide the shortest reliable tool overview: which tools search, which tools fetch, and which tools produce full-text paper Markdown. Skills should provide detailed source-use procedure: when to search broadly, when to fetch, when to request full text, how to decide that a paper is central, and how to avoid overclaiming from incomplete retrieval.

The lead agent should treat search results as leads, fetched pages as inspectable evidence, and full-text Markdown as the preferred basis for central related-work claims when available.

## Quality Iteration Plan

The MVP treats this document as a starting point, not as final prompt science.

Quality should be iterated by changing one surface at a time:

- Compare startup prompt variants while keeping skills fixed.
- Compare skill wording while keeping the infrastructure fixed.
- Compare all-core-skills runs against smaller selected-skill profiles.
- Add subagent reviewers only after the lead-agent-only baseline is understood.
- Review generated proposals against the quality dimensions in [`PROJECT_OVERVIEW.md`](../concept/PROJECT_OVERVIEW.md).

The infrastructure should make these experiments easy by letting configuration select the harness, model profile, MCP profile, selected skills, and optional subagent profile without code changes.
