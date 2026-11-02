# Cutoff-aware search backend

Both the ideation baselines and the evaluator's literature agents must see the literature **as it stood on a cutoff date**. If the agent can read a paper published after the cutoff, a benchmark built on 2025–2026 papers is trivially leaked. In our experiments this was enforced by an internal HTTP service, the *Web Tools Service* (WTS), which is **not included in this release**: it depended on private infrastructure.

This page specifies the interface the released code expects, so that you can implement a compatible backend, and lists public components that cover each piece.

## Who calls it

| Client | Code | Endpoints used | Env vars |
|---|---|---|---|
| Baselines (`scholar-gateway-mcp`) | `baselines/src/research_ideation_agent/scholar_gateway/client.py` | `/search`, `/fetch`, `/scrape_v1` | `WTS_BASE_URL`, `WTS_API_KEY` |
| Evaluator (literature-review and research agents) | `evaluator/toolkit/tool_handlers/*_dated_handler.py` | `/search`, `/fetch`, `/scrape_v1` (falls back to `/extract`) | `WEB_TOOLS_SERVICE_URL`, `WEB_TOOLS_SERVICE_TOKEN` |

The evaluator's **judge-only** path (`scripts/run_literature_archive_judge_manifest.py`) reads a pre-built literature workspace and needs no search backend. The released calibration and ideation data include those workspaces, so you can reproduce judge results without implementing anything on this page.

All endpoints take a JSON body and authenticate with `Authorization: Bearer <key>`.

## Endpoints

### `POST /search`

```json
{"query": "transformer attention mechanism", "cutoff_date": "2025-11-11",
 "pages": 1, "strict_filtering": true, "restrict_domains": true, "mode": "rules"}
```

Returns results published on or before `cutoff_date`:

```json
{
  "success": true,
  "results": [{"title": "...", "url": "https://arxiv.org/abs/1706.03762", "snippet": "...", "date": "2017-06-12"}],
  "agent_result": "Found 8 results.\n\n1. ...",
  "meta": {"mode": "rules", "raw_count": 10, "filtered_count": 2, "latency_ms": 2500}
}
```

- `agent_result` is the plain-text rendering the agent actually sees.
- `strict_filtering: true` drops any result whose date cannot be confirmed. Every baseline config sets it. The evaluator sends only `query` and `cutoff_date`, so make strict filtering your backend's default: precision of the time boundary matters more than recall.
- `restrict_domains: true` limits results to scholarly and research sources (arXiv, OpenReview, ACL Anthology, ACM, IEEE, conference proceedings sites, major publishers, preprint servers, lab and company research blogs, Wikipedia, GitHub, Hugging Face, Semantic Scholar, DBLP).

### `POST /fetch`

```json
{"url": "https://arxiv.org/abs/2301.00001", "cutoff_date": "2025-11-11", "mode": "rules", "format": "markdown"}
```

Returns the page text in `agent_result` with `meta.page_date`. If the page is dated after the cutoff, it responds with `success: false` and `agent_result: "Filtered: page date ... is after cutoff ..."`. For PDF URLs it tells the agent to use the extraction endpoint instead.

### `POST /scrape_v1` (and `/extract`)

```json
{"url": "https://arxiv.org/abs/2406.12345", "cutoff_date": "2025-11-11"}
```

Returns `application/zip`:

```
paper_info.json     # {"title": "...", "toc": ["Abstract.md", "1 Introduction.md", "  1.1 ....md", ...]}
sections/*.md       # one Markdown file per section; indentation in toc = nesting
images/             # optional
```

If the paper was published after the cutoff, respond with HTTP 403 and
`{"detail": {"error": "blocked_by_cutoff", "published_date": "2024-06", "cutoff_date": "2024-03-01", ...}}`.
Use 422 for URLs that are not papers. The clients treat 5xx as retriable.

## Building one from public parts

| Piece | What we used | Public alternatives |
|---|---|---|
| Web search with a date bound | Serper (Google) with `tbs=cdr:1,cd_max:M/D/YYYY`, then rule-based date checks per result | Serper or Brave Search with a date range; for papers only, [Semantic Scholar](https://api.semanticscholar.org/api-docs/graph) `paper/search?publicationDateOrYear=:YYYY-MM-DD`, [OpenAlex](https://docs.openalex.org) `filter=to_publication_date:YYYY-MM-DD`, or the [arXiv API](https://info.arxiv.org/help/api/) `submittedDate:[* TO YYYYMMDD2359]` |
| Date of a page or paper | Rules: arXiv id → YYMM, ACL venue → month, CrossRef for DOIs, OpenReview API; LLM fallback for other pages | Same sources: arXiv id/API, [CrossRef](https://api.crossref.org), OpenReview API v2, OpenAlex |
| Paper → sectioned Markdown | ar5iv / arXiv HTML first, then PDF + [GROBID](https://github.com/kermitt2/grobid) | arXiv HTML (`arxiv.org/html/<id>`), GROBID, [marker](https://github.com/datalab-to/marker), [docling](https://github.com/docling-project/docling), [MinerU](https://github.com/opendatalab/MinerU) |

Things that are easy to get wrong:

- **Search engines index the current version of a page.** An arXiv abstract page may show a later revision, a venue acceptance, or "cited by" text. Fetch the first version (`arxiv.org/abs/<id>v1`) when possible.
- **Undated results.** Drop them under `strict_filtering`. Keeping them is the most common source of leakage.
- **Papers that were public before arXiv.** A journal paper or OpenReview submission can predate its arXiv id. The benchmark itself handles this (`benchmark/scripts/check_date_leaks*.py`); the search backend should do the same check for papers it returns.
- **Model knowledge cutoff.** A backend cannot prevent a model from already knowing post-cutoff work. Choose agent models whose training cutoff precedes the cohort dates (see the blog post for how we chose the Sep 2025 – Jan 2026 window).
