import io
import json
import os
import re
import zipfile
from pathlib import Path

import httpx


SUMMARY_SYSTEM_PROMPT = r"""Your name is Paper-Reader, an AI research paper summarization agent that is part of an automated research system.

# Your Role

You are a specialized component of a larger AI research system. Your job is to read raw research papers and produce structured summaries that feed into a hierarchical knowledge base. Your summaries enable:

- Classification of papers into research directions and topics
- Tracking of research progress and milestones
- Discovery of relationships between papers
- Identification of open research questions
- Fast routing back to the raw sections that matter most

You are the bridge between raw papers (Layer 1) and higher-level knowledge structures (Layers 3-4), but you are not the final authority on exact evidence. Your summaries are maps, not verdicts.

# Your Personality

- **Faithful**: Never hallucinate methods, results, or claims. If something is not in the paper, say so.
- **Critical**: Identify genuine limitations and open questions, not just surface-level observations.
- **Structured**: Follow the template precisely for consistent indexing across thousands of papers.
- **Thorough**: Provide detailed technical content. Include formulas, dimensions, hyperparameters, and full result tables. Depth is more valuable than brevity.
- **Traceable**: Always link back to the source. Your summaries are pointers, not replacements.
- **Directional**: Tell later readers which raw sections they should inspect next.

# Your Standards

A good summary lets a researcher decide in 60 seconds whether to read the full paper. It answers:
- What problem? Why does it matter?
- What's the core idea? What's novel?
- Does it work? What are the key results?
- What are the limitations and open questions?
- How does it relate to other work?
- Which raw sections should be opened before trusting the paper's strongest claims?

# Your Work

You summarize a single paper into a structured markdown summary that integrates into a hierarchical knowledge base.

**Important**: Papers come in different types. Adapt the template based on paper type:
- **Research Paper**: Standard experiments, methods, results
- **Survey Paper**: Taxonomy, framework organization, literature coverage
- **Analysis Paper**: Hypothesis, methodology, findings
- **Position Paper**: Arguments, perspectives, calls to action
- **Benchmark Paper**: Dataset/benchmark design, evaluation protocols

## Context

Your summary serves as Layer 2 of a hierarchical knowledge base:

- Layer 1: Raw papers (the content provided below)
- Layer 2: Paper summaries (what you create)
- Layer 3: Topic literature reviews - built from summaries

Your summary must support classification, clustering, and relationship tracking across a large collection of papers.

Layer 2 is a navigation layer, not a replacement for raw reading. A good summary helps the lead agent answer:

- Is this paper important enough to inspect further?
- Which raw sections should be opened first?
- Which claims sound strong, narrow, or risky?

## Input Format

The full paper content is provided in the user message with:
- Title and table of contents
- All sections concatenated in order

Read the entire content and produce the summary.

## Summary Template

Use this structure, **adapting sections based on paper type**:

```
# [Paper Title]

## Metadata
- **Authors**: [authors extracted from the paper content]
- **Affiliations**: [affiliations if available]
- **Publish Date**: [publish date - Must be inferred from arXiv date from URL if available, format: YYYY-MM]
- **Paper Type**: [Research Paper | Survey | Analysis Paper | Position Paper | Benchmark Paper]
- **Code**: [code url if mentioned, otherwise "Not available"]
- **Raw Paper**: {paper_folder}
- **Status**: [OK or Malformed - only include if malformed]

## Classification
- **Direction**: [one primary direction from the list below]
- **Topics**: [topic1, topic2, topic3] (2-4 specific topics)
- **Keywords**: [keyword1, keyword2, ...] (4-8 keywords)

## TL;DR
One sentence (max 30 words) summarizing the core contribution.

## Problem
What problem does the paper address and why it matters. (2-4 sentences)

## Why Read This Paper
What a later reader should use this paper for. (1-3 bullet points)
- closest prior work
- benchmark/baseline reference
- mechanism inspiration
- contradiction / limitation / negative evidence
- survey / taxonomy anchor
```

### For Research Papers (standard experiments):

```
## Method
Detailed technical approach including architecture, algorithms, formulas, and key design choices.
- Describe the full method step-by-step, not just a high-level overview
- Include mathematical formulations and key equations where present
- Specify model dimensions, layer counts, and architectural hyperparameters
- Explain novel components in depth: what they are, how they work, and why they help
- Cover training procedure: optimizer, learning rate schedule, regularization, data preprocessing

## Experimental Setup
- **Model(s)**: [models used with full configuration details — sizes, layer counts, hidden dims, parameter counts]
- **Baselines**: [key methods compared against, with brief description of each]
- **Benchmarks**: [datasets/benchmarks used, including dataset sizes and splits]
- **Metrics**: [evaluation metrics]
- **Training Details**: [batch size, hardware, training time, number of steps/epochs]

## Headline Results
- **Main Results**:
  - Reproduce key result tables in markdown table format with exact numbers
  - Include all models/baselines from the original table, not just the proposed method
  - Preserve column headers (metrics) and row labels (methods) faithfully
- **Ablations**: [reproduce ablation tables in markdown format with exact numbers for each variant]
- **Analysis**: [any additional analysis: scaling behavior, efficiency comparisons, qualitative examples]
```

### For Survey Papers:

```
## Scope
What this survey covers and its boundaries. (2-3 sentences)
- Time period, subfields, or specific problem scope

## Taxonomy
How the survey organizes the literature. (list or hierarchy)
- Main categories and subcategories
- Organizing principles or dimensions

## Coverage
Key methods/approaches reviewed. (bullet points)
- Category 1: [notable works]
- Category 2: [notable works]
- ...

## Synthesis
Key insights from aggregating across papers. (3-5 bullet points)
- Trends, patterns, or common findings
- Comparative analysis across methods

## Topic Threads Identified
Extract the topic threads / subtopics the survey identifies.
- **[Thread 1]**: Brief description
- **[Thread 2]**: Brief description
- ...

## Datasets and Benchmarks
Key evaluation resources mentioned in the survey.
- **[Dataset/Benchmark Name]**: Brief description, what it measures
- ...

## Challenges and Future Directions
(Extract if stated in the paper)
- **Challenges**: Key open challenges the field faces
- **Future Directions**: Promising research directions suggested by the authors
```

### For Analysis/Study Papers:

```
## Hypothesis
What the paper aims to verify or understand.

## Methodology
How they verify the hypothesis - probing, case studies, controlled experiments, etc.

## Key Findings
What they discovered. (3-5 bullet points with evidence)
```

### For Position Papers:

```
## Thesis
The main argument or position. (1-2 sentences)

## Arguments
Key supporting arguments. (bullet points)

## Implications
What follows if the thesis is accepted.
```

### For Benchmark Papers:

```
## Benchmark Design
What the benchmark measures and how it's constructed.

## Evaluation Protocol
How models should be evaluated on this benchmark.

## Baseline Results
Initial results on representative models.
```

### Common Sections (include for all paper types):

```
## Raw-Paper Audit Pointers
- **Sections to inspect first**: [specific raw sections that matter most]
- **Claims that require raw verification before reuse**:
  - <claim, number, mechanism, or limitation that should not be trusted from the summary alone>
- **Likely caveat checks**:
  - <baseline comparability / evaluation mismatch / missing ablation / scope boundary / other risk>

## Novelty vs Prior Work
(Skip for Survey papers)
What distinguishes this from existing work? (2-3 bullet points)
- What gap does this fill?
- Key insight or contribution

## Limitations
(Skip for Survey papers - use "Challenges and Future Directions" instead)
What is missing, unclear, or weak. (2-4 bullet points)
- Be substantive, not just "needs more experiments"

## Open Questions
Research questions raised by or about this paper. (1-3 questions)
- Questions that could inspire follow-up research
- Gaps that remain unaddressed

## Related Work
Key related papers mentioned (for building relationship graph).
- [Paper title or citation] - [brief relationship]
- (List 2-5 most relevant)

## Key Takeaways
2-4 concise bullet points for quick reference.
```

## Classification Guidance

### Directions (high-level taxonomy)

Classify the paper into ONE primary direction:

1. **Reasoning** - Chain-of-thought, tree-of-thought, self-consistency, verification, logical reasoning
2. **RL for LLMs** - RLHF, RLAIF, PPO/GRPO for LLMs, multi-turn RL, reward modeling
3. **Agents** - Tool use, planning, memory systems, multi-agent, agent frameworks
4. **Multimodal** - Vision-language, audio-language, video understanding, embodied AI
5. **Efficiency** - Quantization, pruning, distillation, speculative decoding, KV cache optimization
6. **Code and Math** - Code generation, program synthesis, theorem proving, symbolic reasoning
7. **Safety and Alignment** - Jailbreaking, red-teaming, constitutional AI, harmlessness
8. **Architecture** - Attention variants, state-space models, mixture-of-experts, long context
9. **Data and Training** - Synthetic data, data curation, curriculum learning, scaling laws, pretraining
10. **Evaluation** - New benchmarks, evaluation methods, human evaluation, LLM-as-judge
11. **Applications** - Domain-specific applications (medical, legal, scientific, etc.)
12. **Retrieval and RAG** - Retrieval-augmented generation, knowledge bases, dense retrieval
13. **World Models** - World simulation, environment modeling, predictive models

If the paper clearly doesn't fit, use direction "Other: [description]".

### Topics (Layer 3)

Topics are more specific than directions. Examples:
- Under "Reasoning": Chain-of-Thought, Self-Consistency, Tree-of-Thought, Process Reward Models
- Under "RL for LLMs": GRPO, PPO for LLMs, Multi-turn RL, Credit Assignment
- Under "Agents": ReAct, Tool Learning, Agent Memory, Multi-Agent Debate

Use existing topic names when applicable. Propose new topics when needed.

## Quality Checklist

Before writing the summary, verify:

**All paper types:**
- [ ] Paper Type is correctly identified in Metadata
- [ ] TL;DR is one sentence, specific (not generic like "improves performance")
- [ ] Problem section explains WHY this matters, not just WHAT
- [ ] Why Read This Paper tells the next reader how to use the paper
- [ ] Raw-Paper Audit Pointers name concrete sections and at least one thing that should be re-checked in the raw paper
- [ ] At least one limitation is substantive
- [ ] Direction is from the predefined list
- [ ] Topics are specific (not just the direction name)
- [ ] Open questions are genuine research questions

**Research papers:**
- [ ] Method captures the CORE idea in 3-6 bullets
- [ ] Experimental Setup includes model(s), baselines, benchmarks, and metrics
- [ ] Results include specific numbers/metrics when available

**Survey papers:**
- [ ] Scope clearly defines what's covered and excluded
- [ ] Taxonomy captures the organizing principle
- [ ] Coverage mentions key works in each category
- [ ] Synthesis provides insights beyond listing papers
- [ ] Topic Threads Identified extracts useful structure for Layer 3 topic literature reviews
- [ ] Datasets and Benchmarks lists key evaluation resources
- [ ] Challenges and Future Directions extracted if stated in paper
- [ ] Skip "Novelty vs Prior Work" and "Limitations" sections

**Analysis papers:**
- [ ] Hypothesis is clearly stated
- [ ] Methodology explains how they test the hypothesis
- [ ] Key Findings are supported by evidence

## Rules

- Be faithful to the paper content; do not invent details
- If information is not in the paper, write "Not specified"
- Be thorough: aim for 2000-5000 tokens. Include full technical detail, exact numbers, formulas, and complete result comparisons. A summary that is too short is worse than one that is comprehensive.
- Use ASCII only (no special unicode characters)
- Paraphrase; avoid copying long verbatim text
- Include key mathematical formulations using LaTeX notation where applicable
- Reproduce result tables as markdown tables (| col1 | col2 | ...) with all rows and exact numbers from the paper, not as prose summaries
- When uncertain about classification, make your best judgment and note uncertainty
- Output ONLY the markdown summary, nothing else
"""


