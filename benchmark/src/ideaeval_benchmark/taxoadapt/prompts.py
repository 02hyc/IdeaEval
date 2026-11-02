"""Prompt templates for TaxoAdapt clustering."""

# ---------------------------------------------------------------------------
# Stage 1: Initial taxonomy generation
# ---------------------------------------------------------------------------

INITIAL_TAXONOMY_SYSTEM = """\
You are an AI/ML research taxonomy expert. Given a diverse sample of recent AI/ML \
paper titles and abstracts, generate a SHALLOW taxonomy of approximately \
{n_topics} broad research areas.

CRITICAL rules:
- Classify by RESEARCH PROBLEM and APPLICATION DOMAIN, not by method or technique.
- Do NOT create areas like "Reinforcement Learning", "Diffusion Models", \
"Transformer Architectures", or "Graph Neural Networks" — these are methods, \
not problems.
- DO create areas like "Autonomous GUI Agents for Task Completion", \
"Protein Structure Prediction", "Long-Context LLM Inference Optimization", \
"Medical Image Diagnosis", "Code Generation and Repair".
- Each area should be broad enough to contain 100+ papers from a large corpus.
- Areas should be mutually exclusive and collectively exhaustive for AI/ML research.
- Include an "Other / Cross-cutting" area for papers that don't fit elsewhere.

Return a JSON tree with ONLY root + Level 1 children (no deeper levels):
{{
  "id": "root",
  "label": "AI/ML Research",
  "children": [
    {{"id": "l1_001", "label": "...", "description": "...", "children": []}},
    {{"id": "l1_002", "label": "...", "description": "...", "children": []}}
  ]
}}
No extra text outside the JSON."""

INITIAL_TAXONOMY_USER = """\
Here are {n_papers} sample papers from the corpus. Generate approximately \
{n_topics} broad research areas that would cover this corpus:

{paper_summaries}"""

# ---------------------------------------------------------------------------
# Stage 2 & 4: Paper assignment to tree nodes
# ---------------------------------------------------------------------------

ASSIGN_SYSTEM = """\
You are a research paper classifier. Given a hierarchical taxonomy tree and a batch of \
papers, assign each paper to the most specific (deepest) matching node in the tree.

Each paper includes:
- title and abstract (for context)
- research_problem: the core problem the paper addresses (HIGH PRIORITY — use this \
as the primary signal for classification)
- task_formulation: the specific task framing (HIGH PRIORITY)
- benchmarks: dataset/benchmark names used for evaluation (for REFERENCE ONLY — \
do NOT use benchmark name overlap as a strong grouping signal, because the same \
benchmark can be used for very different research problems)

Key principle: classify by the RESEARCH PROBLEM and APPLICATION SCENARIO, not by \
the method used or benchmarks shared.

Navigate top-down: first choose the Level 1 category, then narrow down.

Return a JSON array: [{{"paper_id": "...", "node_id": "..."}}]
No extra text outside the JSON."""

ASSIGN_USER = """\
## Taxonomy Tree
{tree_json}

## Papers to classify
{papers_json}"""

# ---------------------------------------------------------------------------
# Stage 3a: Pseudo-labeling
# ---------------------------------------------------------------------------

PSEUDO_LABEL_SYSTEM = """\
You are a research paper analyst. You are given a parent research topic and a \
batch of papers classified under it. For each paper, generate a specific \
sub-topic pseudo-label that describes the particular research question this \
paper addresses WITHIN the parent topic.

Parent topic: "{parent_label}"
Parent description: "{parent_description}"

Each paper includes research_problem and task_formulation fields — use these \
as the primary signal. The pseudo-label should capture the SPECIFIC research \
question, not the method or benchmark.

Rules:
- The pseudo-label should be MORE SPECIFIC than the parent topic.
- Focus on WHAT specific problem the paper addresses, not HOW (method).
- Pseudo-labels should be 5-15 words, like a specific research question title.
- Papers addressing the same specific question should get similar pseudo-labels.
- Do NOT group by benchmark name — same benchmark can serve different questions.

Return a JSON array: [{{"paper_id": "...", "pseudo_label": "..."}}]
No extra text outside the JSON."""

PSEUDO_LABEL_USER = """\
Papers under the topic "{parent_label}":

{papers_json}"""

# ---------------------------------------------------------------------------
# Stage 3b: Subtopic clustering
# ---------------------------------------------------------------------------

SUBTOPIC_CLUSTER_SYSTEM = """\
You are a research taxonomy expert. Given a SAMPLE of paper pseudo-labels under \
the parent topic "{parent_label}", identify {min_subtopics}-{max_subtopics} \
coherent sub-topic categories that would cover ALL papers in this area.

Rules:
- Each sub-topic should represent a distinct specific research question.
- Sub-topics should be mutually exclusive and collectively exhaustive.
- Sub-topic labels should be specific research questions, not method names.
- Aim for sub-topic sizes of {target_min}-{target_max} papers each.

Return ONLY the sub-topic definitions (do NOT include paper_ids):
[{{"id": "st_01", "label": "...", "description": "one sentence"}}]
No extra text outside the JSON."""

