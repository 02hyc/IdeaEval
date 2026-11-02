Scraper Tool — Structured scraping with automatic type detection and persistence

1) What this tool does
- Extracts structured text content and metadata from a list of sources (URLs, arXiv links/IDs, PDFs), auto‑selecting the best scraper per source. Processes sources in parallel, then persists the results to a normalized on‑disk layout that downstream tools (`View`/`Grep`/`Glob`) can consume directly.

2) When to use this tool
- You need to convert heterogeneous sources into a uniform, sectioned document directory for later reading, searching, or analysis.
- You want consistent section‑level Markdown files and a Table‑of‑Contents that preserves hierarchy levels.
- You need the abstract information of the sources to write or edit the outline or you plan to use `View`/`Grep`/`Glob` tools on the persisted outputs.
- Important!! When you think an article or webpage contains relevant information (based on the snippet of `WebSearch` tool), be sure to use the scraper tool to scrape it.

3) Inputs (schema)
- sources (required, array of string): Each entry can be a URL, a local/remote PDF path, or an arXiv link/ID (e.g., "2212.09741" or an "abs/" URL). At most 20 sources are allowed per call. For GitHub repository scraping, only use repo-root URLs like `https://github.com/owner/repo`; issue/PR/blob/tree/wiki pages should be treated as normal webpages instead.

4）Outputs
- Persistence layout (per document):
  - Base dir: `<working_dir>/references/`
  - Directory name: sanitized title; if collision occurs, a numeric suffix (`_1`, `_2`, …) is appended.
  - sections/<section_name>.md: one Markdown file per section. The filename is the sanitized section key from `structure`.
  - meta/toc.txt: lines correspond to `structure`, preserving leading "-" prefixes to encode levels.
  - meta/meta_info.txt: key: value format containing title, url, authors, publish date, citation count, etc.
- Return value (per source):
  - result: { title, url, abstract, saved_dir }
  - display_result: same fields for quick UI rendering
- Overall return:
  - result: list of per‑source results or errors
  - display_result: { success, num_success, num_failed }

8) Persistence layout reference
```
<references_root>/
  <title_dir>/
    sections/
      <SectionA>.md
      <SectionB>.md
      <SubsectionB1>.md
      ...
    meta/
      toc.txt            # lines like: "SectionA", "SectionB", "-SubsectionB1", "--Subsubsection..."
      meta_info.txt      # key: value format containing title, url, authors, publish date, citation count, etc.
```

Usage notes:
- For PDF files: Requires GROBID service running on localhost:8070.
- The tool writes UTF‑8 encoded artifacts under the references directory for later use.
- Processing may take time for large documents or web requests.
- Accepts a list of sources and processes them in parallel.
- If a paper source only has reliable metadata but no full-text sections available, the tool may still persist a metadata-only document so downstream tools can cite and inspect it.
- Before calling the scraper tool, you should carefully consider whether there are any important URLs found by the WebSearch tool in the historical conversation records (determine if a URL is important based on the relevance of its snippet and the current task). If such URLs exist, immediately call the scraper.
- High-quality domains that should be prioritized for scraping: arxiv.org, aclanthology.org, openreview.net, biorxiv.org, medrxiv.org, pubmed.ncbi.nlm.nih.gov, chemrxiv.org, eartharxiv.org, chemrxiv.org, osf.io, philarchive.org, engrxiv.org, cabidigitallibrary.org, ecoevorxiv.org

