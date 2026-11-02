---
name: literature-review
description: Use to survey the published literature for a research topic. Performs source discovery and reading, then writes an objective, comprehensive literature survey (notes/literature_review.md).
---

# Literature Review

This skill owns the evidence base: an objective survey of the published literature. It surveys what the field has done and what it leaves unresolved; it does not select a direction or draw conclusions about what to pursue.

## Inputs

- Research topic or problem.
- Optional constraints such as field, venue, source budget, or paper count.

## Workflow

1. Create `notes/` and use `references/` for extracted papers. Use `papers/paper_summaries/` for summaries produced automatically by `extract_paper` or manually by `summarize_paper`.
2. Define the review scope: target field, neighboring fields, time window, and what is out of scope.
3. Build multiple query families instead of one query:
   - direct topic queries
   - method/mechanism queries
   - benchmark/dataset/evaluation queries
   - robustness/failure/limitation queries
   - survey and position-paper queries
   - adjacent-domain analogy queries
4. When parallel exploration is available, use it before synthesis to cover the major review lanes; otherwise cover the same lanes sequentially:
   - direct prior work and neighboring work
   - surveys, benchmarks, datasets, and evaluation protocols
   - limitations, negative evidence, failures, and open problems
   - adjacent or cross-domain mechanisms
5. Keep lane findings structured: query-log rows, source candidates, extraction candidates, citation candidates, key claims, limitations, and unresolved uncertainties.
6. Run the MCP evidence pipeline described in `## MCP Tool Usage`: search, fetch, extract central papers, work from their summaries by default, open a raw section only to verify a figure you quote verbatim, and record tool provenance.
7. Use backward and forward citation chasing for key papers to track the research line. Do not rely only on web search.
8. Apply inclusion and exclusion criteria. Prefer peer-reviewed papers, arXiv papers with clear relevance, benchmark/dataset papers, surveys, official technical reports, and high-signal project documentation.
9. For each important paper, extract structured notes: problem, main claim, method or mechanism, dataset or setting, evidence type, key result, limitation, and relation to the topic.
10. Build a landscape map and draft the problem and method taxonomies: research lineage, milestone works, paradigm shifts, sub-problems, method categories, benchmarks, evaluation practices, and current state-of-the-art results.
11. Separate recent advances, durable trends, and the field's dynamics.
12. Map how the works relate to, build on, and differ from one another.
13. Identify disagreements, unresolved questions, weak assumptions, and open problems shown by the evidence.
14. Build `notes/literature_review.md` by iterating to convergence (see `## Building The Survey: Iterate To Convergence`) — draft, self-audit, fill gaps, and revise; converge quickly (usually one focused revision) rather than re-drafting for polish, but do not submit an un-revised first draft.
15. Hand off the converged survey to the `scientific-reviewer` evidence gate, which writes `notes/reviews/evidence_review.md`.

## MCP Tool Usage

The MCP tool descriptions define arguments and low-level behavior. This skill defines how the agent should use the tools during literature review.

### Availability Gate

- Before source discovery, confirm `web_search`, `web_fetch`, and `extract_paper` are available and that the configured MCP server is available. If any required discovery/fetch/scrape tool or credential is unavailable, stop before writing the review and report the blocker.
- Check whether automatic summaries are being created by `extract_paper`. If `summary_status` is `skipped` or `failed`, continue by reading extracted `sections/*.md` directly and record that the summary was skipped or failed. If `summarize_paper` is available, use it to rerun or repair missing summaries for central papers when practical.
- If an evidence tool reports that a source is unavailable or cannot be fetched/extracted, set it aside as unavailable and do not infer its content.
- Do not rely on model memory for factual literature claims. Every important claim must trace to a fetched page, extracted paper folder, paper summary backed by a scraped folder, or an explicitly marked low-confidence source.

### Evidence Flow

