---
name: "literature-review"
description: "Specification for producing a comprehensive research topic survey. Defines the 8 required knowledge dimensions, writing standards, and completeness test."
---

# Literature Review Skill

## Core Principle

A comprehensive literature review provides the **full landscape** of a research topic — not just one thread, but competing approaches, community dynamics, and what has been absorbed vs. merely published. Surface-level coverage (reading 10 papers from one thread) is insufficient. The goal is coverage deep enough that a downstream research agent can confidently assess any proposal's potential without needing additional searches.

---

## Output: `literature_review.md`

A self-contained survey that assumes the reader has strong ML/DL knowledge but **ZERO domain knowledge** of the research topic. After reading this document alone, the reader should be able to:
- Explain the problem precisely to a colleague
- Categorize any new paper into the right paradigm
- Design a credible experiment with appropriate baselines and metrics
- Identify promising research gaps

---

## 8 Required Knowledge Dimensions

**Balance rule:** Each dimension should receive comparable depth. If your open challenges section is <20% the length of your history section, it's underweight. A lopsided review means you stopped searching too early for some dimensions.

### 1. History and Paradigm Shifts
- When did this topic first attract serious research attention?
- What were the early approaches and what paradigm were they in?
- What key breakthroughs or paradigm shifts changed the field's direction? WHY did these shifts happen (what limitation of the prior paradigm drove the change)?
- What approaches were tried and abandoned? Why did they fail?

