# Scientific Proposal Quality Rubric

This rubric is the single source of truth for judging proposal quality. The reviewer subagent applies it at each gate, and the offline evaluator applies it to the finished proposal. Score every dimension on an integer 1–10 scale using the anchored bands below. Cite specific evidence (a note section, a `references/` source, or a `proposal.md` line) for every score; a bare number is not acceptable output.

## Dimensions

### 1. Problem Value — is this a real problem the research community cares about?

- 1–3: Contrived, toy, trivial, or already solved; no sign anyone cares.
- 4–5: Real but niche or low-stakes; importance asserted but weakly grounded.
- 6–7: Legitimate problem with some active community interest; incremental importance.
- 8–10: Central, high-stakes open problem the field actively works on; importance grounded in literature.

### 2. Novelty — has anyone done something similar; genuine innovation vs. technical repackaging?

- 1–3: Already done; a rename or trivial variant of existing work — repackaging.
- 4–5: Incremental — an obvious extension, dataset/domain swap, or recombination of known components with no new mechanism.
- 6–7: A real but narrow delta — a new combination or setting with a modest new insight.
- 8–10: A genuinely new mechanism, problem framing, or evaluation, distinct from all closest work. Cap at ≤7 when novelty rests on absence of search results rather than inspected closest work.

### 3. Soundness — is the method sound, precisely specified (notation, equations, algorithms, complete derivations), and justified (why it is designed this way and why it should work)?

- 1–3: Unsound or unimplementable — internal contradictions, undefined core operations, or a prose-only sketch that cannot be built from the description.
- 4–5: Major gaps — key quantities or operations given only in prose, undefined notation, hidden assumptions, or derivations that skip steps; or design choices are arbitrary and no argument is given for why the method should work; significant ambiguity remains.
- 6–7: Mostly sound and partly formalized, but under-specified in places — some equations or algorithm steps missing, or the design rationale and why-it-works argument are thin; needing non-trivial filling-in.
- 8–10: Fully formalized, internally consistent, and justified — notation defined, every computed quantity given as an equation, procedures as algorithms, derivations complete with no skipped steps; each design choice is motivated and the causal argument for why the method works is explicit; implementable as written with no hidden assumptions.

### 4. Feasibility — will it actually solve the problem with high probability; any blocking flaws; feasible for an AI implementer?

- 1–3: Fatal flaw, or wildly infeasible even for an AI implementer.
- 4–5: Low probability of solving the problem (unaddressed blocking risk), or very heavy / borderline resource-time demands.
- 6–7: Plausibly solves the problem with residual risk; resources/time substantial but achievable for an AI implementer.
- 8–10: Likely solves the problem (causal bottleneck addressed, main risks mitigated) and is clearly efficient under reasonable AI-implementer resources/time.

Judge resource and time feasibility against an AI implementer that is much faster than human scientists: high experiment counts or large sweeps are not automatically infeasible.

### 5. Experimental Quality — does the setup truly test the hypothesis, are its choices (models, benchmarks, metrics, settings, baselines, ablations, analysis) justified, and is success reliable evidence while failure stays informative?

- 1–3: Cannot test the hypothesis — confounded, missing baselines/controls; results uninterpretable either way.
- 4–5: Partial test; major confounds uncontrolled or key baselines missing; or design choices are arbitrary and unexplained; success ambiguous and/or failure uninformative.
- 6–7: Tests the main claim with standard baselines and metrics; choices mostly justified but some thin; some confound or a subclaim untested.
- 8–10: Direct tests per subclaim, with controls and ablations that isolate the mechanism; every design choice (models, benchmarks, metrics, baselines, ablations, analysis) justified by what it tests; success is reliable evidence and failure is diagnostic and valuable to the community.

## Evidence-gate dimensions

The evidence gate judges the literature *survey* (`notes/literature_review.md`), not a hypothesis, so it scores these five dimensions instead of the five above. The same 1–10 bands, severity mapping, and minimum-binds rule apply: a survey that is a 9 on grounding but a 3 on depth is bound by the 3.

### A. Grounding — is every claim traceable to a real, inspected source, and accurately attributed?

A claim is grounded when it traces to an inspected source: a **paper summary backed by an extracted folder**, a raw `references/...` section, or a fetched page. The summary — which the summarizer produced by reading the paper — grounds claims, **including mechanism-level descriptions**; it is the default working layer, not a second-class source. A raw section is needed only to verify a figure the survey quotes **verbatim** (an exact number, equation, or ablation result), where paraphrase risk is real. Do not reward, or expect, raw reading beyond that — reading every raw section is the main avoidable cost of a run.

- 1–3: Claims rest on search snippets or model memory; little traces to any inspected source; possible fabrication or misattribution.
- 4–5: Mostly grounded, but several load-bearing claims trace to no inspected source, a verbatim-quoted figure is unverifiable, or a web-search-only source anchors a thematic or Open-Problem claim.
- 6–7: Load-bearing claims trace to an inspected source (summary, raw section, or fetched page) and are accurately attributed; a few are weakly sourced.
- 8–10: Every load-bearing claim traces to an inspected source, cited inline and accurately attributed; each figure quoted verbatim is confirmed against its raw section; weak, abstract-only, or web-search-only sources are labeled and kept non-load-bearing, and no Open Problem rests on an unextracted source. Grounding asks that cited claims be verifiable from inspected sources — not that every raw section be read.