1. Search around the topic with `web_search`.
   - Use multiple query families, not one query.
   - When several query families are known, pass them together through the tool's batch input instead of making one tool call per query.
   - Capture links, snippets, paper titles, venues, years, recurring authors, benchmark names, and terminology.
   - For each useful hit, decide the next action: extract and inspect summary, fetch only, exclude, or defer.
   - Search results and snippets are leads. Do not treat them as evidence for factual claims.

2. Triage links before extraction.
   - Use `web_fetch` for abstracts, landing pages, benchmark pages, project pages, official documentation, and pages where the snippet is not enough to decide inclusion.
   - When multiple pages need independent triage, pass their URLs together through the tool's batch input.
   - Use fetched content to verify topic fit, publication date, source type, and whether the source should enter the extraction list.
   - Set weak, duplicate, out-of-scope, inaccessible, or unfetched sources aside; do not infer their content.

3. Build an extraction list.
   - Prefer extracting papers that may become milestone works, benchmark or dataset anchors, central prior work, strong neighboring work, or important negative evidence.
   - Deduplicate papers by title, arXiv ID, DOI, and obvious URL aliases before extraction.
   - Record why each paper is being scraped: lineage, benchmark, method, limitation, adjacent mechanism, or possible open challenge.

4. Scrape selected papers into `references/`.
   - When several selected paper URLs are ready, pass them together through `extract_paper`'s batch input after deduplication.
   - Treat each extracted paper folder as the raw local evidence store.
   - The raw paper content is stored as markdown files split from the full text of the paper, usually under `sections/`, with metadata in `paper_info.json`.
   - `extract_paper` also returns a summary path and `summary_status` when automatic summarization is attempted.
   - Keep your own provenance notes as you go (raw section paths and summary status) so you can cite them inline and in `## References`; you do not write a source-inventory section in the survey.

5. Read summaries first; read raw sections surgically.
   - The summary is your default working layer. For every extracted paper, read its summary (when `summary_status` is `created` or `cached`) and use it for understanding, routing, taxonomy placement, comparison, and any non-load-bearing context. The summaries are rich enough to carry mechanism-level understanding — do not re-read the raw paper just to learn what it does.
   - **Read summaries in small batches, not one per turn — and not all at once.** Each turn re-sends the whole transcript, so reading summaries one per turn re-sends the growing context once for every paper, which is the main reason a survey's turn count inflates cost. Read a few related summaries together in a single step (for example, the papers in one taxonomy branch), turn them into notes (step 6), then move on. But keep each batch small enough that the transcript stays well under the context window: pulling every summary in at once overflows the window and forces a context compaction, which costs more than the turns you saved. Batch only as far as it cuts turns; split a batch the moment it would bloat the context.
   - Open a raw split section only to verify a specific claim you will **cite** as load-bearing — an exact number, equation, ablation, dataset detail, or mechanism step you quote — and open only that one section file, not the whole paper. Most papers need zero or one raw section; a peripheral paper stays at summary level.
   - Reading every section of every paper is the main avoidable cost of this stage — it is what makes a run cost several times more than it should. Budget raw reads hard: a complete survey opens only a handful of raw sections in total (the specific figures it quotes verbatim). If you have opened more than a few, you are over-reading — work from the summaries.
   - If `summary_status` is `skipped` or `failed`, rerun `summarize_paper` when practical, or fall back to reading directly the specific raw sections you need.

6. Extract structured notes.
   - Build paper notes from the summary; verify against the specific raw section only for the load-bearing details you will cite (an exact result, equation, or mechanism step).
   - Fill paper notes with problem, method, evidence, benchmark, result, limitation, and role in the landscape.

7. Trace citations to recover research lines.
   - Backward chasing: inspect references cited by central papers. Include cited papers only when they are relevant to the topic, explain an ancestor method or benchmark, define a framing, or reveal a missing baseline or limitation.
   - Forward chasing: for important papers, look for later papers that cite them. Use this to find follow-up work, critiques, extensions, replications, benchmark reuse, and shifts in how the community interpreted the paper.
   - Do not extract every cited or citing paper. Triage citation candidates the same way as search hits: include, extract and inspect summary, fetch only, exclude, or defer.
   - Record the citation role: ancestor, competing line, benchmark lineage, extension, critique, replication, or downstream application.
   - Use citation chasing to build the `## Background and Foundations` and `## Thematic Review` sections.

