---
name: "writing-targeted-findings"
description: "How to maintain targeted_findings.md as a proposal-specific search/read evidence log for the research agent."
---

# Writing `targeted_findings.md`

`targeted_findings.md` is a running evidence ledger for proposal-specific follow-up research. It is not a polished report and not a place for final factor verdicts.

The purpose is to make the targeted-search process auditable:

- what gap was being filled;
- what queries or local retrieval actions were run;
- which sources were selected;
- whether each source was actually read;
- what source-grounded findings changed or confirmed the proposal positioning;
- what remains uncertain.

## Hard Sequencing Rule

Create `targeted_findings.md` before the first targeted web search or scraper call. Initialize it with the planned cycles from `evidence_gaps.md`, while leaving room for later proposal-relevant leads discovered during reading.

Treat each query/source trail plus source selection/scrape/fetch/read as one targeted search/read cycle. A cycle may start from an evidence gap or from a new lead discovered while reading. A cycle may use multiple tools. Once that cycle has produced search results and any selected sources have been read or marked unavailable, update `targeted_findings.md` before starting another targeted search/read cycle or drafting `analysis_report.md`.

Do not wait until all searches are complete to write this file. If a search fails, returns only snippets, or produces sources you decide not to use, record that too.

## Source Status Labels

Use explicit evidence status labels:

- `read locally`: existing `paper_summaries/` or `scraped_papers/` content was opened/read.
- `scraped and read`: a new source was fetched/scraped and the resulting paper summary or scraped content was read.
- `fetched and read`: a webpage was fetched and read.
- `search-only / unverified`: the source was seen only in search results or snippets. It may guide future search but must not support strong claims.
- `failed / inaccessible`: attempted retrieval failed; record the failure briefly.

## Recommended Format

```markdown
# Targeted Findings Log

Date initialized: [cutoff date]
Proposal: [short proposal label]

This file is updated after each targeted search/read cycle. Claims intended for
`analysis_report.md` must be backed by sources marked `read locally`,
`scraped and read`, or `fetched and read`.

## Planned Cycles From `evidence_gaps.md`

- G1: [gap] -> planned queries / local reads
  - Why `literature_review.md` is insufficient:
  - Expected report impact if resolved:
- G2: [gap] -> planned queries / local reads
  - Why `literature_review.md` is insufficient:
  - Expected report impact if resolved:

---

## Cycle C1 - [short purpose]

### Gap(s)
- [gap id and short description]

### Rationale
- Why this was worth checking for this proposal:
- Whether it adds new information beyond `literature_review.md`, confirms an
  important point, or shows no useful change:
- What should change in `analysis_report.md` if this cycle resolves the issue:

### Actions Taken
- Local reads:
  - [path]
- Queries run:
  - `[query]`
- Fetch/scrape actions:
  - [URL or path]

### Sources Selected
1. **[Title]** ([date if known]; [citation count if available])
   - URL/path: [URL or relative path]
   - Why selected: [specific reason tied to this proposal]
   - Evidence status: [read locally / scraped and read / fetched and read / search-only / failed]

### Key Findings
- **Finding:** [one atomic claim]
  - Evidence: [source title/path and the specific source-grounded fact]
  - Relevance: [which proposal claim, gap, or report section this affects]
  - Direction: [supports / limits / contextualizes / leaves unresolved]
  - Confidence: [source-grounded / inferred from source / weak or search-only]

### Update Relative to `literature_review.md`
- [What this cycle adds, narrows, corrects, or leaves unchanged relative to the
  shared review.]

### Report Fields Supported
- Opportunity Leverage:
- Knowledge Delta:
- Citation Surface:
- Diffusion Pathway:
- Credibility Gate:
- Field Dynamics:
- Landscape Placement / Specific Increment:

### Unresolved Uncertainty
- [Open question that remains after this cycle]

### Next Action
- [next planned gap/search/read, or "none before report drafting"]
```

## Key Finding Rules

Each key finding should be atomic. Do not combine multiple claims into one long bullet.

Each key finding should name or point to its source. A reader should be able to trace the claim back to a source without searching the whole file.

Use `Direction` to describe how the evidence functions, not to score the proposal:

- `supports`: evidence makes a proposal claim more plausible.
- `limits`: evidence creates a boundary, caveat, or counterpressure.
- `contextualizes`: evidence places the proposal among nearby work without clearly supporting or limiting it.
- `leaves unresolved`: the search/read cycle did not settle the issue.

Do not write factor ratings, strongest/weakest factor labels, bottleneck verdicts, overall potential, win/loss language, or revision recommendations.

## Final Check Before `analysis_report.md`

Before drafting `analysis_report.md`, re-read `targeted_findings.md` and verify:

- every targeted search/scrape cycle has an entry;
- every cycle states why it was worth checking for this proposal and how it relates to `literature_review.md`;
- every cycle states what report field or section it can change;
- every strong claim in the planned report is supported by a read source;
- search-only snippets are labeled unverified;
- unresolved uncertainty is preserved rather than silently converted into a conclusion.