SUBTOPIC_CLUSTER_USER = """\
Parent topic: "{parent_label}"

Sample pseudo-labels from {n_papers} papers:
{pseudo_labels_json}"""

# ---------------------------------------------------------------------------
# Stage 6: Final label generation
# ---------------------------------------------------------------------------

FINAL_LABEL_SYSTEM = """\
Given a cluster of papers that supposedly address the same research question, \
generate a precise research question statement and detailed description.

The research_question should be a concise 1-2 sentence statement that captures \
the CORE PROBLEM all these papers are trying to solve. It should be suitable \
as an input prompt for an automated research system.

CRITICAL rules for research_question:
- State ONLY the problem itself (WHAT needs to be solved)
- Do NOT list specific methods, approaches, or directions (no HOW)
- Do NOT enumerate techniques like "through X, Y, and Z"
- Bad example: "How can diffusion inference be accelerated through caching, \
solver optimization, and timestep scheduling?"
- Good example: "How can diffusion model inference be accelerated without \
retraining the model?"

The research_question_detail should be a 3-5 sentence description covering: \
the specific problem, typical evaluation benchmarks, application scenarios, \
and key challenges.

Return JSON:
{{"research_question": "...", "research_question_detail": "..."}}
No extra text outside the JSON."""

FINAL_LABEL_USER = """\
Cluster label: {cluster_label}

Papers in this cluster ({n_papers}):
{papers_json}"""

# ---------------------------------------------------------------------------
# Stage 7: Coherence verification
# ---------------------------------------------------------------------------

COHERENCE_CHECK_SYSTEM = """\
You are a research quality auditor. Given a research question and a list of \
papers supposedly answering it, determine for EACH paper whether it genuinely \
addresses this research question.

Research question: "{research_question}"

For each paper, return true if the paper genuinely addresses this research \
question, or false if it's an outlier that doesn't belong.

Return JSON array: [{{"paper_id": "...", "belongs": true/false, "reason": "brief reason if false"}}]
No extra text outside the JSON."""

COHERENCE_CHECK_USER = """\
Papers to verify ({n_papers}):
{papers_json}"""

# ---------------------------------------------------------------------------
# Stage 2D/3D: Deduplication
# ---------------------------------------------------------------------------

DEDUP_SYSTEM = """\
You are a research taxonomy expert. Given all category nodes at level {level} of a \
research taxonomy, identify groups of nodes that have SIGNIFICANT topical overlap \
— i.e., a substantial fraction of papers in one node could equally belong to the other.

Rules:
- Only flag groups with genuine semantic overlap, not superficial keyword similarity.
- "Robustness in NLP" and "Robustness in Vision" are DIFFERENT topics — don't merge.
- "LLM Safety and Robustness" and "Adversarial Robustness and Defense" DO overlap.
- A node can appear in at most one overlap group.
- Nodes with no overlap should NOT be grouped.
- For each group, suggest a merged label and description that covers all nodes.

Return JSON array (empty [] if no overlaps):
[{{"group": ["id1", "id2"], "suggested_label": "...", \
"suggested_description": "one sentence", "reason": "brief explanation"}}]
No extra text outside the JSON."""

DEDUP_USER = """\
Level {level} nodes ({n_nodes} total):
{nodes_json}"""

# ---------------------------------------------------------------------------
# Multi-label paper assignment
# ---------------------------------------------------------------------------

ASSIGN_MULTI_SYSTEM = """\
You are a research paper classifier. Given a hierarchical taxonomy tree and a batch of \
papers, assign each paper to 1-3 most relevant leaf nodes in the tree.

A paper should be assigned to multiple nodes ONLY if it genuinely spans multiple \
research areas. Most papers should have 1-2 assignments. Assign 3 only when clearly \
justified.

Each paper includes:
- title and abstract (for context)
- research_problem: the core problem the paper addresses (HIGH PRIORITY — use this \
as the primary signal for classification)
- task_formulation: the specific task framing (HIGH PRIORITY)
- benchmarks: dataset/benchmark names used for evaluation (for REFERENCE ONLY — \
do NOT use benchmark name overlap as a strong grouping signal, because the same \
benchmark can be used for very different research problems)

Key principle: classify by the RESEARCH PROBLEM and APPLICATION SCENARIO, not by \
the method used or benchmarks shared.

Navigate top-down: first choose the Level 1 category, then narrow down.

Return a JSON array: [{{"paper_id": "...", "node_ids": ["...", ...]}}]
No extra text outside the JSON."""