8. Iterate through snowballing.
   - From fetched pages, summaries, and raw paper sections, collect references, benchmark names, datasets, method terms, and cited competing approaches.
   - Run follow-up `web_search` queries around those terms.
   - Repeat triage, extraction, optional summarization, and source-note extraction until the stop criteria are met.

### Inclusion Decisions

- Extract and inspect summary: central papers, long papers, surveys, benchmark papers, dataset papers, and papers likely to shape the final synthesis.
- Scrape only: useful papers whose raw sections are needed but whose role is narrow.
- Fetch only: project pages, benchmark pages, documentation, abstracts, and papers that are relevant but not central enough to scrape.
- Exclude: duplicates, weak sources, tangential sources, inaccessible sources, unfetched sources, and sources whose fetched content does not support the needed claim.

### Parallel Exploration Flow

Parallel exploration is a coverage strategy, not the final review. Whether exploration is parallel or sequential, preserve these lane-level artifacts:

- Query-log rows with links and snippets.
- Candidate source rows with inclusion recommendations.
- Candidate extraction list with reason for extraction.
- Backward and forward citation candidates for central papers, with relevance decisions.
- Important fetched evidence and unresolved uncertainties.
- Duplicate or alias warnings.

After lane exploration, deduplicate candidates, decide the final extraction set, inspect available summaries, open a raw section only to verify a figure you quote verbatim, resolve conflicts, and write one coherent `notes/literature_review.md`.

## How To Do A Good Literature Review

Use the required sections as outputs, not as the thinking process. A good review is built through iterative search, selective reading, extraction, and synthesis.

### Search Know-How

- Start broad, then branch. Use one or two broad queries to identify vocabulary, communities, venues, datasets, and recurring authors. Then split into query families.
- Search by concept, not only exact topic wording. Include synonyms, older terminology, benchmark names, method names, failure terms, and adjacent-domain terms.
- Mix source types. Look for surveys to map the area, milestone papers to understand the lineage, benchmark/dataset papers to understand evaluation, and recent papers to identify current movement.
- Use snowballing. Web search is for discovery, but citation chasing is for reconstructing the research line. For key papers, inspect references for earlier foundations and search for later papers that cite or build on them when tool support allows.
- Keep a query log. Record the query, intent, tool, useful hits, and what was missing. This prevents repeating shallow searches and makes the run auditable.

### Citation Chasing Know-How

- Start from central papers, not random papers. Good seed papers are surveys, benchmark papers, papers repeatedly found across query families, and papers that introduce a method, task, dataset, or framing.
- Backward citation chasing answers: what did this paper build on, what baseline or benchmark lineage does it inherit, and which assumptions were already present?
- Forward citation chasing answers: how did the community use, extend, critique, replicate, or move away from this paper?
- Follow citations selectively. Include a cited or citing paper only if it helps explain the topic's lineage, competing approaches, evaluation practice, trend, limitation, or open problem.
- Mark citation direction and role. A source discovered through citations should say whether it is backward or forward and whether it is an ancestor, extension, critique, replication, benchmark reuse, competing line, or downstream application.
- Stop citation chasing when new sources are mostly duplicates, tangential applications, or low-signal citations that do not change the landscape.

### Triage Know-How

- First pass: read title, abstract, venue/source, year, figures/tables if available, and conclusion. Decide whether to include, exclude, or defer.
- Second pass: for included papers, read introduction, method overview, experiments, limitations, and related work.
- Deep pass: only for milestone papers, central neighboring work, benchmark papers, or sources that strongly affect the survey's conclusions.
- Exclude aggressively but log the reason. Common reasons: out of scope, unfetched, inaccessible, weak source, duplicate, only tangentially related, or no evidence for the claim needed.