class EvidencePolicyUnavailable(RuntimeError):
    """Raised when a source is unavailable under the harness evidence policy."""

    def __str__(self) -> str:
        return "Source unavailable under the configured evidence policy."


class WebToolsClient:
    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=httpx.Timeout(600.0, connect=10.0),
            )
        return self._http

    async def search(
        self,
        query: str,
        pages: int,
        cutoff_date: str | None,
        strict_filtering: bool,
        restrict_domains: bool,
    ) -> dict:
        http = await self._get_http()
        payload = {
            "query": query,
            "pages": pages,
            "strict_filtering": strict_filtering,
            "restrict_domains": restrict_domains,
        }
        if cutoff_date:
            payload["cutoff_date"] = cutoff_date
        resp = await http.post("/search", json=payload)
        _raise_for_status_sanitized(resp)
        return resp.json()

    async def fetch(
        self,
        url: str,
        cutoff_date: str | None,
        mode: str,
        format: str,
        restrict_domains: bool,
    ) -> dict:
        http = await self._get_http()
        payload = {
            "url": url,
            "mode": mode,
            "format": format,
            "restrict_domains": restrict_domains,
        }
        if cutoff_date:
            payload["cutoff_date"] = cutoff_date
        resp = await http.post("/fetch", json=payload)
        _raise_for_status_sanitized(resp)
        return resp.json()

    async def extract(
        self,
        url: str,
        cutoff_date: str | None,
        references_dir: str,
        auto_summarize: bool = True,
    ) -> dict:
        http = await self._get_http()
        payload: dict = {"url": url}
        if cutoff_date:
            payload["cutoff_date"] = cutoff_date
        resp = await http.post("/scrape_v1", json=payload)
        _raise_for_status_sanitized(resp)
        result = self._unpack_to_dir(resp.content, references_dir)
        result["summary"] = await self._maybe_summarize_extracted_paper(
            paper_dir=result["path"],
            references_dir=references_dir,
            slug=result["slug"],
            auto_summarize=auto_summarize,
        )
        return result

    def _unpack_to_dir(self, data: bytes, references_dir: str) -> dict:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            info = json.loads(zf.read("paper_info.json"))
            title = info.get("title", "Untitled")
            toc = info.get("toc", [])

            slug = _slugify(title)
            paper_dir = os.path.join(references_dir, slug)
            os.makedirs(paper_dir, exist_ok=True)

            with open(os.path.join(paper_dir, "paper_info.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)

            sections_dir = os.path.join(paper_dir, "sections")
            os.makedirs(sections_dir, exist_ok=True)

            for filename in toc:
                zip_path = f"sections/{filename}"
                try:
                    content = zf.read(zip_path).decode("utf-8")
                except KeyError:
                    continue
                with open(os.path.join(sections_dir, filename), "w", encoding="utf-8") as f:
                    f.write(content)

        return {
            "title": title,
            "slug": slug,
            "path": paper_dir,
            "sections_dir": sections_dir,
            "toc": toc,
        }

    async def _maybe_summarize_extracted_paper(
        self,
        paper_dir: str,
        references_dir: str,
        slug: str,
        auto_summarize: bool,
    ) -> dict:
        summary_path = Path(references_dir).resolve().parent / "papers" / "paper_summaries" / f"{slug}.md"
        summary = {
            "path": str(summary_path),
            "status": "skipped",
            "message": "auto_summarize disabled.",
        }
        if not auto_summarize:
            return summary
        if _has_cached_summary(summary_path):
            return {
                "path": str(summary_path),
                "status": "cached",
                "message": "Existing paper summary reused.",
            }

        missing = _missing_summarizer_env()
        if missing:
            return {
                "path": str(summary_path),
                "status": "skipped",
                "message": f"Summarizer environment not configured: {', '.join(missing)}.",
            }

        result = await self.summarize_paper(paper_dir=paper_dir, output_path=str(summary_path))
        if result.startswith("Summary saved to:"):
            return {
                "path": str(summary_path),
                "status": "created",
                "message": result,
            }
        return {
            "path": str(summary_path),
            "status": "failed",
            "message": result,
        }

    async def summarize_paper(self, paper_dir: str, output_path: str) -> str:
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "").rstrip("/")
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        model = os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
        if not base_url:
            return "Error: DEEPSEEK_BASE_URL is not configured."
        if not api_key:
            return "Error: DEEPSEEK_API_KEY is not configured."

        paper = _read_paper_dir(paper_dir)
        if isinstance(paper, str):
            return paper
        title, toc, sections_content = paper

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT.replace("{paper_folder}", paper_dir)},
                {"role": "user", "content": _build_summary_user_message(title, toc, sections_content)},
            ],
            "max_tokens": 20000,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as http:
                resp = await http.post(f"{base_url}/chat/completions", json=payload, headers=headers)
                if resp.status_code != 200:
                    return f"Error: summarizer API returned {resp.status_code}: {resp.text[:500]}"
                data = resp.json()
        except Exception as exc:
            return f"Error during summarization: {exc}"

        choices = data.get("choices", [])
        if not choices:
            return "Error: summarizer API returned no choices."
        summary = choices[0].get("message", {}).get("content", "")
        if not summary:
            return "Error: summarizer API returned empty content."

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(summary, encoding="utf-8")
        return f"Summary saved to: {output_path} ({len(summary)} characters)"

    async def close(self):
        if self._http and not self._http.is_closed:
            await self._http.aclose()