### 2. Milestone Works and Their Impact
- What are the foundational papers that defined the field?
- What are the landmark papers that the community has **absorbed** (widely cited, built upon, used as baselines)?
- For each milestone: what did it introduce, why was it important, and what did it enable?
- Distinguish clearly between "widely adopted" (high citations, used as building blocks) and "exists but not absorbed" (low citations, recent, not yet part of the community's toolkit).

### 3. Core Tasks and Research Problems
- What concrete problems/tasks does this research area address? Specify inputs and outputs clearly.
- Why are these problems technically hard? What makes naive approaches fail?
- What real-world applications drive interest?
- Are there sub-problems or task variants the community distinguishes?

### 4. Recent Trends and Advances
- What directions are gaining momentum NOW (last 1-2 years)?
- What new methods, architectures, or paradigms are emerging?
- Are there new workshops, benchmarks, or shared tasks indicating rising interest?
- Is there convergence (field settling on one approach) or divergence (multiple active fronts)?

### 5. Community Landscape
- How large is the community? (Estimate: papers/year, distinct research groups)
- Academic vs. industry balance — who drives the field?
- Key research groups and their focus areas
- Key researchers — who are the leading figures? What are their recent works?
- Primary venues: which conferences, journals, workshops?
- Growth trajectory: growing, stable, or declining? Evidence?

**Important:** Identify the top 5-10 researchers and research groups in this area early. Then search for each one's recent (last 1-2 year) publications individually — this is how you find cutting-edge work that keyword searches miss. Leading groups often have the most current and impactful work. Don't just list names; follow their publication trails and incorporate what you find into the relevant dimensions.

### 6. Baseline Methods and Absorbed Solutions
- What methods does a reviewer EXPECT to see as baselines in a new paper?
- Which methods are the de facto comparison targets right now?
- What is considered a "fair" comparison? (Same backbone? Same data? Same compute?)
- What would make a reviewer say "the experimental section is incomplete"?
- Which methods are truly **absorbed** by the community (everyone knows and uses them) vs. which merely exist?

### 7. Datasets, Benchmarks, Metrics, and Evaluation Standards
- What are the standard datasets? For each: name, size, modality, what it contains, how widely adopted.
- What are the standard metrics? What do they measure? Any known issues or debates?
- What is "good" performance? Give concrete SOTA numbers with context (e.g., "42.3 mIoU on X is SOTA; top-5 gap is ~2 points").
- What evaluation gaps exist? What can't be measured with current benchmarks?

### 8. Open Challenges and Future Directions
- What are the acknowledged unsolved problems? Be specific — name concrete technical barriers, not vague "needs improvement" statements.
- What limitations are cited repeatedly across papers?
- What are the biggest disagreements or unresolved tensions in the community?
- Where is the field heading in the next 1-2 years? What concrete directions are people investing in?
- What has the community collectively learned DOESN'T work? What approaches looked promising but were abandoned or shown to be limited?
- What gaps exist between what current methods achieve and what applications actually need?

**This dimension requires as much depth as history or milestones.** A thin open-challenges section means you haven't read enough discussion/conclusion sections of recent papers.

---

## Writing Standards

**Write as narrative survey, not paper catalog.**

BAD:
```
### SparseOcc (2024, CVPR, 112 citations)
- Method: uses sparse queries instead of dense grids
- Results: 74.9% FLOPs reduction
```

GOOD:
```
The computational cost of dense 3D grids motivated a shift toward sparse
representations in 2023-2024. SparseOcc (Li et al., CVPR 2024, ~112 citations)
demonstrated that sparse 3D processing can reduce FLOPs by 74.9% while improving
accuracy. The key mechanism is sparse query initialization that avoids redundant
computation in empty space. This established that efficiency need not sacrifice
accuracy — as long as sparsification preserves geometric structure.
```

The difference: narrative explains mechanisms, draws conclusions, and connects findings to the field's trajectory.

**Reference table** — maintain at the end of `literature_review.md`:
```markdown
## Reference Table
| Paper | Year | Citations | Venue | Key Contribution | Local Path |
|-------|------|-----------|-------|------------------|------------|
```

Each entry MUST include the local path (`paper_summaries/<name>.md` or `scraped_papers/<folder>/`) so downstream agents can access the source material directly.

---

## Completeness Test (MANDATORY before finalizing)

Your `literature_review.md` must be able to answer ALL of these:

1. What is the concrete input/output of this problem?
2. What makes it technically hard? What are the 2-3 core challenges?
3. What real-world applications need this?
4. What are the top 3-5 active methods/paradigms? How do they differ mechanistically?
5. What approaches were tried and failed? Why?
6. What is current SOTA? On which benchmark? What number?
7. What are the top 2-3 datasets used in recent papers? What do they contain?
8. What metrics are standard? What's "good" vs. "SOTA" performance?
9. What baselines MUST a new paper compare against?
10. How large/active is the community? What venues? Growing or declining?
11. What are the 2-3 biggest acknowledged open problems?
12. Where is momentum heading? What new directions are emerging?
13. What has the community absorbed (widely used) vs. what merely exists (published but not adopted)?

If any question is unanswerable from the review, that's a gap to fill before finalizing.

---

## Workspace Files

- **`research_plan.md`** — Create BEFORE first search. Your roadmap: threads to investigate, questions to answer, search strategy notes. Must include a **subfield checklist**: enumerate all major paradigms/subfields you expect this topic to contain, and track which ones you've investigated. Update after each cycle.
- **`literature_review.md`** — The deliverable. Create after first scrape cycle, update after EVERY Scraper call. Never defer updates.
- **`key_findings.md`** — High-confidence conclusions as they emerge. Insurance against context compaction.
- **`coverage_gaps.md`** — Self-assessment checkpoint. Write before finalizing to verify no major subfield has zero coverage and no dimension is underweight.

---

## Core Workflow

### Phase 1: Breadth Scan
Before going deep on any single thread, do a broad survey pass:
1. Search for recent survey papers on this topic.
2. From surveys and initial results, enumerate ALL major subfields/paradigms in `research_plan.md`.
3. Do at least one search per identified subfield to confirm it exists and find key papers.
4. Write the first draft of `coverage_gaps.md` identifying which subfields need deeper investigation.

### Phase 2: Deep Dives
For each subfield with weak coverage:
1. Search specifically for that subfield's recent (1-2 year) advances.
2. Identify key researchers in that subfield and search for their recent publications individually.
3. Scrape and summarize key papers.
4. Update `literature_review.md` after every Scraper call.

### Phase 3: Dimension Balance Check
Before finalizing:
1. Re-read your `literature_review.md` and check dimension balance (the balance rule above).
2. If open challenges, community landscape, or any other dimension is thin relative to history/milestones, do targeted searches to fill it.
3. Read discussion/conclusion/limitation sections of recent papers specifically for open challenges content.

### Anti-Convergence Rule
After your initial searches, always ask: "What subfields of this topic have I NOT yet found papers for?" Search for those specifically. A comprehensive review means covering all major paradigms, not just going deep on one. Scraping 30 papers from one lineage doesn't help if another major paradigm has zero coverage.

---

## Context Compaction Recovery

If context compaction happens:
1. Re-read: `research_plan.md` → `key_findings.md` → `literature_review.md`
2. Trust your earlier conclusions in `key_findings.md`
3. Continue from unchecked items in `research_plan.md`
4. Do NOT restart from scratch