### Extraction Know-How

For each important source, extract facts in a comparable form:

- What problem does it address?
- What assumption or framing does it use?
- What method, mechanism, dataset, benchmark, or evaluation does it introduce?
- What evidence supports the claim?
- What changed because of this work?
- What limitation, blind spot, or unresolved question remains?
- How does it shape the landscape, not just its own narrow result?

Do not write only prose summaries. Prose can hide missing fields and makes synthesis harder.

### Synthesis Know-How

- Build the landscape before judging gaps. Explain the field's research lines, subtopics, evaluation infrastructure, and major transitions.
- Group papers by research line or problem, not by retrieval order.
- Identify why milestone works are milestones. A paper is a milestone if it introduced a widely used task, dataset, benchmark, method family, evaluation protocol, framing, or empirical finding that changed later work.
- Identify paradigm shifts by contrast. State what the field believed or optimized before, what changed after, what triggered the shift, and what problem remained unsolved.
- Treat datasets and benchmarks as scientific instruments. Ask what they measure, what they fail to measure, what assumptions they encode, and how they shape progress.
- Separate recent advances from current trends. A recent paper is not a trend unless multiple sources show movement in the same direction.
- Make uncertainty visible. Distinguish established findings, plausible interpretations, weak evidence, contested claims, and speculation.

### Parallel Synthesis Know-How

- Compare lane outputs for duplicated sources, conflicting claims, missing benchmarks, and inconsistent terminology.
- Synthesize across lanes. Do not paste lane notes together.

### Common Failure Modes

- Paper-list review: summarizing papers one by one without explaining the landscape.
- Recency bias: over-weighting new papers because they are easy to find.
- Survey dependence: trusting one survey's framing without checking primary papers.
- Benchmark blindness: missing that progress is driven by a dataset or metric artifact.
- Claim laundering: repeating a paper's claim without checking its evidence.
- Premature direction-setting: judging what is new or worth pursuing instead of objectively describing the landscape.
- Missing negative evidence: ignoring failures, null results, limitations, and critiques.

### Stop Criteria

Stop the literature review when these are true, or explain why they cannot be met:

- The `## Problem Taxonomy` and `## Method Taxonomy` are complete: every major line and category is named and populated.
- Milestone works and paradigm shifts are justified.
- Major datasets and benchmarks are mapped, each with its current state-of-the-art result traced to an inspected source.
- Recent advances, durable trends, and the field's dynamics are separated and evidenced.
- Open problems are grounded in cited evidence and stated objectively.
- All relevant work is mapped and compared, with no direction advocated.
- A full refinement pass adds nothing material (convergence).

## Source Sufficiency

The target is comprehensive coverage of the field, bounded by relevance — not a fixed count. Unless the user sets a smaller budget:

- Cover every major research line, method-taxonomy category, and benchmark line; represent each by its seminal and its most recent load-bearing works. For a mature topic this is typically 15–20 inspected works. Be stricter about full extraction than about inspection: extract — and so carry a summary for — only the ~12–18 core works you will actually read in depth and cite as load-bearing; cover the rest from search results, abstracts, or a single fetched page without extracting them. Every extra extraction is a summary you then carry through every later pass, so a bloated extraction set is the main avoidable context cost of this stage. Go deep on the load-bearing works and note the rest.
- Include neighboring and competing work, surveys, benchmark/dataset papers, negative evidence and limitations, and adjacent-domain mechanisms where cross-domain transfer matters.
- Within a branch, stop when new searches return duplicates (saturation); across branches, prefer breadth — do not let one branch crowd out the others.
- If the available literature is genuinely sparse, say so explicitly and lower confidence. Never pad with weak or irrelevant sources, and never inflate coverage with web-search-only entries.

## Parallel Exploration Coverage

When parallel exploration is available, use it to increase breadth before synthesis. The skill only requires coverage and artifact quality:

