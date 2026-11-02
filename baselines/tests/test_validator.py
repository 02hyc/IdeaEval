from __future__ import annotations

from pathlib import Path

import pytest

from research_ideation_agent.validator import _ALIASES, validate_proposal


def test_validator_accepts_aliases_and_does_not_gate_impact_or_success(tmp_path: Path) -> None:
    proposal = tmp_path / "proposal.md"
    proposal.write_text(
        """
```markdown
## References
not a real heading for the validator
```

# Specific Proposal

## 1. Intro
Body.

### Background_and_Related_Work:
Body.

## Proposed Approach
Body.

## Evaluation Plan
Body.

## Bibliography
Body.
""",
        encoding="utf-8",
    )

    result = validate_proposal(proposal)

    assert result.valid
    assert result.missing == []
    assert "references" in result.present
    assert "impact_statement" not in result.present
    assert "success_criteria" not in result.missing


def test_validator_requires_non_empty_section_body(tmp_path: Path) -> None:
    proposal = tmp_path / "proposal.md"
    proposal.write_text(
        """
# Title
## Introduction
Body.
## Related Work
Body.
## Methods
## Experiments Design
Body.
## References
Body.
""",
        encoding="utf-8",
    )

    result = validate_proposal(proposal)

    assert not result.valid
    assert result.missing == ["methods"]


def test_validator_counts_nested_subsections_as_section_body(tmp_path: Path) -> None:
    proposal = tmp_path / "proposal.md"
    proposal.write_text(
        """
# Title
## Introduction
Body.
## Related Work
Body.
## Methods
### Stage 1
Body.
## Experiments Design
### Experiment 1
Body.
## References
Body.
""",
        encoding="utf-8",
    )

    result = validate_proposal(proposal)

    assert result.valid
    assert result.missing == []


def test_validator_accepts_additional_common_heading_aliases(tmp_path: Path) -> None:
    proposal = tmp_path / "proposal.md"
    proposal.write_text(
        """
# Title
## Motivation Section
Body.
## Prior Art
Body.
## Technical Approach
Body.
## Experiments & Evaluation
Body.
## Sources
Body.
""",
        encoding="utf-8",
    )

    result = validate_proposal(proposal)

    assert result.valid
    assert result.missing == []


def test_validator_reports_missing_file(tmp_path: Path) -> None:
    result = validate_proposal(tmp_path / "proposal.md")

    assert not result.valid
    assert "proposal.md does not exist." in result.errors


@pytest.mark.parametrize(
    "intent,alias",
    [(intent, alias) for intent, aliases in _ALIASES.items() for alias in sorted(aliases)],
)
def test_validator_accepts_every_documented_alias(tmp_path: Path, intent: str, alias: str) -> None:
    """Every alias in the contract's _ALIASES dict must be recognized by
    validate_proposal. Driven from the source-of-truth so adding/removing an
    alias in validator.py automatically updates the test surface."""

    body_lines = [
        "# Demo Title",
        "## Introduction",
        "Intro body.",
        "## Related Work",
        "RW body.",
        "## Methods",
        "Methods body.",
        "## Experiments Design",
        "Exp body.",
        "## References",
        "Ref body.",
    ]
    # Replace whichever section corresponds to `intent` with the alias under test.
    intent_to_canonical = {
        "introduction": "## Introduction",
        "related_work": "## Related Work",
        "methods": "## Methods",
        "experiments_design": "## Experiments Design",
        "references": "## References",
    }
    canonical = intent_to_canonical[intent]
    body_lines = [
        line.replace(canonical, f"## {alias.title()}", 1) if line == canonical else line
        for line in body_lines
    ]
    proposal_path = tmp_path / "proposal.md"
    proposal_path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")

    result = validate_proposal(proposal_path)

    assert intent in result.present, f"alias '{alias}' not recognized for intent '{intent}'"
