"""
LLM Client Factory - Creates appropriate LLM client based on configuration
"""

from ..config import (
    get_config,
    AnthropicProviderConfig,
    BedrockProviderConfig,
    DeepSeekProviderConfig,
    GeminiProviderConfig,
    OpenAIProviderConfig,
)
from ..llm.claude import LLMClaude
from ..llm.deepseek import LLMDeepSeek
from ..llm.fallback import FallbackLLMClient
from ..llm.gemini import LLMGeminiOpenAICompatible
from ..llm.openai import LLMOpenAI
from utils.logger import Logger

_logger = Logger()


def _create_single_client(provider_cfg, agent_cfg, model_id):
    """Create a single LLM client based on provider type."""
    if isinstance(provider_cfg, (BedrockProviderConfig, AnthropicProviderConfig)):
        return LLMClaude(provider_cfg, agent_cfg, model_id)

    if isinstance(provider_cfg, GeminiProviderConfig):
        return LLMGeminiOpenAICompatible(provider_cfg, agent_cfg, model_id)

    if isinstance(provider_cfg, DeepSeekProviderConfig):
        return LLMDeepSeek(provider_cfg, agent_cfg, model_id)

    if isinstance(provider_cfg, OpenAIProviderConfig):
        return LLMOpenAI(provider_cfg, agent_cfg, model_id)

    raise ValueError(f"Unsupported provider config type: {type(provider_cfg).__name__}")


def create_llm_client(agent_name: str):
    """
    Create LLM client based on configuration.

    Uses the unified get_agent_llm_config() method to get:
    - provider_config: Connection info (credentials, endpoints, retry settings)
    - agent_config: Runtime parameters (max_tokens, temperature, thinking, etc.)
    - model_id: Actual model identifier for API calls

    If the agent has a fallback_model configured, returns a FallbackLLMClient
    that wraps the primary and fallback clients transparently.  If the fallback
    client fails to initialise (e.g. missing credentials), the primary client is
    returned without fallback and a warning is logged.

    Args:
        agent_name: Agent name (e.g., 'lead_agent', 'peer_discussion_agent', 'context_compression')

    Returns:
        Appropriate client instance (LLMClaude, LLMOpenAI, LLMGeminiOpenAICompatible,
        or FallbackLLMClient wrapping any of the above)

    Raises:
        ValueError: When agent_name is unknown
    """
    config = get_config()

    # Get all config in one call
    provider_cfg, agent_cfg, model_id = config.get_agent_llm_config(agent_name)

    # Create primary client
    primary = _create_single_client(provider_cfg, agent_cfg, model_id)

    # If fallback is configured, wrap with FallbackLLMClient
    fallback_config = config.get_agent_fallback_llm_config(agent_name)
    if fallback_config is not None:
        fb_provider_cfg, fb_agent_cfg, fb_model_id = fallback_config
        fb_alias = config.get_agent_config(agent_name).fallback_model or ""
        try:
            fallback = _create_single_client(fb_provider_cfg, fb_agent_cfg, fb_model_id)
            return FallbackLLMClient(primary, fallback, fallback_model_alias=fb_alias)
        except Exception as exc:
            _logger.warning(
                f"[FALLBACK] Could not create fallback client for '{agent_name}': {exc}. "
                "Continuing without fallback."
            )

    return primary