1. Cover the direct-work, survey/benchmark, limitation/failure, and adjacent-mechanism lanes.
2. Keep lane outputs structured: query-log rows, URLs or DOIs, fetched status, candidate extraction list, key claims, limitations, and relevance.
3. Deduplicate sources across lanes before batch extraction when practical.
4. Record extracted paths, section paths, summary paths, and summary statuses for sources that survive triage.
5. Verify high-impact claims against fetched pages or scraped sections before using them.
6. Resolve conflicts by checking evidence type, recency, venue/source quality, and directness to the topic.
7. Merge lane outputs into one coherent synthesis.

Parallel exploration may discover and summarize sources, but it does not by itself produce the synthesis.

## Building The Survey: Iterate To Convergence

A survey is refined, not dumped out in one pass. A single read-then-write pass yields a competent first draft, never a comprehensive survey, so revise it before the gate — but converge fast: the goal is a complete survey, not an endlessly polished one. Do not bring an un-revised first draft to the evidence gate.

Work in phases, and run the refinement loop until it converges — which should be quick:

1. **Map** — breadth-first searches across all query families to enumerate the field's research lines, sub-problems, method families, and benchmarks. Draft the `## Problem Taxonomy` and `## Method Taxonomy` skeletons from what you find. This fixes the *shape* of the survey before you write it.
2. **Harvest** — depth-first within each taxonomy branch: read the seminal, the recent, and the competing papers at mechanism level; chase citations backward to foundations and forward to the current state of the art. Stop a branch when new hits are duplicates (saturation), but cap depth so no single branch crowds out the rest — prefer breadth across branches over exhaustive depth in one.
3. **Draft** — write the survey: populate both taxonomies, the thematic deep-dives, the comparison matrix, the state-of-the-art table, the dynamics, and the open problems.
4. **Critique and close gaps** — run the self-audit below against your own draft; for every gap, run targeted searches, read, and rewrite the affected section.
5. **Repeat step 4 only while it changes something material** — a missing work, an absent mechanism, a stale state-of-the-art number, a broken taxonomy node, an unsharp comparison. Stop the moment a critique pass surfaces nothing load-bearing: that is convergence, and one focused revision after the first draft usually reaches it. Cap self-revision at two passes — the evidence gate, not endless self-polishing, is the quality backstop, and it is a gate finding that reopens the survey for a further, targeted pass. Do not re-draft for polish.

Self-audit checklist — run it every refinement pass:

- **Comprehensiveness**: is any research line, method-taxonomy category, or benchmark line missing or represented by too few papers? Is a seminal work or a very recent one absent?
- **Taxonomy**: is every taxonomy node populated? Are the categories mutually distinct and jointly exhaustive — no overlap, no gap? Does the method taxonomy map onto the problem taxonomy?
- **State of the art**: is the best result on each benchmark the current one? Did a newer method overtake it? Does every state-of-the-art number trace to an inspected source?
- **Consolidation**: does each table interpret rather than list? Are methods compared on shared axes?
- **Dynamics**: are the accelerating, consolidating, contested, and fading lines captured, each with evidence?
- **Depth**: is every load-bearing method explained at mechanism level, with numbers?

## Output: write a neutral survey, not an outline

Write `notes/literature_review.md` as an **objective survey of the field** — connected analytical prose organized around ideas, the way a good standalone related-work survey reads. It is comprehensive, neutral, and unbiased: its only job is to describe what the literature establishes and what it leaves unresolved. It is not a process log, a source inventory, or a list of papers; the search, triage, extraction, and citation work above is how you *build* the evidence and does not appear in the review. Cite sources inline by short name (for example, "EM-LLM") and list them once under `## References` with the inspected source you relied on.

**Stay objective. The survey must say nothing about hypothesis generation, research directions to pursue, candidate questions, novelty, or which open problem is promising, important, strongest, or worth investigating. Do not recommend, rank, prioritize, advocate, or select.** State open problems as objective facts grounded in the evidence; a later stage — not this survey — decides what to pursue. If a sentence reads as advice about what to do next, delete it.

