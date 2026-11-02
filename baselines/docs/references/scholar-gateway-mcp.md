# Scholar Gateway MCP

MCP server exposing web search, page fetching, and academic paper extraction as tools for AI agents. Connects to a running `web-tools-service` backend.

## Setup

```bash
uv sync
```

## Configuration

Environment variables:
- `WTS_BASE_URL` — Web tools service URL (default: `http://localhost:8900`)
- `WTS_API_KEY` — API key for web-tools-service (required)

## Usage

```bash
WTS_API_KEY=wts-xxx uv run scholar-gateway-mcp [OPTIONS]
```

### Harness-injected options

These options are set by the orchestration harness at server launch time. They are **not visible to the LLM** — the agent only sees the tool's user-facing parameters (query, url, pages).

| Flag | Default | Description |
|------|---------|-------------|
| `--cutoff-date YYYY-MM-DD` | None | Filter results/pages published after this date |
| `--restrict-domains / --no-restrict-domains` | enabled | Restrict to domain allowlist |
| `--strict-filtering / --no-strict-filtering` | enabled | Drop ambiguous date results in search |
| `--mode {rules,llm}` | rules | Date extraction engine for fetch |
| `--format {text,markdown}` | markdown | Output format for fetch |
| `--references-dir PATH` | ./references | Directory to save extracted papers into |

### Claude Code integration

Add to your Claude Code MCP config (`~/.claude/settings.json` or project `.claude/settings.local.json`):

```json
{
  "mcpServers": {
    "scholar-gateway": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/path/to/IdeaEval/baselines",
        "scholar-gateway-mcp",
        "--cutoff-date", "2025-01-01",
        "--references-dir", "/path/to/your/project/references"
      ],
      "env": {
        "WTS_API_KEY": "wts-xxx"
      }
    }
  }
}
```

### Claude Code Agent SDK (programmatic usage)

Use `--mcp-config` to attach the MCP server when invoking Claude Code as an SDK:

```bash
# Save MCP config to a file
cat > mcp-config.json << 'EOF'
{
  "mcpServers": {
    "scholar-gateway": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/path/to/IdeaEval/baselines",
        "scholar-gateway-mcp",
        "--cutoff-date", "2025-01-01"
      ],
      "env": {
        "WTS_API_KEY": "wts-xxx"
      }
    }
  }
}
EOF

# Run agent with MCP tools (non-interactive)
echo "Search for papers about sparse mixture of experts" | \
  claude -p \
    --mcp-config mcp-config.json \
    --allowedTools "mcp__scholar-gateway__web_search,mcp__scholar-gateway__web_fetch,mcp__scholar-gateway__extract_paper"

# JSON output for programmatic consumption
echo "Search for RAG survey papers" | \
  claude -p \
    --mcp-config mcp-config.json \
    --allowedTools "mcp__scholar-gateway__web_search" \
    --output-format json
```

**Tool names for `--allowedTools`:**
- `mcp__scholar-gateway__web_search`
- `mcp__scholar-gateway__web_fetch`
- `mcp__scholar-gateway__extract_paper`

**Verified example session** (actual output from testing):

```
$ echo "Find the top 3 papers about sparse mixture of experts" | \
    claude -p --mcp-config mcp-config.json \
    --allowedTools "mcp__scholar-gateway__web_search"

1. Outrageously Large Neural Networks: The Sparsely-Gated Mixture-of-Experts Layer (2017)
   https://arxiv.org/abs/1701.06538
   — Cited by 5,951

2. From Sparse to Soft Mixtures of Experts (2023)
   https://arxiv.org/abs/2308.00951
   — Cited by 352

3. Sparse MoE with Language Guided Routing for Multilingual Machine Translation (2023)
   https://openreview.net/forum?id=ySS7hH1smL
   — Cited by 27
```

## Tools

### `web_search`

Search the web with automatic date filtering and domain allowlist.

**LLM-visible parameters:**
- `query` (string, required) — search keywords
- `pages` (integer, optional, default 1, max 10) — number of result pages

**Example output** (with `--cutoff-date 2024-06-01`):

```
Found 9 results.

1. Chain-of-Thought Prompting Elicits Reasoning in Large ...
   URL: https://arxiv.org/abs/2201.11903
   Date: 2022-01-28
   Snippet: Experiments on three large language models show that chain of thought
   prompting improves performance on a range of arithmetic, commonsense, and
   symbolic reasoning tasks.

2. Chain-of-Thought Reasoning Without Prompting
   URL: https://arxiv.org/abs/2402.10200
   Date: 2024-02-15
   Snippet: Our findings reveal that, intriguingly, CoT reasoning paths can be ...

3. Structured Chain-of-Thought Prompting for Code Generation
   URL: https://arxiv.org/abs/2305.06599
   Date: 2023-05-11
   Snippet: LLMs take prompts as inputs, and Chain-of-Thought (CoT) prompting is
   the state-of-the-art prompting technique.
...
```

When `--cutoff-date` is set, results published after that date are filtered out. With `--cutoff-date 2020-01-01`, a query for "GPT-4 technical report" returns 0 results since GPT-4 was published in 2023.

---

