from __future__ import annotations

from pathlib import Path

ASSET_ROOT = Path("src/research_ideation_agent/assets/scientific-hypothesis-generator")


def test_rubric_has_five_dimensions_and_gating() -> None:
    text = (ASSET_ROOT / "shared" / "rubric.md").read_text(encoding="utf-8")
    for dimension in ("Problem Value", "Novelty", "Soundness", "Feasibility", "Experimental Quality"):
        assert dimension in text, f"rubric missing dimension: {dimension}"
    assert "## Preconditions" in text
    assert "## Scoring and gating" in text
    assert "minimum" in text.lower(), "rubric must state the no-averaging (minimum-binds) rule"


def test_rubric_has_no_cutoff_leakage() -> None:
    text = (ASSET_ROOT / "shared" / "rubric.md").read_text(encoding="utf-8").lower()
    for banned in ("cutoff date", "post-cutoff", "benchmark cutoff", "date boundary"):
        assert banned not in text, f"cutoff leakage: {banned}"


def test_reviewer_subagent_frontmatter_and_protocol() -> None:
    text = (ASSET_ROOT / "subagents" / "scientific-reviewer.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "subagent must start with YAML frontmatter"
    assert "name: scientific-reviewer" in text, "frontmatter name must match the selected id"
    assert "shared/rubric.md" in text, "reviewer must reference the shared rubric"
    for gate in ("evidence", "hypothesis_method", "proposal"):
        assert gate in text, f"reviewer must define the {gate} gate protocol"
    for field_ in ("status:", "unresolved_critical:", "Resolved when"):
        assert field_ in text, f"reviewer must define verdict/finding field: {field_}"
    for section in ("## Summary", "## Dimension Assessment", "## Strengths", "## Revision Plan"):
        assert section in text, f"reviewer must mandate verdict document section: {section}"


def test_startup_prompt_describes_gates_and_new_files() -> None:
    text = (ASSET_ROOT / "prompts" / "startup.md").read_text(encoding="utf-8")
    for required in (
        "notes/reviews/evidence_review.md",
        "notes/hypothesis.md",
        "notes/novelty_check.md",
        "notes/method.md",
        "notes/experiment_plan.md",
        "notes/reviews/proposal_review.md",
    ):
        assert required in text, f"startup must list {required}"
    assert "scientific-reviewer" in text
    assert "rubric" in text.lower(), "startup must point the lead at the scoring rubric"
    assert "proposal-reflection-revision" not in text, "deleted skill must not be referenced"
    for banned in ("cutoff", "post-cutoff"):
        assert banned not in text.lower()


def test_repair_prompt_points_to_proposal_writeup() -> None:
    text = (ASSET_ROOT / "prompts" / "repair.md").read_text(encoding="utf-8")
    assert "proposal-writeup" in text
    assert "proposal-reflection-revision" not in text


def test_skill_set_is_consolidated() -> None:
    skills = ASSET_ROOT / "skills"
    assert (skills / "literature-review" / "SKILL.md").is_file()
    assert (skills / "hypothesis-method" / "SKILL.md").is_file()
    assert (skills / "proposal-writeup" / "SKILL.md").is_file()
    assert not (skills / "hypothesis-development").exists()
    assert not (skills / "proposal-writeup-audit").exists()
    assert not (skills / "proposal-reflection-revision").exists()
    for name in ("hypothesis-method", "proposal-writeup"):
        head = (skills / name / "SKILL.md").read_text(encoding="utf-8").splitlines()
        assert f"name: {name}" in head[:6], f"{name} frontmatter name must match dir"


def test_proposal_writeup_template_passes_validator(tmp_path: Path) -> None:
    import re

    from research_ideation_agent.validator import validate_proposal

    text = (ASSET_ROOT / "skills" / "proposal-writeup" / "SKILL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```markdown\n(.*?)```", text, re.DOTALL)
    template = max(blocks, key=len)  # the proposal template is the largest fenced markdown block
    template = template.replace("<Concise Proposal Title>", "Concrete Title")
    proposal = tmp_path / "proposal.md"
    proposal.write_text(template, encoding="utf-8")
    result = validate_proposal(proposal)
    assert result.valid, f"shipped template must validate; missing={result.missing}"


def test_shg_configs_wire_reviewer_hook_and_skills() -> None:
    from research_ideation_agent.config import load_config

    for name in ("base", "claude", "gpt"):
        cfg = load_config(f"configs/scientific-hypothesis-generator/{name}.yaml")
        assert cfg.skills.selected == ["literature-review", "hypothesis-method", "proposal-writeup"]
        assert cfg.subagents.selected == ["scientific-reviewer"]
        assert cfg.budgets.max_turns is not None, f"{name}: expected a max_turns runaway backstop"
        assert cfg.hooks.stop, f"{name}: expected a declared stop hook"
        script = cfg.hooks.stop[0].script
        assert script.is_absolute() and script.exists()
        assert script.name == "stop_artifact_gate.py"
        assert cfg.hooks.stop[0].timeout == 30
    claude_cfg = load_config("configs/scientific-hypothesis-generator/claude.yaml")
    assert claude_cfg.budgets.timeout_seconds is not None
    assert claude_cfg.budgets.timeout_seconds >= 3600


def test_core_skill_ids_removed_and_default_empty() -> None:
    from research_ideation_agent import constants
    from research_ideation_agent.config import DEFAULT_CONFIG

    assert not hasattr(constants, "CORE_SKILL_IDS")
    assert DEFAULT_CONFIG["skills"]["selected"] == []


def test_rubric_has_evidence_gate_dimensions() -> None:
    text = (ASSET_ROOT / "shared" / "rubric.md").read_text(encoding="utf-8")
    assert "## Evidence-gate dimensions" in text
    for dim in ("Grounding", "Coverage", "Depth", "Synthesis", "Open Problems"):
        assert dim in text, f"rubric missing evidence-gate dimension: {dim}"


def test_evidence_gate_scores_depth_and_synthesis() -> None:
    text = (ASSET_ROOT / "subagents" / "scientific-reviewer.md").read_text(encoding="utf-8")
    assert "Evidence-gate dimensions" in text
    assert "Depth" in text and "Synthesis" in text


def test_literature_review_skill_targets_a_survey() -> None:
    text = (ASSET_ROOT / "skills" / "literature-review" / "SKILL.md").read_text(encoding="utf-8")
    for marker in (
        "survey",
        "Comparative Synthesis",
        "Background and Foundations",
        "Problem Taxonomy",
        "Method Taxonomy",
        "Thematic Review",
        "Benchmarks and State of the Art",
        "Trends and Community Dynamics",
        "Open Problems",
        "mechanism",
        "Writing The Survey",
    ):
        assert marker.lower() in text.lower(), f"literature-review must target a survey: {marker}"
    assert "## Query Log" not in text
    assert "## Source Inventory" not in text
    assert "## Excluded or Unfetched Sources" not in text


def test_literature_review_skill_is_objective_and_strict() -> None:
    text = (ASSET_ROOT / "skills" / "literature-review" / "SKILL.md").read_text(encoding="utf-8")
    lower = text.lower()
    # Strict template: exact verbatim headings; only the ### deep-dives are agent-titled.
    assert "exact" in lower and "do not rename" in lower, "skill must mandate exact, unchangeable headings"
    # Objective and unbiased: the survey states what exists, never what to pursue.
    assert "objective" in lower and "neutral" in lower
    assert "say nothing about hypothesis" in lower, "skill must forbid hypothesis-generation framing"
    for banned in ("recommend", "rank", "select"):
        assert banned in lower, f"skill must forbid the survey to {banned} a direction"
    # The opinionated handoff section is gone; the survey ends at objective Open Problems.
    assert "Candidate Directions for Hypothesis Development" not in text
    assert "Recommended Direction" not in text
    assert "## Open Problems" in text


def test_method_spec_demands_math_precision() -> None:
    method = (ASSET_ROOT / "skills" / "hypothesis-method" / "SKILL.md").read_text(encoding="utf-8").lower()
    writeup = (ASSET_ROOT / "skills" / "proposal-writeup" / "SKILL.md").read_text(encoding="utf-8")
    rubric = (ASSET_ROOT / "shared" / "rubric.md").read_text(encoding="utf-8").lower()
    # Upstream method spec mandates formal notation, equations, algorithms, and full derivations.
    for marker in ("equation", "algorithm", "notation", "derivation", "no skipped steps"):
        assert marker in method, f"method spec must demand {marker}"
    # The proposal template carries a notation section and equation/algorithm/derivation requirements.
    assert "### Preliminaries and Notation" in writeup
    writeup_lower = writeup.lower()
    for marker in ("equation", "algorithm", "complete derivations with no skipped steps"):
        assert marker in writeup_lower, f"proposal template must demand {marker}"
    # Soundness rewards formalization and penalizes prose-only specification.
    assert "formaliz" in rubric and "prose-only" in rubric


def test_method_explains_design_rationale_and_why_it_works() -> None:
    method = (ASSET_ROOT / "skills" / "hypothesis-method" / "SKILL.md").read_text(encoding="utf-8").lower()
    writeup = (ASSET_ROOT / "skills" / "proposal-writeup" / "SKILL.md").read_text(encoding="utf-8")
    rubric = (ASSET_ROOT / "shared" / "rubric.md").read_text(encoding="utf-8").lower()
    # The method spec must require the reasoning behind the method, not just its mechanics.
    assert "design rationale" in method
    assert "why this design" in method
    assert "why it should work" in method
    # The proposal carries a dedicated Design Rationale section covering both halves.
    assert "### Design Rationale" in writeup
    writeup_lower = writeup.lower()
    assert "why it is designed this way" in writeup_lower
    assert "why it should work" in writeup_lower
    # Soundness rewards justified design.
    assert "justified" in rubric and "why it should work" in rubric


def test_experiments_justify_design_choices() -> None:
    plan = (ASSET_ROOT / "skills" / "hypothesis-method" / "SKILL.md").read_text(encoding="utf-8").lower()
    writeup = (ASSET_ROOT / "skills" / "proposal-writeup" / "SKILL.md").read_text(encoding="utf-8")
    rubric = (ASSET_ROOT / "shared" / "rubric.md").read_text(encoding="utf-8").lower()
    # Upstream experiment plan must justify each experimental choice.
    assert "justify each choice" in plan
    # The proposal carries a dedicated Design Justification section under Experiments.
    assert "### Design Justification" in writeup
    writeup_lower = writeup.lower()
    for marker in ("why this design tests the hypothesis", "why each choice"):
        assert marker in writeup_lower, f"proposal experiments must explain: {marker}"
    # Experimental Quality enumerates and rewards justified choices.
    assert "models, benchmarks, metrics" in rubric


def test_literature_review_is_comprehensive_and_iterative() -> None:
    skill = (ASSET_ROOT / "skills" / "literature-review" / "SKILL.md").read_text(encoding="utf-8")
    lower = skill.lower()
    rubric = (ASSET_ROOT / "shared" / "rubric.md").read_text(encoding="utf-8").lower()
    # The survey is built by iterating to convergence, not a single pass.
    assert "iterate to convergence" in lower
    assert "do not bring an un-revised first draft" in lower
    assert "self-audit" in lower
    # ...but convergence is bounded: one focused revision usually suffices, capped at
    # two passes, with the evidence gate (not endless self-polishing) as the backstop.
    assert "cap self-revision at two passes" in lower
    # Required taxonomies, SOTA, and dynamics sections.
    for section in ("## Problem Taxonomy", "## Method Taxonomy", "## Benchmarks and State of the Art", "## Trends and Community Dynamics"):
        assert section in skill, f"survey must require section: {section}"
    assert "state-of-the-art table" in lower
    # The evidence gate rewards comprehensiveness, taxonomy, and SOTA.
    assert "comprehensiveness" in rubric and "taxonom" in rubric and "state of the art" in rubric


def test_literature_review_reading_policy_is_summary_first() -> None:
    skill = (ASSET_ROOT / "skills" / "literature-review" / "SKILL.md").read_text(encoding="utf-8").lower()
    rubric = (ASSET_ROOT / "shared" / "rubric.md").read_text(encoding="utf-8").lower()
    reviewer = (ASSET_ROOT / "subagents" / "scientific-reviewer.md").read_text(encoding="utf-8").lower()
    # Skill: summary is the default working layer; raw reads are surgical and budgeted.
    assert "default working layer" in skill
    assert "surgical" in skill
    assert "budget raw reads" in skill
    # Summaries are read in small batches to cut turns, but bounded so the transcript
    # does not overflow the context window and force a compaction.
    assert "small batches" in skill
    assert "context window" in skill
    # Grounding is summary-first and CONSISTENT across rubric + reviewer (no raw-everything
    # contradiction): a summary grounds claims (incl. mechanism), raw only for verbatim figures.
    assert "default working layer" in rubric
    assert "verbatim" in rubric
    assert "not that every raw section be read" in rubric
    assert "summary-grounded claim is grounded" in reviewer


def test_hypothesis_method_requires_self_novelty_check() -> None:
    skill = (ASSET_ROOT / "skills" / "hypothesis-method" / "SKILL.md").read_text(encoding="utf-8")
    lower = skill.lower()
    # A dedicated novelty-check artifact, produced after the hypothesis is selected.
    assert "notes/novelty_check.md" in skill
    assert "## notes/novelty_check.md structure" in skill
    # Done by the lead itself, search-backed, to rule out duplicating prior work.
    assert "run it yourself" in lower
    assert "adversarial search" in lower
    assert "duplicate" in lower
    # The re-check loop is bounded (no infinite revise -> re-check thrash).
    assert "at most twice" in lower
    # The hypothesis_method gate's required-artifact set and the reviewer both include it.
    gate = (ASSET_ROOT / "hooks" / "stop_artifact_gate.py").read_text(encoding="utf-8")
    reviewer = (ASSET_ROOT / "subagents" / "scientific-reviewer.md").read_text(encoding="utf-8")
    assert "notes/novelty_check.md" in gate
    assert "notes/novelty_check.md" in reviewer


def test_reviewer_revision_budget_clears_noncritical_residue() -> None:
    reviewer = (ASSET_ROOT / "subagents" / "scientific-reviewer.md").read_text(encoding="utf-8").lower()
    startup = (ASSET_ROOT / "prompts" / "startup.md").read_text(encoding="utf-8").lower()
    # After the cycle budget, non-critical residue ships as accepted limitations (PASS), not an open gate.
    assert "revision budget" in reviewer
    assert "accepted residual limitations" in reviewer
    # A critical finding or failed precondition is never waived — keeps the reviewer aligned with the stop hook.
    assert "never waive" in reviewer
    assert "never waived" in startup


def test_grounding_and_attribution_hardening() -> None:
    rubric = (ASSET_ROOT / "shared" / "rubric.md").read_text(encoding="utf-8").lower()
    reviewer = (ASSET_ROOT / "subagents" / "scientific-reviewer.md").read_text(encoding="utf-8").lower()
    litrev = (ASSET_ROOT / "skills" / "literature-review" / "SKILL.md").read_text(encoding="utf-8").lower()
    # #1 misattribution is a precondition failure, not a shippable finding.
    assert "misattribut" in rubric and "mis-citation" in rubric
    assert "misattribution" in reviewer and "no_fabricated_literature" in reviewer
    # #2 web-search-only sources cannot anchor load-bearing / Open Problem claims.
    assert "web-search-only" in rubric
    assert "web-search-only" in reviewer
    assert "web-search only" in litrev


def test_novelty_extraction_and_ambition() -> None:
    hm = (ASSET_ROOT / "skills" / "hypothesis-method" / "SKILL.md").read_text(encoding="utf-8").lower()
    rubric = (ASSET_ROOT / "shared" / "rubric.md").read_text(encoding="utf-8").lower()
    reviewer = (ASSET_ROOT / "subagents" / "scientific-reviewer.md").read_text(encoding="utf-8").lower()
    # #3 closest works must be extracted, not abstract-read.
    assert "extract_paper" in hm and "abstract-only" in hm
    assert "abstract-read" in reviewer
    # #4 ambition within rigor; selection prefers the most ambitious defensible option.
    assert "ambition within rigor" in rubric
    assert "do not default to the safest" in hm


def test_final_consistency_pass() -> None:
    pw = (ASSET_ROOT / "skills" / "proposal-writeup" / "SKILL.md").read_text(encoding="utf-8").lower()
    reviewer = (ASSET_ROOT / "subagents" / "scientific-reviewer.md").read_text(encoding="utf-8").lower()
    assert "consistency pass" in pw
    assert "consistency check" in reviewer and "dangling" in reviewer


def test_evidence_gate_enforces_structure_and_neutrality() -> None:
    text = (ASSET_ROOT / "subagents" / "scientific-reviewer.md").read_text(encoding="utf-8")
    lower = text.lower()
    # Structural check: the gate verifies the survey's exact (expanded) section set.
    assert "exact section" in lower
    for section in ("## Thematic Review", "## Open Problems", "## Problem Taxonomy", "## Method Taxonomy", "## Benchmarks and State of the Art"):
        assert section in text, f"evidence gate must check section: {section}"
    assert "state-of-the-art currency" in lower
    # Neutrality check: the gate rejects advocacy and hypothesis framing.
    assert "neutrality" in lower
    assert "open_problems" in text, "evidence-gate score key must be open_problems"