### B. Coverage & Comprehensiveness — are all research lines, methods, and benchmarks present, and are the taxonomies complete?

- 1–3: Major research lines, milestone works, or the most relevant prior work are missing; no taxonomy, or a flat list in its place.
- 4–5: The main line is covered, but neighboring work, benchmarks, or negative evidence are thin, or a taxonomy node is empty or its categories overlap.
- 6–7: Broad coverage of lines, milestones, benchmarks, and neighboring work, with the problem and method taxonomies mostly complete; minor gaps stated.
- 8–10: Comprehensive and balanced — every research line, method-taxonomy category, and benchmark line represented by its seminal and recent works; both taxonomies complete (mutually distinct, jointly exhaustive, mapped to each other); remaining gaps disclosed — the breadth of a survey built over multiple passes, not a single read.

### C. Depth — are load-bearing methods explained at mechanism level?

- 1–3: One-line labels ("uses episodic memory"); no mechanism, no design choices, no numbers.
- 4–5: Topic-level description; mechanisms vague; few quantitative results; assumptions and limitations mostly absent.
- 6–7: Most load-bearing methods have a real mechanism description and some quantitative evidence; a few remain shallow.
- 8–10: Each load-bearing method is explained well enough to design against and differentiate from — operation, design choices, assumptions, requirements, quantitative results, and limitations, drawn from the paper's summary (which carries mechanism-level detail), with any verbatim-quoted figure confirmed against its raw section.

### D. Synthesis, Taxonomy & State of the Art — is it a consolidated, taxonomized survey with current SOTA, or a paper-by-paper outline?

- 1–3: A list of papers or a bullet outline; no taxonomy, no cross-work comparison, no state-of-the-art tracking.
- 4–5: Grouped by theme but still paper-by-paper within themes; the taxonomy is a flat list; little real comparison; benchmarks listed without their best results.
- 6–7: Idea-organized prose with real taxonomies and a consolidation table; some state-of-the-art results given; interpretation partial.
- 8–10: A genuine survey — problem and method taxonomies that map onto each other, an interpreted comparison across methods on shared axes, a state-of-the-art table whose numbers trace to inspected sources, and the field's dynamics analyzed; settled vs. contested made explicit.

### E. Open Problems — are unresolved problems mapped clearly, objectively, and grounded?

This dimension rewards a neutral map of what the field leaves unresolved — never advocacy. A survey that recommends, ranks, or selects a direction, calls a gap "promising" or "worth pursuing," or otherwise editorializes about what to do next is biased and scores here in the critical or major band regardless of how well it is written.

- 1–3: No grounded open problems, or they are generic ("needs more work"); or the survey editorializes — recommending, ranking, or selecting a direction, or framing gaps as next steps.
- 4–5: Some open problems named but weakly grounded, or the survey drifts into advocacy for particular directions.
- 6–7: Evidence-grounded open problems stated objectively, most tied to specific inspected evidence, with little or no advocacy.
- 8–10: Open problems and disagreements are comprehensive, each grounded in inspected evidence and stated neutrally — no direction recommended, ranked, or selected.

## Preconditions

Two hard preconditions are checked at every gate and are not scored. A precondition failure blocks the gate outright and invalidates the Novelty and Soundness scores until repaired.

- No fabricated, ungrounded, or misattributed literature: every reference and load-bearing prior-work claim traces to an inspected source that actually supports it. A load-bearing claim attributed to a source that does not contain it (a mis-citation) is a precondition failure, not a scored finding — it must be corrected, never shipped as a disclosed residual limitation.
- No fake or implied empirical results: the proposal designs experiments and states hypotheses but never claims unexecuted experiments produced results.

## Scoring and gating

Report a gate's dimensions as a vector — the five Dimensions above at the hypothesis_method and proposal gates, the five Evidence-gate dimensions at the evidence gate. Map bands to severity: 1–3 critical, 4–5 major, 6–7 minor, 8–10 strong.

The gate is bound by the minimum dimension, never the mean: a high average must not mask a fatal weakness. Record the binding (worst) dimension explicitly. Dimensions are weighted equally, but each stage has hard-blockers — Novelty and Soundness at the hypothesis_method and proposal gates, Depth and Synthesis at the evidence gate — and a critical score on a hard-blocker blocks regardless of the others.

**Ambition within rigor.** Minimum-binds deliberately caps a safe-but-incremental proposal: a method that is sound and feasible but low on Problem Value or Novelty is bound by those scores and must not pass as merely "good enough." Reward the most ambitious version the evidence can defend — a high-stakes problem addressed by a genuinely new mechanism — over a trivially safe one. Ambition never excuses weak grounding, however: an exciting but ungrounded, misattributed, or unsound claim still fails on its binding dimension or a precondition.
