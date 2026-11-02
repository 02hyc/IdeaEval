"""Public runtime models for the MVP API."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal


Effort = Literal["minimal", "min", "low", "medium", "high", "xhigh", "max"]
Harness = Literal["claude", "codex"]
RunStatus = Literal["succeeded", "failed"]
FailureKind = Literal[
    "validation_failed",
    "adapter_error",
    "timeout",
    "budget_exceeded",
    "cancelled",
    "runtime_error",
]


def to_jsonable(value: Any) -> Any:
    """Convert dataclass model values to JSON-compatible values."""

    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: to_jsonable(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class SerializableModel:
    """Mixin for deterministic model serialization."""

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True)
class WorkspaceConfig(SerializableModel):
    root: Path
    run_id: str | None
    agent_dir: str
    clean_existing: bool


@dataclass(frozen=True)
class AssetsConfig(SerializableModel):
    prompt_template: Path
    repair_prompt_template: Path
    skills_dir: Path
    subagents_dir: Path | None = None
    root: Path | None = None
    version: str | None = None


@dataclass(frozen=True)
class ScholarGatewayConfig(SerializableModel):
    command: str
    cutoff_date: str | None
    restrict_domains: bool
    strict_filtering: bool
    mode: Literal["rules", "llm"]
    format: Literal["markdown", "text"]
    references_dir: str
    required_env_vars: list[str]
    optional_env_vars: list[str]
    startup_timeout_seconds: int
    tool_timeout_seconds: int
    approval_mode: Literal["approve"]
    max_concurrency: int = 20
    extract_max_concurrency: int = 20


@dataclass(frozen=True)
class MCPConfig(SerializableModel):
    profile: Literal["scholar-gateway"]
    scholar_gateway: ScholarGatewayConfig


@dataclass(frozen=True)
class PermissionsConfig(SerializableModel):
    approval_policy: Literal["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"]


@dataclass(frozen=True)
class BudgetsConfig(SerializableModel):
    timeout_seconds: int | None
    max_turns: int | None
    max_cost_usd: float | None


@dataclass(frozen=True)
class ModelPricingConfig(SerializableModel):
    input_per_million_usd: float | None = None
    cached_input_per_million_usd: float | None = None
    cache_creation_input_per_million_usd: float | None = None
    output_per_million_usd: float | None = None
    reasoning_output_per_million_usd: float | None = None
    input_includes_cached_tokens: bool = True
    long_context_threshold_tokens: int | None = None
    long_context_input_per_million_usd: float | None = None
    long_context_cached_input_per_million_usd: float | None = None
    long_context_output_per_million_usd: float | None = None
    long_context_reasoning_output_per_million_usd: float | None = None


@dataclass(frozen=True)
class PricingConfig(SerializableModel):
    models: dict[str, ModelPricingConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class RepairConfig(SerializableModel):
    strategy: Literal["external-loop"]
    max_attempts: int


@dataclass(frozen=True)
class SkillsConfig(SerializableModel):
    selected: list[str]


@dataclass(frozen=True)
class SubagentsConfig(SerializableModel):
    selected: list[str]


@dataclass(frozen=True)
class LoggingConfig(SerializableModel):
    write_run_record: bool
    retain_event_log: bool


@dataclass(frozen=True)
class ClaudeAdapterConfig(SerializableModel):
    strict_mcp_config: bool
    api_key_env: str
    base_url_env: str
    disallowed_tools: list[str] = field(default_factory=list)
    setting_sources: list[str] = field(default_factory=lambda: ["project"])
    sdk_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodexAdapterConfig(SerializableModel):
    api_key_env: str
    base_url_env: str
    sandbox: str
    approval_mode: str
    network_access_enabled: bool
    cleanup_codex_home: bool
    reasoning_summary: Literal["auto", "concise", "detailed", "none"] | None
    supports_reasoning_summaries: bool | None
    disabled_features: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AdaptersConfig(SerializableModel):
    claude: ClaudeAdapterConfig
    codex: CodexAdapterConfig


@dataclass(frozen=True)
class StopHookSpec(SerializableModel):
    script: Path
    timeout: int = 30


@dataclass(frozen=True)
class HooksConfig(SerializableModel):
    stop: list[StopHookSpec] = field(default_factory=list)


@dataclass(frozen=True)
class RunConfig(SerializableModel):
    version: int
    harness: Harness
    model: str | None
    model_profile: str | None
    effort: Effort | None
    workspace: WorkspaceConfig
    assets: AssetsConfig
    mcp: MCPConfig
    permissions: PermissionsConfig
    budgets: BudgetsConfig
    pricing: PricingConfig
    repair: RepairConfig
    skills: SkillsConfig
    subagents: SubagentsConfig
    logging: LoggingConfig
    adapters: AdaptersConfig
    hooks: HooksConfig = field(default_factory=HooksConfig)

    def to_redacted_dict(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass(frozen=True)
class RunRequest(SerializableModel):
    topic: str
    config: RunConfig
    config_path: Path | None = None
    overrides: dict[str, object] | None = None


@dataclass(frozen=True)
class ValidationResult(SerializableModel):
    valid: bool
    proposal_path: Path
    missing: list[str]
    present: list[str]
    errors: list[str]
    validator_version: str


@dataclass(frozen=True)
class UsageSummary(SerializableModel):
    input_tokens: int | None = None
    input_tokens_include_cached: bool | None = None
    cached_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None
    model_context_window: int | None = None
    total_cost_usd: float | None = None
    turns: int | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class RepairAttempt(SerializableModel):
    index: int
    instruction: str
    before_validation: ValidationResult
    after_validation: ValidationResult | None
    continued_session: bool | None
    previous_session_id: str | None
    session_id: str | None
    continuation_error: str | None
    usage: UsageSummary | None
    error: str | None


@dataclass(frozen=True)
class RunResult(SerializableModel):
    run_id: str
    status: RunStatus
    failure_kind: FailureKind | None
    harness: Harness
    workspace_path: Path
    agent_workspace_path: Path
    proposal_path: Path
    validation: ValidationResult
    repair_attempts: list[RepairAttempt]
    session_id: str | None
    model: str | None
    usage: UsageSummary | None
    artifacts: dict[str, Path]
    error: str | None
