## CONTEXT BUDGET WARNING (take durable notes now)

Your current context is **{token_count} tokens** out of **{max_context_tokens}** (~**{pct:.1f}%**).

Before context compaction happens, **persist important information now** (so you don't lose it):

### Update Your Memory Files (`./memories/`)

1. **Update `./memories/MEMORY.md`** with:
   - Your current focus and what you're working on
   - Portfolio status (finalized/in-progress proposals)
   - Key insights from your recent work
   - Important process notes

2. **Update `./memories/lessons.md`** if you learned something reusable:
   - Tool usage tips (one-liners)
   - Research process rules
   - Mistakes to avoid

### Also persist task-specific notes

- Update **`./research_topics/<topic>/notes.md`** for topic-specific progress
- Update **`./research_topics/<topic>/wonder_log.md`** if you have new anomalies, contradictions, or hypotheses
- Update **`./research_topics/<topic>/claim_audit.md`** if you have verified or weakened key paper claims
- Update **`./research_topics/<topic>/synthesis.md`** if you have new abstraction, mechanism, or promotion-gate reasoning
- Update **`./research_proposal_drafts/<slug>/notes.md`** for proposal-specific progress
- If there are critical tool results you must not lose, **summarize them into your notes** (tool outputs are not durable)
- If there are any **important papers you only have as URLs** (not yet scraped/summarized), **scrape and summarize them now**

### What to save

- Key citations/URLs and 1-2 line takeaways
- The current best hypothesis + decision criteria
- Which paper claims are raw-paper-verified vs summary-derived
- Any partial results, errors, or reasoning you'll need later
- Lessons learned and mistakes to avoid (add to lessons.md as one-liners)

This reminder is intentionally early so you can save state **before** the compaction threshold is reached.

**After compaction, your MEMORY.md will be auto-injected to restore your context.**
