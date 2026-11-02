WebSearch is a fast tool best for quick, specific lookups.

**When to use WebSearch:**
- Finding a specific arXiv link or paper URL
- Checking if a method or paper exists
- Quick verification of a fact
- Finding a specific number or benchmark result
- Any simple, factual query
- Mapping the research landscape with multiple targeted queries
- Discovering workshops, blog posts, open-source projects

**For deeper reading of found papers:**
- Use `Scraper` to extract full paper content from URLs found via WebSearch
- Use `SummarizePaper` after scraping for efficient coverage
- Use `WebFetch` to read specific pages (e.g., arxiv abstract pages, blog posts)

Features:
- Allows the agent to search the web and use the results to inform responses
- Provides up-to-date information for current events and recent data
- Returns search result information formatted as search result blocks
- Searches are performed automatically within a single API call

Usage notes:
- Domain filtering is supported to include or block specific websites
- Account for "Today's date" in <env>. For example, if <env> says "Today's
  date: 2025-07-01", and the user wants the latest docs, do not use 2024 in
  the search query. Use 2025.