"""Runtime constants shared across modules."""

CHECKED_SECTION_IDS = [
    "title",
    "introduction",
    "related_work",
    "methods",
    "experiments_design",
    "references",
]

CANONICAL_HEADINGS = {
    "title": "# <Actual Proposal Title>",
    "introduction": "## Introduction",
    "related_work": "## Related Work",
    "methods": "## Methods",
    "experiments_design": "## Experiments Design",
    "references": "## References",
}

DEFAULT_CODEX_API_KEY_ENV = "GPT_API_KEY"
DEFAULT_CODEX_BASE_URL_ENV = "GPT_API_BASE_URL"


# Codex does not expose Claude Code's arbitrary `disallowed_tools` surface.
# These feature flags are the app-server controls that remove native tools
# outside the proposal-writing contract while leaving shell/file/edit and MCP
# access available.
DEFAULT_CODEX_DISABLED_FEATURES = [
    "apps",
    "auth_elicitation",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "image_generation",
    "in_app_browser",
    "plugin_sharing",
    "plugins",
    "request_permissions_tool",
    "standalone_web_search",
    "tool_call_mcp_elicitation",
    "tool_suggest",
    "web_search_cached",
    "web_search_request",
]

CODEX_NO_SUBAGENT_DISABLED_FEATURES = [
    "child_agents_md",
    "enable_fanout",
    "multi_agent",
    "multi_agent_v2",
]


# Tool names that the bundled Claude Code CLI recognizes for `disallowed_tools`
# / `allowed_tools` gating. Verified against the strings in
# `claude_agent_sdk/_bundled/claude` (SDK 0.2.91). Update when the bundled CLI
# version moves and adds/removes tools. MCP tools follow the
# `mcp__<server>__<tool>` pattern and are accepted separately.
KNOWN_CLAUDE_TOOL_NAMES = frozenset(
    [
        "AskUserQuestion",
        "Bash",
        "CronCreate",
        "CronDelete",
        "CronList",
        "Edit",
        "EnterPlanMode",
        "EnterWorktree",
        "ExitPlanMode",
        "ExitWorktree",
        "Glob",
        "Grep",
        "LSP",
        "Monitor",
        "NotebookEdit",
        "NotebookRead",
        "PushNotification",
        "Read",
        "ScheduleWakeup",
        "Skill",
        "Task",
        "TaskCreate",
        "TaskGet",
        "TaskList",
        "TaskOutput",
        "TaskStop",
        "TaskUpdate",
        "TodoWrite",
        "WebFetch",
        "WebSearch",
        "Workflow",
        "Write",
    ]
)