**Use these exact `##` headings, verbatim and in this order — do not rename, renumber, merge, reorder, add, or omit any `##` section.** The only headings you choose yourself are the `###` subsections under `## Thematic Review`. Write the analytical sections as prose; the structured tables are the two taxonomies, the comparison matrix, and the state-of-the-art table.

- `# Literature Review: <topic>`
- `## Scope` — one short paragraph: what the survey covers, the time window, and what is out of scope.
- `## Background and Foundations` — the intellectual lineage in prose: where the field came from, the foundational ideas and milestone works, and how the framing of the problem evolved into its current form.
- `## Problem Taxonomy` — a structured decomposition of the topic into the distinct sub-problems and challenge categories the field works on (the problem space). Present it as a short hierarchy or table; each node names a problem and the works or lines that attack it. This is what the field is trying to solve.
- `## Method Taxonomy` — a structured classification of the approaches by their defining design choices (the solution space), organized so the categories are mutually distinct and jointly cover the field, and mapped onto the problems above. Each category names its defining property and the works in it. This is the map the `## Thematic Review` follows.
- `## Thematic Review` — the heart of the survey: one `###` subsection per method-taxonomy category (you title these). Each is analytical prose that (a) introduces the sub-problem; (b) explains the dominant approaches at the **mechanism level** — the key operation, algorithm, or representation each uses, its design choices, its assumptions, and what it requires (training? architecture change? external store?); (c) **compares** the approaches, with quantitative evidence where the sources give it (datasets, metrics, magnitudes), tracing how later work built on or reacted to earlier work; and (d) states each approach's limitations and the failure modes the evidence shows. Cite works in the flow of the argument; never give one paragraph per paper.
- `## Comparative Synthesis` — the consolidation: a comparison matrix across the load-bearing methods on the taxonomy's dimensions, followed by prose interpreting it — what is common, what genuinely differs, what is settled, and what is contested.
- `## Benchmarks and State of the Art` — datasets and benchmarks treated as instruments (what each measures, what it fails to measure, what confounds it carries), classified by the capability they test, plus a **state-of-the-art table**: for each major benchmark, the metric, the current best published result, the method or model that holds it, and the source. Every state-of-the-art number must trace to an inspected source.
- `## Trends and Community Dynamics` — the field's trajectory, objectively: which lines are accelerating (evidenced by follow-up volume), which are consolidating into standard practice, which are contested between competing claims, and which are fading — each grounded in the evidence. Describe the dynamics; do not recommend a direction.
- `## Open Problems` — what the literature objectively shows is unresolved, including open disagreements, each grounded in inspected evidence. State the problems neutrally; do not rank them, recommend any, or frame them as next steps.
- `## References` — the inline-cited works, each with the inspected source you relied on (the extracted paper's summary by default, plus the raw `references/...` section for any figure you quote verbatim), so the reviewer can verify grounding. Any source you could only find through web search (not extracted) must be explicitly labeled `(web-search only)` and kept off load-bearing claims; every source that anchors an `## Open Problems` entry or a state-of-the-art number must be an extracted source, not web-search-only.

The comparison matrix in `## Comparative Synthesis` adapts its columns to the topic's real design axes:

```text
| Method | Memory representation | Write / compression policy | Read / retrieval policy | Provenance preserved? | Training / arch cost | Evidence (dataset → result) | Key limitation |
```

## Writing The Survey

A survey reads as a coherent argument, not a set of notes. Hold to these:

- **Organize by idea, not by paper.** The unit of a section is a sub-problem or an approach; papers are cited inside the argument. If a section is a run of "Paper X does Y. Paper Z does W." sentences, it is an outline — rewrite it.
- **Go to mechanism depth.** A reader should finish a deep-dive able to explain *how* each method works and *why* it differs from its neighbors, not just its topic. One-line labels such as "uses episodic memory" are insufficient; describe the actual operation from the paper's summary, which carries mechanism-level detail — and verify a figure you quote against its raw section.
- **Consolidate and compare.** Put methods side by side on shared axes and say what the comparison means. Synthesis is the point; per-paper description is only the raw material.
- **Build real taxonomies.** The problem and method taxonomies are structured classifications, not lists: categories must be mutually distinct, jointly cover the field, and map onto each other. A flat run of bullets is not a taxonomy.
- **Track the state of the art.** For each benchmark, name the current best published result and the method or model that holds it, traced to an inspected source — not just that some method "improves" something.
- **Be quantitative where the evidence allows.** "Improves on LongBench" is weak; "+N points on LongBench task T over InfLLM, training-free" is survey-grade.
- **Be analytically critical, not directional.** State strengths and weaknesses and where evidence is strong versus thin, as objective assessment — never as a case for pursuing any particular next step.
- **Stay neutral.** Describe what the evidence shows and what is unresolved; say nothing about hypotheses, research questions, novelty, or which open problem is worth pursuing — that is a later stage's job, not the survey's.
- **Prose, not bullets.** Write paragraphs with transitions. Reserve bullets for true enumerations and the comparison matrix only.

The bar, concretely:

- Outline (reject): "EM-LLM uses surprise-based event segmentation and contiguity-aware retrieval, improving over InfLLM on LongBench."
- Survey (write this): "EM-LLM segments the token stream into episodic events wherever next-token surprisal spikes, then refines those boundaries with a graph-cut that maximizes intra-event cohesion; each event becomes a retrievable unit, and retrieval blends similarity with contiguity and recency, deliberately mirroring human episodic memory. It is training-free and beats InfLLM by N on LongBench task T, but its segmentation signal is a perplexity heuristic untethered to the reasoning task — a limitation later sections build on."

## Quality Checklist

- The review reads as a survey: connected analytical prose organized by idea, not a paper-by-paper list or an outline of bullets.
- It uses the exact required `##` headings, in order, with the method families as `###` subsections under `## Thematic Review`.
- The `## Problem Taxonomy` and `## Method Taxonomy` are real taxonomies — mutually distinct, jointly exhaustive, mapped to each other — each node populated with its works, not a flat list.
- `## Benchmarks and State of the Art` classifies benchmarks by what they test and gives a state-of-the-art table whose numbers trace to inspected sources.
- `## Trends and Community Dynamics` captures the field's trajectory (accelerating / consolidating / contested / fading) with evidence, objectively.
- Coverage is comprehensive across every research line, method category, and benchmark line — the product of repeated refinement, not a single pass; any remaining gap is stated.
- It is neutral and objective: it does not recommend, rank, prioritize, or select a research direction or question, and says nothing about hypothesis generation, novelty, or what is "promising," "strongest," or "worth pursuing."
- `## Open Problems` states what the literature objectively leaves unresolved, grounded in evidence — as fact, not as advice about next steps.
- Every load-bearing method is described at mechanism level — operation, design choices, assumptions, requirements, key quantitative results, and limitations — not a one-line label.
- A comparison matrix plus interpretive prose consolidates the load-bearing methods on shared dimensions; the survey states what is common, what differs, what is settled, and what is contested.
- Claims are quantitative where the sources allow (dataset, metric, magnitude), not vague ("improves performance").
- Sources are real and inspected; every load-bearing claim traces to an inspected source — a paper summary backed by an extraction, a raw `references/...` section, or a fetched page — cited inline. The summary is the default and grounds mechanism-level claims; only figures quoted verbatim (exact numbers, equations, ablations) are checked against the raw section.
- Coverage spans the main research lines, milestones, benchmarks, negative evidence, and adjacent mechanisms; any coverage gap is stated.
- Benchmarks are treated as instruments: what each measures, what it misses, and its confounds.
- Weak, abstract-only, or unfetched sources are labeled and kept non-load-bearing; the review never infers their content.
- No process artifacts appear in the review (no query log, no source inventory, no exclusion table); provenance lives in inline citations and `## References`.