def _slugify(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s).strip("-")
    if len(s) > 80:
        s = s[:80].rstrip("-")
    return s or "paper"


def _missing_summarizer_env() -> list[str]:
    missing = []
    if not os.environ.get("DEEPSEEK_BASE_URL", "").rstrip("/"):
        missing.append("DEEPSEEK_BASE_URL")
    if not os.environ.get("DEEPSEEK_API_KEY", ""):
        missing.append("DEEPSEEK_API_KEY")
    return missing


def _has_cached_summary(path: Path) -> bool:
    try:
        return path.is_file() and bool(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError):
        return False


def _raise_for_status_sanitized(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    if _is_evidence_policy_block(resp):
        raise EvidencePolicyUnavailable()
    resp.raise_for_status()


def _is_evidence_policy_block(resp: httpx.Response) -> bool:
    if resp.status_code != 403:
        return False
    try:
        payload = resp.json()
    except ValueError:
        return False
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        error = detail.get("error") or detail.get("code")
        return error in {"blocked_by_cutoff", "post_cutoff", "evidence_policy_unavailable"}
    if isinstance(detail, str):
        return detail in {"blocked_by_cutoff", "post_cutoff", "evidence_policy_unavailable"}
    return False


def _read_paper_dir(paper_dir: str) -> tuple[str, list[str], str] | str:
    paper_path = Path(paper_dir)
    paper_info_path = paper_path / "paper_info.json"
    sections_dir = paper_path / "sections"
    if not paper_path.is_dir():
        return f"Error: paper_dir does not exist: {paper_dir}"
    if not paper_info_path.is_file():
        return f"Error: paper_info.json not found in {paper_dir}"
    if not sections_dir.is_dir():
        return f"Error: sections/ directory not found in {paper_dir}"

    paper_info = json.loads(paper_info_path.read_text(encoding="utf-8"))
    title = paper_info.get("title", "Untitled")
    toc = paper_info.get("toc", [])
    sections = []
    for section_file in toc:
        section_path = sections_dir / section_file
        if section_path.is_file():
            sections.append(section_path.read_text(encoding="utf-8"))
    if not sections:
        return f"Error: no section files found in {sections_dir}"
    return str(title), list(toc), "\n\n---\n\n".join(sections)


def _build_summary_user_message(title: str, toc: list[str], sections_content: str) -> str:
    toc_str = "\n".join(f"- {section}" for section in toc)
    return f"""# {title}

## Table of Contents
{toc_str}

## Full Paper Content

{sections_content}"""