### `web_fetch`

Fetch and read a web page, with optional date-based rejection.

**LLM-visible parameters:**
- `url` (string, required) — URL to fetch

**Example output** (fetching `https://arxiv.org/abs/2201.11903`):

```markdown
# [2201.11903] Chain-of-Thought Prompting Elicits Reasoning in Large Language Models

[View PDF](https://arxiv.org/pdf/2201.11903)
> Abstract:We explore how generating a chain of thought—a series of intermediate
reasoning steps—significantly improves the ability of large language models to
perform complex reasoning. In particular, we show how such reasoning abilities
emerge naturally in sufficiently large language models via a simple method called
chain of thought prompting...

## Submission history

From: Jason Wei
 **[v1]** Fri, 28 Jan 2022 02:33:07 UTC (944 KB)
...
```

The output format is controlled by `--format` (text or markdown). If `--cutoff-date` is set and the page's detected publication date exceeds it, the tool returns an error message instead of content.

---

### `extract_paper`

Extract an academic paper to local files for on-demand reading. Supports arxiv, openreview, ACL Anthology, DOI links, and direct PDF URLs.

The tool downloads the paper, splits it into sections, and saves them to `references/<paper-slug>/`. It returns only the directory path and table of contents — the agent then reads individual sections as needed using file read tools.

**LLM-visible parameters:**
- `url` (string, required) — paper URL

**Output directory structure:**
```
references/
└── chain-of-thought-prompting-elicits-reasoning-in-large-language-models/
    ├── paper_info.json          # title, toc, url
    └── sections/
        ├── Abstract.md
        ├── 1 Introduction.md
        ├── 2 Chain-of-Thought Prompting.md
        ├── 3 Arithmetic Reasoning.md
        └── ...
```

**Example output** (extracting `https://arxiv.org/abs/2201.11903`):

```
Paper extracted: Chain-of-Thought Prompting Elicits Reasoning in Large Language Models
Directory: /path/to/references/chain-of-thought-prompting-elicits-reasoning-in-large-language-models

Sections:
  - .../sections/Abstract.md
  - .../sections/1 Introduction.md
  - .../sections/2 Chain-of-Thought Prompting.md
  - .../sections/3 Arithmetic Reasoning.md
  - .../sections/4 Commonsense Reasoning.md
  - .../sections/5 Symbolic Reasoning.md
  - .../sections/6 Discussion.md
  - .../sections/7 Related Work.md
  - .../sections/8 Conclusions.md
  - .../sections/Acknowledgements.md
  - .../sections/References.md
  - .../sections/A Frequently Asked Questions.md
  - .../sections/B All Experimental Results.md
  ...
```

The agent receives a lightweight index (~500 chars) instead of the full paper text (138K+ chars). It can then read specific sections on demand. The output directory is controlled by `--references-dir` (default: `./references`).

If `--cutoff-date` is set and the paper was published after that date, the tool returns an error without downloading.

**Typical agent workflow:**
1. Agent calls `extract_paper(url="https://arxiv.org/abs/2201.11903")`
2. Gets back directory path + section list
3. Reads `sections/Abstract.md` to understand the paper
4. Reads additional sections as needed for deeper analysis

---

## How cutoff date filtering works

The `--cutoff-date` flag applies to all three tools:

| Scenario | Behavior |
|----------|----------|
| `web_search` with cutoff | Search results published after the cutoff are filtered out |
| `web_fetch` with cutoff | If the page's publication date exceeds cutoff, returns error |
| `extract_paper` with cutoff | If the paper was published after cutoff, returns 403 error |
| Any tool without cutoff | No date filtering, all results returned |

This ensures agents operating under a knowledge cutoff (e.g., simulating training data boundaries) cannot access content beyond their allowed date.

## MCP Protocol Details

The server speaks MCP over stdio. On `tools/list`, it advertises exactly three tools with minimal schemas:

```
Tool: web_search
  Description: Search the web for information. Returns titles, URLs, snippets,
               and dates of matching pages.
  Params: [query, pages]

Tool: web_fetch
  Description: Fetch and read the content of a web page. Returns the page text.
  Params: [url]

Tool: extract_paper
  Description: Extract an academic paper (arxiv, openreview, ACL, DOI, or
               direct PDF URL) to local files. Returns the directory path and
               section list. Use file read tools to access individual sections.
  Params: [url]
```

Harness-injected parameters (`cutoff_date`, `restrict_domains`, `strict_filtering`, `mode`, `format`, `references_dir`) are **not part of the tool schemas** — agents cannot see or override them.

## Tests

```bash
# Unit tests (no network)
uv run pytest tests/ -v --ignore=tests/test_integration.py

# Integration tests (requires running web-tools-service at localhost:8900)
WTS_API_KEY=wts-xxx uv run pytest tests/test_integration.py -v
```

## Architecture

```
Agent ←stdio→ scholar-gateway-mcp ←httpx→ web-tools-service:8900
                    │
                    ├── cli.py        (argparse, env vars → create_mcp_server)
                    ├── server.py     (FastMCP + 3 tool definitions)
                    └── client.py     (httpx async client, ZIP unpacking)
```
