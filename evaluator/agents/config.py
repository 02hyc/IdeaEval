"""
YAML Configuration Loader - Loads and validates config.yaml

New architecture with inline model providers:
- Models: Provider-specific connection info + model_id under provider-suffixed aliases
- Agents: Runtime parameters (max_tokens, temperature, thinking, etc.)
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple, Union, cast

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env_vars(obj):
    """
    Recursively expand ${VAR_NAME} placeholders in loaded YAML using environment variables.
    Unset variables expand to empty string.
    """
    if isinstance(obj, str):
        return _ENV_VAR_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), obj)
    if isinstance(obj, list):
        return [_expand_env_vars(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    return obj


# =============================================================================
# Provider Configs (connection info only)
# =============================================================================

class BedrockProviderConfig(BaseModel):
    """Connection info for AWS Bedrock - NO runtime parameters"""
    type: Literal["bedrock"] = Field(default="bedrock", description="Provider type")
    aws_access_key: SecretStr = Field(..., description="AWS Access Key")
    aws_secret_key: SecretStr = Field(..., description="AWS Secret Key")
    aws_region: str = Field(default="ap-northeast-1", description="AWS Region")
    
    # Retry parameters (provider-level, applies to all models using this provider)
    num_retries: int = Field(default=3, ge=1, description="Number of retry attempts")
    retry_multiplier: float = Field(default=1.0, ge=0.1, description="Exponential backoff multiplier")
    retry_start_wait: float = Field(default=1.0, ge=0.1, description="Initial wait time in seconds")
    
    @field_validator("aws_access_key", "aws_secret_key")
    @classmethod
    def validate_secrets_not_empty(cls, v: SecretStr):
        if v is None:
            raise ValueError("Missing required secret. Please set it via .env or config.yaml.")
        if not v.get_secret_value().strip():
            raise ValueError(
                "Secret value is empty. Populate it via environment variables (recommended) or config.yaml."
            )
        return v


class AnthropicProviderConfig(BaseModel):
    """Connection info for Anthropic API (native or proxy) - NO runtime parameters"""
    type: Literal["anthropic"] = Field(default="anthropic", description="Provider type")
    api_key: Optional[SecretStr] = Field(
        default=None,
        description="API key (recommended via ANTHROPIC_API_KEY env var)",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Base URL for Anthropic-compatible proxy",
    )
    anthropic_version: Optional[str] = Field(
        default=None,
        description="Optional anthropic_version header (only needed for some proxies)",
    )

    # Retry parameters (provider-level)
    num_retries: int = Field(default=3, ge=1, description="Number of retry attempts")
    retry_multiplier: float = Field(default=1.0, ge=0.1, description="Exponential backoff multiplier")
    retry_start_wait: float = Field(default=1.0, ge=0.1, description="Initial wait time in seconds")

    @model_validator(mode="after")
    def validate_api_key(self):
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            env_key = (os.getenv("ANTHROPIC_API_KEY", "") or "").strip()
            if not env_key:
                env_key = (os.getenv("OPENAI_API_KEY", "") or "").strip()
            if env_key:
                self.api_key = SecretStr(env_key)
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            raise ValueError(
                "Missing API key. Provide models.<name>.api_key or set ANTHROPIC_API_KEY."
            )
        if self.base_url is None or not str(self.base_url).strip():
            env_base = (os.getenv("ANTHROPIC_BASE_URL", "") or "").strip()
            if env_base:
                self.base_url = env_base
        return self


class OpenAIProviderConfig(BaseModel):
    """Connection info for OpenAI-compatible APIs - NO runtime parameters"""
    type: Literal["openai"] = Field(default="openai", description="Provider type")
    api_key: Optional[SecretStr] = Field(
        default=None,
        description="API key (recommended via OPENAI_API_KEY env var)",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Optional custom base URL (e.g., compatible proxy)",
    )

    # Retry parameters (provider-level)
    num_retries: int = Field(default=3, ge=1, description="Number of retry attempts")
    retry_multiplier: float = Field(default=1.0, ge=0.1, description="Exponential backoff multiplier")
    retry_start_wait: float = Field(default=1.0, ge=0.1, description="Initial wait time in seconds")

    @model_validator(mode="after")
    def validate_api_key(self):
        # Fill from env var if not provided
        if self.api_key is None:
            env_key = (os.getenv("OPENAI_API_KEY", "") or "").strip()
            if env_key:
                self.api_key = SecretStr(env_key)

        if self.api_key is None or not self.api_key.get_secret_value().strip():
            raise ValueError(
                "Missing API key. Provide models.<name>.api_key or set OPENAI_API_KEY."
            )
        return self


# NOTE: Gemini (OpenAI-compatible) differs in:
# - Many endpoints reject role="developer" (require role="system")
# - Thinking mode may require a `google.thought_signature` to be echoed back across turns
#   (handled in `agents/llm/gemini.py`).
class GeminiProviderConfig(BaseModel):
    """
    Connection info for Gemini via OpenAI-compatible endpoints.

    This exists as a distinct provider type so the client factory can route to
    `LLMGeminiOpenAICompatible` automatically (without hardcoded agent lists).
    """

    type: Literal["gemini"] = Field(default="gemini", description="Provider type")
    api_key: Optional[SecretStr] = Field(
        default=None,
        description="API key (recommended via GEMINI_API_KEY env var; falls back to OPENAI_API_KEY)",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Optional custom base URL (recommended via GEMINI_BASE_URL; falls back to OPENAI_BASE_URL)",
    )

    # Retry parameters (provider-level)
    num_retries: int = Field(default=3, ge=1, description="Number of retry attempts")
    retry_multiplier: float = Field(default=1.0, ge=0.1, description="Exponential backoff multiplier")
    retry_start_wait: float = Field(default=1.0, ge=0.1, description="Initial wait time in seconds")

    @model_validator(mode="after")
    def validate_api_key(self):
        # Treat missing/empty YAML-expanded values as "unset", then fill from env vars.
        # Prefer GEMINI_*, fall back to OPENAI_*.
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            env_key = (os.getenv("GEMINI_API_KEY", "") or "").strip()
            if not env_key:
                env_key = (os.getenv("OPENAI_API_KEY", "") or "").strip()
            if env_key:
                self.api_key = SecretStr(env_key)

        # Fill base_url from env vars if not provided. Prefer GEMINI_BASE_URL, fall back to OPENAI_BASE_URL.
        if self.base_url is None or not str(self.base_url).strip():
            env_base = (os.getenv("GEMINI_BASE_URL", "") or "").strip()
            if not env_base:
                env_base = (os.getenv("OPENAI_BASE_URL", "") or "").strip()
            if env_base:
                self.base_url = env_base

        if self.api_key is None or not self.api_key.get_secret_value().strip():
            raise ValueError(
                "Missing API key. Provide models.<name>.api_key or set GEMINI_API_KEY (or OPENAI_API_KEY)."
            )
        return self


# NOTE: DeepSeek's public API (https://api.deepseek.com) speaks OpenAI Chat
# Completions only — no Responses API. We route it through a dedicated provider
# so the client factory can pick LLMDeepSeek without hardcoded agent lists.
class DeepSeekProviderConfig(BaseModel):
    """
    Connection info for DeepSeek via OpenAI-compatible Chat Completions endpoints.

    Falls back to SILICONFLOW_* env vars (legacy naming used by .env) before
    DEEPSEEK_* and finally OPENAI_*.
    """

    type: Literal["deepseek"] = Field(default="deepseek", description="Provider type")
    api_key: Optional[SecretStr] = Field(
        default=None,
        description="API key (recommended via SILICONFLOW_API_KEY or DEEPSEEK_API_KEY env var)",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Optional custom base URL (recommended via SILICONFLOW_BASE_URL or DEEPSEEK_BASE_URL)",
    )

    # Retry parameters (provider-level)
    num_retries: int = Field(default=3, ge=1, description="Number of retry attempts")
    retry_multiplier: float = Field(default=1.0, ge=0.1, description="Exponential backoff multiplier")
    retry_start_wait: float = Field(default=1.0, ge=0.1, description="Initial wait time in seconds")

    @model_validator(mode="after")
    def validate_api_key(self):
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            env_key = (
                os.getenv("SILICONFLOW_API_KEY", "")
                or os.getenv("DEEPSEEK_API_KEY", "")
                or os.getenv("OPENAI_API_KEY", "")
                or ""
            ).strip()
            if env_key:
                self.api_key = SecretStr(env_key)

        if self.base_url is None or not str(self.base_url).strip():
            env_base = (
                os.getenv("SILICONFLOW_BASE_URL", "")
                or os.getenv("DEEPSEEK_BASE_URL", "")
                or ""
            ).strip()
            if env_base:
                self.base_url = env_base

        if self.api_key is None or not self.api_key.get_secret_value().strip():
            raise ValueError(
                "Missing DeepSeek API key. Provide models.<name>.api_key or set "
                "SILICONFLOW_API_KEY / DEEPSEEK_API_KEY."
            )
        return self


# Union type for any provider config
ProviderConfig = Union[
    BedrockProviderConfig,
    AnthropicProviderConfig,
    OpenAIProviderConfig,
    GeminiProviderConfig,
    DeepSeekProviderConfig,
]


# =============================================================================
# Inline Model Configs (provider-specific config + model_id)
# =============================================================================


class BedrockModelConfig(BedrockProviderConfig):
    """AWS Bedrock model config with inline connection info."""
    model_id: str = Field(..., description="Actual model identifier (e.g., ARN or model name)")


class AnthropicModelConfig(AnthropicProviderConfig):
    """Anthropic API (native or proxy) model config with inline connection info."""
    model_id: str = Field(..., description="Actual model identifier")


class OpenAIModelConfig(OpenAIProviderConfig):
    """OpenAI-compatible model config with inline connection info."""
    model_id: str = Field(..., description="Actual model identifier (e.g., ARN or model name)")


class GeminiModelConfig(GeminiProviderConfig):
    """Gemini model config with inline connection info."""
    model_id: str = Field(..., description="Actual model identifier (e.g., ARN or model name)")


class DeepSeekModelConfig(DeepSeekProviderConfig):
    """DeepSeek model config with inline connection info."""
    model_id: str = Field(..., description="Actual model identifier (e.g., deepseek-v4-pro)")


ModelConfig = Union[
    BedrockModelConfig,
    AnthropicModelConfig,
    OpenAIModelConfig,
    GeminiModelConfig,
    DeepSeekModelConfig,
]


# =============================================================================
# Agent Config (runtime parameters - unified for all agents)
# =============================================================================

class AgentConfig(BaseModel):
    """
    Runtime configuration for an agent.
    All agents share this same config structure.
    """
    model: str = Field(..., description="Model name (references a key in 'models' section)")
    max_tokens: int = Field(default=4000, gt=0, description="Maximum output tokens")
    max_context_tokens: int = Field(default=150000, gt=0, description="Maximum context window tokens")
    temperature: float = Field(default=0.5, ge=0.0, le=2.0, description="Sampling temperature")
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Top-p sampling")
    enable_thinking: bool = Field(default=False, description="Whether to enable extended thinking mode")
    thinking_budget_tokens: int = Field(default=4000, ge=0, description="Thinking mode token budget (used when thinking_type='enabled')")
    thinking_type: Literal["enabled", "adaptive"] = Field(
        default="enabled",
        description="Thinking mode type: 'enabled' (manual budget) for Claude 4.5 and earlier, "
                    "'adaptive' for Claude 4.6+",
    )
    
    # Fallback model (used when primary provider fails)
    fallback_model: Optional[str] = Field(
        default=None,
        description="Fallback model name (references a key in 'models' section). "
                    "If set, a FallbackLLMClient wraps the primary client.",
    )

    # Reasoning effort for OpenAI/DeepSeek models (low, medium, high, xhigh)
    reasoning_effort: Optional[Literal["low", "medium", "high", "xhigh"]] = Field(
        default=None,
        description="Reasoning effort level for OpenAI/DeepSeek models. "
                    "If set, overrides the thinking_budget_tokens-based mapping.",
    )

    # Agent-specific optional fields
    keep_recent_messages: Optional[int] = Field(
        default=None,
        ge=1,
        description="For context_compression: number of recent messages to keep"
    )


# =============================================================================
# Token Counter Configs
# =============================================================================

class ClaudeTokenCountConfig(BaseModel):
    """Claude token counting configuration (uses Anthropic API count_tokens)"""
    model: str = Field(..., description="Model ID for count_tokens API")
    method: str = Field(default="accurate", description="Token counting method: 'accurate' or 'estimated'")
    max_tokens: int = Field(default=128000, gt=0, description="Maximum token limit")
    max_retries: int = Field(default=3, ge=1, description="Maximum retry attempts")
    thinking_budget_tokens: int = Field(default=4000, gt=0, description="Token budget for thinking")

    # Anthropic API credentials - resolved from the selected model after config load
    api_key: Optional[SecretStr] = Field(default=None, description="Anthropic API key (resolved from the selected model)")
    base_url: Optional[str] = Field(default=None, description="Anthropic base URL (resolved from the selected model)")
    
    @field_validator('method')
    @classmethod
    def validate_method(cls, v: str) -> str:
        if v not in ['accurate', 'estimated']:
            raise ValueError(f"Method must be 'accurate' or 'estimated', got '{v}'")
        return v


class OpenAITokenCountConfig(BaseModel):
    """OpenAI token counting configuration (tiktoken-based)"""
    model: str = Field(..., description="Model name for tiktoken encoding lookup")
    encoding_name: Optional[str] = Field(
        default=None,
        description="Optional tiktoken encoding name fallback (e.g., 'o200k_base')",
    )
    method: str = Field(default="tiktoken", description="Token counting method: 'tiktoken' or 'estimated'")
    max_tokens: int = Field(default=128000, gt=0, description="Maximum token limit")

    @field_validator('method')
    @classmethod
    def validate_method(cls, v: str) -> str:
        if v not in ['tiktoken', 'estimated']:
            raise ValueError(f"Method must be 'tiktoken' or 'estimated', got '{v}'")
        return v


# =============================================================================
# Root Config
# =============================================================================

class Config(BaseModel):
    """
    Root configuration class that loads and validates config.yaml
    
    New architecture:
    - models: Provider-specific connection info + model_id under provider-suffixed aliases
    - agents: Runtime parameters for each agent
    
    Usage:
        config = Config.from_yaml()  # Load from default path
        config = Config.from_yaml("/path/to/config.yaml")  # Load from custom path
    """
    models: Dict[str, ModelConfig] = Field(
        default_factory=dict,
        description="Model configurations with inline provider-specific connection info"
    )
    agents: Dict[str, AgentConfig] = Field(
        default_factory=dict,
        description="Agent configurations (runtime parameters)"
    )
    
    # Token counting configs
    claude_token_count: Optional[ClaudeTokenCountConfig] = Field(
        default=None, 
        description="Claude token counting configuration"
    )
    openai_token_count: Optional[OpenAITokenCountConfig] = Field(
        default=None,
        description="OpenAI token counting configuration",
    )
    
    @model_validator(mode='after')
    def validate_references(self):
        """Validate that all references are valid"""
        # Validate agent references point to valid models
        for agent_name, agent_cfg in self.agents.items():
            if agent_cfg.model not in self.models:
                raise ValueError(
                    f"Agent '{agent_name}' references unknown model '{agent_cfg.model}'. "
                    f"Available models: {list(self.models.keys())}"
                )
            if agent_cfg.fallback_model is not None and agent_cfg.fallback_model not in self.models:
                raise ValueError(
                    f"Agent '{agent_name}' references unknown fallback_model '{agent_cfg.fallback_model}'. "
                    f"Available models: {list(self.models.keys())}"
                )

        return self
    
    # =========================================================================
    # Unified accessor methods
    # =========================================================================
    
    def get_agent_config(self, agent_name: str) -> AgentConfig:
        """
        Get configuration for any agent by name.
        
        Args:
            agent_name: Agent identifier (e.g., 'lead_agent', 'evaluation_agent')
            
        Returns:
            AgentConfig with runtime parameters
        """
        if agent_name not in self.agents:
            raise ValueError(
                f"Unknown agent '{agent_name}'. Available: {list(self.agents.keys())}"
            )
        return self.agents[agent_name]
    
    def get_model_config(self, model_name: str) -> ModelConfig:
        """Get model configuration by name."""
        if model_name not in self.models:
            raise ValueError(
                f"Unknown model '{model_name}'. Available: {list(self.models.keys())}"
            )
        return self.models[model_name]
    
    def get_agent_llm_config(
        self, agent_name: str
    ) -> Tuple[ProviderConfig, AgentConfig, str]:
        """
        Get all LLM configuration needed for an agent.
        
        Args:
            agent_name: Agent identifier
            
        Returns:
            Tuple of (provider_config, agent_config, model_id)
            - provider_config: Connection info (credentials, endpoints, retry settings)
            - agent_config: Runtime params (max_tokens, temperature, thinking, etc.)
            - model_id: Actual model identifier to use in API calls
        """
        agent_cfg = self.get_agent_config(agent_name)
        model_cfg = self.get_model_config(agent_cfg.model)
        return model_cfg, agent_cfg, model_cfg.model_id
    
    def get_agent_fallback_llm_config(
        self, agent_name: str
    ) -> Optional[Tuple[ProviderConfig, AgentConfig, str]]:
        """
        Get fallback LLM configuration for an agent, if configured.

        Returns:
            None if no fallback_model is set, otherwise
            Tuple of (provider_config, agent_config, model_id) for the fallback.
            The agent_config is the same as the primary (runtime params stay consistent).
        """
        agent_cfg = self.get_agent_config(agent_name)
        if agent_cfg.fallback_model is None:
            return None
        model_cfg = self.get_model_config(agent_cfg.fallback_model)
        return model_cfg, agent_cfg, model_cfg.model_id

    def get_provider_type(self, agent_name: str) -> str:
        """
        Get the provider type for an agent ('bedrock', 'openai', or 'gemini').
        
        Args:
            agent_name: Agent identifier
            
        Returns:
            Provider type string
        """
        agent_cfg = self.get_agent_config(agent_name)
        model_cfg = self.get_model_config(agent_cfg.model)
        return model_cfg.type
    
    def get_token_counter_config(self, agent_name: str):
        """
        Get token counter config for an agent.
        
        For Claude/Bedrock agents, derives the token counter from the selected
        model config unless an explicit claude_token_count override is provided.
        
        Args:
            agent_name: Agent identifier
            
        Returns:
            Token counter config (Claude or OpenAI) with credentials resolved
        """
        agent_cfg = self.get_agent_config(agent_name)
        model_cfg = self.get_model_config(agent_cfg.model)
        provider_type = model_cfg.type
        if provider_type in ("openai", "gemini", "deepseek"):
            if self.openai_token_count is None:
                raise ValueError("openai_token_count must be configured for OpenAI-compatible providers")
            return self.openai_token_count
        else:
            if self.claude_token_count is None:
                token_cfg = ClaudeTokenCountConfig(
                    model=model_cfg.model_id,
                    method="accurate",
                    max_tokens=agent_cfg.max_context_tokens,
                    thinking_budget_tokens=max(agent_cfg.thinking_budget_tokens, 1),
                )
            else:
                token_cfg = self.claude_token_count

            if isinstance(model_cfg, AnthropicModelConfig):
                token_cfg = token_cfg.model_copy()
                token_cfg.api_key = model_cfg.api_key
                token_cfg.base_url = model_cfg.base_url
            elif isinstance(model_cfg, BedrockModelConfig):
                # Fallback: resolve from env for Bedrock models used with Anthropic token counter
                token_cfg = token_cfg.model_copy()
                env_key = (os.getenv("ANTHROPIC_API_KEY", "") or os.getenv("OPENAI_API_KEY", "") or "").strip()
                if env_key:
                    token_cfg.api_key = SecretStr(env_key)
                env_base = (os.getenv("ANTHROPIC_BASE_URL", "") or "").strip()
                if env_base:
                    token_cfg.base_url = env_base

            return token_cfg
    
    # =========================================================================
    # Convenience accessors for common agents
    # =========================================================================

    @property
    def evaluation_agent(self) -> AgentConfig:
        """Get evaluation_agent config."""
        return self.get_agent_config("evaluation_agent")
    
    @property
    def paper_summarization_agent(self) -> AgentConfig:
        """Get paper_summarization_agent config."""
        return self.get_agent_config("paper_summarization_agent")
    
    @property
    def peer_discussion_agent(self) -> AgentConfig:
        """Get peer_discussion_agent config."""
        return self.get_agent_config("peer_discussion_agent")
    
    @property
    def context_compression(self) -> AgentConfig:
        """Get context_compression config."""
        return self.get_agent_config("context_compression")
    
    # =========================================================================
    # YAML loading
    # =========================================================================
    
    @classmethod
    def from_yaml(cls, path: Optional[str] = None) -> "Config":
        """
        Load configuration from YAML file
        
        Args:
            path: Optional path to config.yaml. If not provided, uses default path
                  (agents/config.yaml relative to this module)
        
        Returns:
            Validated Config instance
        """
        if path is None:
            config_dir = Path(__file__).parent
            config_path = config_dir / "config.yaml"
        else:
            config_path = Path(path)
        
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            loaded = yaml.safe_load(f)
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise ValueError(f"Config YAML must be a mapping/object at the root, got {type(loaded).__name__}")
            data = cast(Dict[str, Any], loaded)
        
        # Expand ${VAR} placeholders from environment variables
        data = _expand_env_vars(data)
        if not isinstance(data, dict):
            raise ValueError(
                f"Config after env expansion must be a mapping/object at the root, got {type(data).__name__}"
            )
        data = cast(Dict[str, Any], data)
        
        # Parse models with discriminator
        models_raw = data.get("models")
        if models_raw is not None:
            if not isinstance(models_raw, dict):
                raise ValueError(f"'models' must be a mapping/object, got {type(models_raw).__name__}")
            parsed_models: Dict[str, ModelConfig] = {}
            for name, model_data in models_raw.items():
                if not isinstance(name, str):
                    continue
                if not isinstance(model_data, dict):
                    raise ValueError(
                        f"models.{name} must be a mapping/object, got {type(model_data).__name__}"
                    )
                model_type = model_data.get("type")
                if not model_type:
                    raise ValueError(f"models.{name}.type is required when providers are inlined")
                if model_type == "bedrock":
                    parsed_models[name] = BedrockModelConfig.model_validate(model_data)
                elif model_type == "anthropic":
                    parsed_models[name] = AnthropicModelConfig.model_validate(model_data)
                elif model_type == "openai":
                    parsed_models[name] = OpenAIModelConfig.model_validate(model_data)
                elif model_type == "gemini":
                    parsed_models[name] = GeminiModelConfig.model_validate(model_data)
                elif model_type == "deepseek":
                    parsed_models[name] = DeepSeekModelConfig.model_validate(model_data)
                else:
                    raise ValueError(
                        f"models.{name}.type must be one of 'bedrock', 'anthropic', 'openai', "
                        f"'gemini', or 'deepseek'; got {model_type!r}"
                    )
            data["models"] = parsed_models
        
        return cls.model_validate(data)
    
    class Config:
        """Pydantic config"""
        extra = "forbid"


# =============================================================================
# Singleton and factory functions
# =============================================================================

_config_instance: Optional[Config] = None


def get_config() -> Config:
    """
    Get the singleton Config instance.
    Loads from YAML on first call, returns cached instance thereafter.
    
    Returns:
        The singleton Config instance
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = Config.from_yaml()
    return _config_instance


def reset_config() -> None:
    """Reset the singleton config instance. Useful for testing."""
    global _config_instance
    _config_instance = None


def load_config(path: Optional[str] = None) -> Config:
    """
    Load and validate configuration from YAML file.
    
    Note: If you want the singleton instance, use get_config() instead.
    This function always creates a new instance.
    
    Args:
        path: Optional path to config.yaml
        
    Returns:
        Validated Config instance
    """
    return Config.from_yaml(path)
