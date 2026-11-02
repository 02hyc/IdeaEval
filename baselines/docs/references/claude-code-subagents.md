# Claude Code Subagents

Reference for designing, configuring, and invoking Claude Code subagents. The aim is that someone — human or agent — can read only this document and produce a working subagent without needing to consult upstream docs.

Authoritative upstream sources (read these if anything below seems stale):

- `https://code.claude.com/docs/en/sub-agents` — primary subagents page.
- `https://code.claude.com/docs/en/tools-reference` — `Agent` tool semantics and built-in subagents.
- `https://code.claude.com/docs/en/agent-sdk/subagents` — Agent SDK view, useful for wire-format details.
- `https://code.claude.com/docs/en/settings` — permission scope merging and managed restrictions.
- `https://code.claude.com/docs/en/env-vars` — concurrency, stall, model, and compaction overrides.
- `https://code.claude.com/docs/en/hooks` — `SubagentStart` / `SubagentStop` hook payloads.
- `https://code.claude.com/docs/en/interactive-mode` — interrupt and background-kill shortcuts.

## Table of contents

**Part 1 — Orientation**

1. [What a subagent is](#1-what-a-subagent-is)
2. [Subagent vs. skill vs. slash command](#2-subagent-vs-skill-vs-slash-command)

**Part 2 — Authoring a subagent**

3. [File anatomy and the field map](#3-file-anatomy-and-the-field-map)
4. [Required fields: `name` and `description`](#4-required-fields-name-and-description)
5. [The body (system prompt)](#5-the-body-system-prompt)

**Part 3 — Configuring behavior**

6. [Tools and permissions](#6-tools-and-permissions)
7. [Model and effort](#7-model-and-effort)
8. [Behavior limits](#8-behavior-limits)
9. [Context injection](#9-context-injection)
10. [Lifecycle hooks and initial prompt](#10-lifecycle-hooks-and-initial-prompt)
11. [Workspace isolation](#11-workspace-isolation)
12. [UI](#12-ui)

**Part 4 — Deployment and invocation**

13. [File location and scope precedence](#13-file-location-and-scope-precedence)
14. [Invocation: the `Agent` tool](#14-invocation-the-agent-tool)

**Part 5 — Runtime behavior**

15. [Context isolation and auto-loaded content](#15-context-isolation-and-auto-loaded-content)
16. [Permission inheritance](#16-permission-inheritance)
17. [MCP inheritance and scoping](#17-mcp-inheritance-and-scoping)
18. [Working directory](#18-working-directory)
19. [Token and cost accounting](#19-token-and-cost-accounting)
20. [Errors, interruption, and limits](#20-errors-interruption-and-limits)

**Part 6 — Patterns and debugging**

21. [Common tool kits by role](#21-common-tool-kits-by-role)
22. [Anti-patterns](#22-anti-patterns)
23. [Iteration and debugging](#23-iteration-and-debugging)

**Appendix**

- [A. Alphabetical field reference](#a-alphabetical-field-reference)
- [B. Fields that look real but do not exist](#b-fields-that-look-real-but-do-not-exist)
- [C. Maximalist worked example](#c-maximalist-worked-example)
- [D. Version-dependent behavior](#d-version-dependent-behavior)

---

# Part 1 — Orientation

## 1. What a subagent is

A subagent is an isolated agent invocation that the lead agent (the main conversation thread) can spawn through the `Agent` tool. Each subagent runs in its own context window with its own system prompt, tool restrictions, MCP server scoping, and model and effort overrides. It returns a single text result back to the lead agent; intermediate tool calls and outputs are not visible to the parent. Full transcripts persist on disk so the user can inspect them via the `/agents` UI.

A subagent is defined by a single Markdown file with YAML frontmatter, dropped into a known directory. The minimal viable subagent is two required frontmatter fields plus a body:

```markdown
---
name: greeter
description: Greets the user warmly. Use proactively at the start of every conversation.
---

You are a friendly greeter. When invoked, output one sentence welcoming the user
and asking what they want to work on today. Do not do anything else.
```

Save this as `.claude/agents/greeter.md` in your project root, restart the Claude Code session, and the lead agent can now spawn it via the `Agent` tool. With the description above, the lead agent will also auto-delegate to it whenever it judges a greeting is appropriate.

The rest of this document is about the choices you make beyond this minimal example: design `description` so the right thing fires, restrict tools so the subagent can't go off the rails, scope MCP and skills so it sees exactly the context it needs, and so on.

## 2. Subagent vs. skill vs. slash command

Subagents are one of three mechanisms for packaging reusable capability. The choice between them is the most consequential decision before you start writing — most reusable workflows that "feel like a subagent" are actually better off as skills.

| Mechanism | Context | Tools | Auto-invoked? | Best for |
|---|---|---|---|---|
| **Subagent** | Fresh, isolated | Restricted allowlist, separate from main | Yes (via `description`) | Self-contained side tasks that produce verbose intermediate output the parent does not need |
| **Skill** | Main conversation context | Whatever main session has | Yes (via `description`) | Reusable knowledge, conventions, or workflow templates that should run in the same context |
| **Slash command** | Main conversation, manually invoked | Whatever main session has | No (often `disable-model-invocation: true`) | Operator-triggered workflows whose side effects should not auto-fire |

Decision rules:

1. **Need isolated context that won't pollute the main thread?** → subagent.
2. **Need a tool list different from the main session?** → subagent.
3. **Want auto-invocation when relevant context appears?** → either skill or subagent; pick by isolation needs.
4. **Want operator-only triggering?** → slash command.
5. **Pure knowledge or convention injection?** → skill.
6. **Repeatable workflow with steps that should run in the current context?** → skill or slash command.

The trap: people reach for subagents because they sound powerful, but subagents pay a context-switching cost (fresh start, must rebuild context) that is only worth it when the alternative is flooding the main conversation. If the workflow does not produce verbose intermediate output and does not need a different tool list, it should be a skill.

---

# Part 2 — Authoring a subagent

## 3. File anatomy and the field map

A subagent file is YAML frontmatter followed by a Markdown body, separated by `---` markers:

```markdown
---
name: <required>
description: <required>
<optional fields...>
---

<Markdown body — this is the subagent's system prompt, verbatim, no template syntax>
```

The filename does not have to match `name`. The `name` field in the frontmatter is the only identifier — `code-reviewer.md` declaring `name: my-reviewer` will be invoked as `my-reviewer`.

The body is **the system prompt verbatim** — no variable substitution, no template syntax. Whatever you write between the closing `---` and the end of the file becomes the subagent's system prompt at startup.

The full configurable surface is 13 frontmatter fields. Two are required; the other 11 fall into seven dimensions of configuration:

| Dimension | Fields | Section |
|---|---|---|
| **Required (identity & routing)** | `name`, `description` | [§4](#4-required-fields-name-and-description) |
| Tools and permissions | `tools`, `disallowedTools`, `permissionMode` | [§6](#6-tools-and-permissions) |
| Model and effort | `model`, `effort` | [§7](#7-model-and-effort) |
| Behavior limits | `maxTurns`, `background` | [§8](#8-behavior-limits) |
| Context injection | `skills`, `mcpServers`, `memory` | [§9](#9-context-injection) |
| Lifecycle | `hooks`, `initialPrompt` | [§10](#10-lifecycle-hooks-and-initial-prompt) |
| Workspace | `isolation` | [§11](#11-workspace-isolation) |
| UI | `color` | [§12](#12-ui) |

A real production subagent typically uses four to six fields. Don't reach for the rest unless you have a concrete reason.

## 4. Required fields: `name` and `description`

### 4.1 `name`

**Type**: lowercase letters, digits, and hyphens. **No default — required.**

The unique identifier for the subagent. Also passed to hooks as `agent_type`. The filename does not have to match — only the `name` field is consulted at registration time.

Constraints:

- Must be lowercase: `code-reviewer`, not `Code-Reviewer` or `code_reviewer`.
- No spaces, dots, or underscores.
- Must be unique within its scope (see [§13](#13-file-location-and-scope-precedence)). Two files in the same scope declaring the same `name` cause one to be silently dropped at registration.

`name` does **not** participate in routing. The lead agent does not see the name when deciding whether to delegate. So do not encode trigger hints into the name — put them in `description`.

### 4.2 `description` — the only routing signal

**Type**: string. **No default — required.**

This is the field the lead agent reads when deciding whether to delegate to this subagent. It is the **only** signal — the body is invisible at routing time, and `name` is just an identifier. Treat `description` as instructions to a routing classifier, not as a human-facing summary.

#### Three-layer template

The official `code-reviewer` example is the reference shape:

> `Expert code review specialist. Proactively reviews code for quality, security, and maintainability. Use immediately after writing or modifying code.`

It has three layers, all of which a good description should have:

| Layer | Content | Purpose |
|---|---|---|
| 1. **Identity** | `Expert code review specialist` | What the subagent is |
| 2. **Capability** | `Proactively reviews code for quality, security, and maintainability` | What it does, in one sentence |
| 3. **Trigger** | `Use immediately after writing or modifying code` | When it should fire |

Generic template:

```
<role noun phrase>. <one-sentence capability>. Use PROACTIVELY when <trigger event 1>, <trigger event 2>, or <trigger event 3>.
```

#### Trigger phrasings, by assertiveness

Roughly increasing strength:

| Phrasing | Use when |
|---|---|
| `Use proactively` | General-purpose nudge |
| `Use immediately after <event>` | A specific event should fire it |
| `MUST BE USED before <event>` | A required gate in a workflow |

These are not reserved keywords — there is no special-keyword behavior. They work because the routing classifier reads them as instructions, not because they are magic tokens. The strength gradient is reproducible but not absolute.

#### Anti-patterns

1. **Vague description.** `description: Reviews code` gives the routing model no signal about *when*. Compare with `Expert code review specialist. Use immediately after writing or modifying code.` — same role, vastly higher hit rate.
2. **Overlapping responsibilities between subagents.** If two subagents could plausibly match the same prompt, their descriptions are insufficiently differentiated. Make the disambiguating words carry the load: `audit / vulnerabilities / compliance` for a security reviewer vs. `quality / maintainability / coverage` for a code-quality reviewer.
3. **One subagent doing too much.** A `code-quality-and-security-and-performance-and-tests` subagent has to enumerate four trigger conditions, none of which fire reliably. Split it into separate subagents with one trigger each.
4. **Trigger phrasing missing entirely.** Skipping `Use proactively` / `Use after X` / `MUST BE USED before X` leaves the classifier guessing.

#### `description` does not affect in-task behavior

Once the subagent is dispatched, only the body drives behavior. So writing `description` in a punchy, command-like tone is safe — it will not pollute the subagent's voice. Reserve the body for behavior; reserve the description for routing.

#### Implementation checklist

After writing a description, run through these:

- [ ] Includes **identity** (what kind of agent it is)?
- [ ] Includes **capability** (one sentence on what it does)?
- [ ] Includes a **trigger phrase** (`Use proactively` / `Use after X` / `MUST BE USED before X`)?
- [ ] Trigger condition is a **concrete event**, not an abstract state ("after writing code", not "when needed")?
- [ ] No **trigger overlap** with other subagents in the same scope?
- [ ] Length is **one to two sentences**? More than that usually means the subagent should be split.

#### Cross-subagent description audit

When you have multiple subagents, read all their descriptions back-to-back as one list. If you cannot tell at a glance which one a given user message would trigger, the descriptions are not differentiated enough. This is the fastest way to find overlap.

#### Auto-dispatch can also be blocked

A specific subagent can be excluded from auto-dispatch via `permissions.deny: ["Agent(my-custom-agent)"]` in `settings.json`. The subagent is still spawnable by explicit invocation (`@my-agent` or direct `subagent_type` in `Agent`); only auto-routing skips it.

## 5. The body (system prompt)

Everything after the closing `---` is the subagent's system prompt, fed verbatim at startup. There is no template syntax: no variable substitution, no Mustache, no Jinja. What you write is what the subagent reads.

### Recommended skeleton

Across the canonical `code-reviewer`, `debugger`, `data-scientist`, and `db-reader` examples, a consistent structure works:

```markdown
You are a <specific role> specializing in <narrow domain>.

When invoked:
1. <action>
2. <action>
3. <action>

<Domain-specific> checklist:
- <criterion with a measurable threshold>
- <criterion>

For each <unit of work>, provide:
- <output element>
- <output element>

<Closing constraint that names the failure mode to avoid>
```

Concrete guidance:

- **Open with a specific role identity, not a generic one.** `You are a senior code reviewer ensuring high standards of code quality and security.` sets voice for the entire transcript. Avoid `You are a helpful assistant.`
- **Use a numbered "When invoked" procedure.** It stops the agent from spending two turns deciding what to look at.
- **Make checklist criteria measurable.** `coverage > 80%` is checkable; `good test coverage` is not.
- **Declare the output format explicitly.** Without it, two invocations of the same subagent return wildly different shapes.
- **End with one anti-pattern directive.** `Focus on fixing the underlying issue, not the symptoms.` lets the agent satisfy the prompt without missing the point.

### What to keep out of the body

- **Anything already in CLAUDE.md or memory.** Subagents auto-load the full memory hierarchy at startup (see [§15](#15-context-isolation-and-auto-loaded-content)). Repeating those rules in the body just costs context.
- **Operator-facing introductions.** The body is not a README. Skip "This subagent reviews code by..." — the subagent is reading this, not a human.
- **Trigger conditions for itself.** Trigger conditions belong in `description`. The body sees only the prompt the lead passes; it cannot make routing decisions.

---

# Part 3 — Configuring behavior

The fields in this part are all optional. Each one has a default; a subagent that just sets `name` + `description` + body will work. Add fields as you have specific needs.

## 6. Tools and permissions

Three frontmatter fields manage which tools the subagent can call. They compose:

```
disallowedTools  →  removes from the inherited pool
tools            →  allowlist applied to whatever remains
permissionMode   →  how to handle prompts when a tool wants to act
```

### 6.1 `tools` — allowlist

**Type**: comma-separated string OR YAML list. **Default**: inherit every tool the parent has.

```yaml
# Comma-separated string
tools: Read, Grep, Glob, Bash
```

```yaml
# YAML list
tools:
  - Read
  - Grep
  - Glob
```

If the field is **omitted**, the subagent inherits everything the parent can use, including built-in tools and MCP tools. If the field is **set**, only what's listed is available.

Built-in tool names are PascalCase: `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash`, `WebFetch`, `WebSearch`, `Skill`, `NotebookEdit`, etc.

#### Don't enumerate MCP tools in `tools`

MCP tools have full names of the form `mcp__<server>__<tool>` (e.g. `mcp__github__list_issues`). The official guidance is to **not** list these in `tools`. The reasoning is that `tools` controls built-ins; MCP visibility is controlled via `mcpServers` ([§9.2](#92-mcpservers)) at server-level granularity.

Practically, this means:

- If you want **a restricted built-in set but full MCP access**, do not write `tools: Read, Grep, Glob` (which excludes all MCP tools because the allowlist doesn't include them). Use `disallowedTools` instead to ban specific built-ins while keeping MCP intact.
- If you want **subagent-specific MCP scoping**, use `mcpServers`, not `tools`.

### 6.2 `disallowedTools` — denylist

**Type**: comma-separated string OR YAML list. **Default**: none.

```yaml
disallowedTools: Write, Edit
```

When **both** `tools` and `disallowedTools` are set, `disallowedTools` is applied first, then `tools` is resolved against the remaining pool. A tool listed in both is removed.

The most common use case is "inherit MCP and most built-ins, but block writes":

```yaml
# Reviewer that can do anything except modify files
disallowedTools: Write, Edit
```

This is a tighter, more readable expression of intent than a long `tools` allowlist that has to enumerate everything you wanted to keep.

### 6.3 `permissionMode` — how to handle prompts

**Type**: enum. **Default**: inherits from parent.

| Value | Effect |
|---|---|
| `default` | Prompt the user on each tool call that needs permission |
| `acceptEdits` | Auto-approve edit operations; prompt for everything else |
| `auto` | Auto-approve everything (no prompts) |
| `dontAsk` | Auto-deny anything that would otherwise prompt |
| `bypassPermissions` | Skip the permission system entirely (most permissive) |
| `plan` | Plan mode — `ExitPlanMode` becomes available |

Caveats:

- **Parent overrides child for the most permissive modes.** If the parent is in `bypassPermissions`, `acceptEdits`, or `auto`, that mode wins and the subagent's `permissionMode` field is ignored. The child can only ever be **more** restrictive than the parent.
- **Plugin-source subagents have this field silently ignored.**
- **Background subagents auto-deny prompts** regardless of `permissionMode` ([§8.2](#82-background)).

### 6.4 Tools that are never available to subagents

Some tools depend on main-conversation UI state and cannot work from inside a subagent, even if listed in `tools`:

- `Agent` — subagents cannot spawn other subagents (nesting depth is capped at 1).
- `AskUserQuestion`
- `EnterPlanMode`
- `ExitPlanMode` (unless `permissionMode: plan`)
- `ScheduleWakeup`
- `WaitForMcpServers`

The `Agent(<name>)` syntax sometimes seen in `tools` allowlists only matters when the subagent is run as the *main* thread via `claude --agent`. Inside a normal subagent invocation it is silently ignored.

## 7. Model and effort

### 7.1 `model`

**Type**: model alias (`sonnet` / `opus` / `haiku`), full model ID, or the literal `inherit`. **Default**: `inherit`.

```yaml
# By alias
model: opus

# By full ID — useful to pin to a specific version
model: claude-opus-4-7

# Explicit inherit (same as omitting the field)
model: inherit
```

Resolution order, highest priority first:

1. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable (set to `inherit` to disable).
2. Per-invocation `model` parameter on the `Agent` tool call.
3. This frontmatter field.
4. Main conversation's model.

The built-in `Explore` agent is fixed to Haiku and ignores all overrides; `Plan` and `general-purpose` inherit. Managed `availableModels` policies filter subagent model selection too, by alias.

### 7.2 `effort`

**Type**: enum `low` / `medium` / `high` / `xhigh` / `max`. **Default**: inherits.

Controls reasoning depth for models that support effort levels. Not all models support all levels — `xhigh` and `max` may be rejected by Sonnet/Haiku.

The `CLAUDE_CODE_EFFORT_LEVEL` environment variable overrides this field when set.

### 7.3 Common gotcha — environment variables are sledgehammers

Both `CLAUDE_CODE_SUBAGENT_MODEL` and `CLAUDE_CODE_EFFORT_LEVEL` override **every subagent's frontmatter** in the session. If `model: opus` is being silently downgraded to Haiku, check the environment first.

## 8. Behavior limits

### 8.1 `maxTurns`

**Type**: positive integer. **Default**: unlimited.

Hard cap on agentic turns (one turn ≈ one round of model output + tool calls) before the subagent stops. When the cap is reached, the subagent stops and whatever it last produced is returned as the final result. **No warning, no graceful wind-down — it just stops.**

```yaml
maxTurns: 25
```

Sizing guidance: observe a successful run's turn count, leave 1.5–2× headroom. Too tight = truncated output; too loose = no real protection.

If you set `maxTurns`, structure the body so the subagent emits intermediate results (a partial report) at every major step. That way, if it does get truncated, the parent gets something useful instead of a half-formed sentence.

### 8.2 `background`

**Type**: bool. **Default**: `false`.

If `true`, this subagent **always** runs as a background task — concurrent with the parent, returning the `Agent` tool call immediately and notifying the parent on completion.

Compare to the `run_in_background` parameter on the `Agent` tool call:

| Where set | Granularity | Decided by |
|---|---|---|
| `Agent(..., run_in_background=true)` | Per call | The parent agent, dynamically |
| Frontmatter `background: true` | Per subagent | The subagent author, fixed |

Costs of background mode:

- **Auto-deny on permission prompts.** A background subagent has no UI to surface prompts on, so any tool call that would otherwise prompt the user is auto-denied and the subagent continues without that tool. Reserve background for tasks whose tool needs are pre-authorized.
- **Stall timeout.** A background subagent that produces no streaming progress for `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS` ms (default 600 000 = 10 min) is aborted and its partial output is returned. A long single Bash call inside a background subagent may trip this if it produces no streaming text in the meantime.
- **Different interruption keys.** `Esc` does not kill background subagents; use `Ctrl+X Ctrl+K` (twice within 3 s).

## 9. Context injection

These three fields change what the subagent sees in its system prompt at startup, beyond the body and the auto-loaded CLAUDE.md / memory hierarchy.

### 9.1 `skills`

**Type**: YAML list of skill names. **Default**: none.

```yaml
skills:
  - sql-style-guide
  - bigquery-cost-tips
```

Preloads the named skills' **full content** directly into the subagent's startup system prompt. Compare to a normal `Skill` tool call, which only loads the skill content when the model decides to invoke it.

| Mode | When skill content enters context |
|---|---|
| Default `Skill` invocation | At call time, on demand |
| Frontmatter `skills` preload | At subagent startup, always |

After preloading, the subagent can still invoke other (non-preloaded) skills via the `Skill` tool, unless `Skill` has been removed from `tools`.

Restrictions:

- Skills with `disable-model-invocation: true` cannot be preloaded.
- Preloaded skill bodies count against the subagent's startup token budget. High-frequency or always-applicable skills are good preload candidates; situational ones should stay on demand.

### 9.2 `mcpServers`

**Type**: YAML list (server-name strings or inline server definitions). **Default**: inherits all parent MCP servers.

Three usage patterns:

```yaml
# Pattern A — inherit a subset of the parent's servers
mcpServers:
  - scholar-gateway
  # github is on the parent but excluded here
```

```yaml
# Pattern B — define a server inline; subagent-private
mcpServers:
  - playwright:
      type: stdio
      command: npx
      args: ["-y", "@playwright/mcp@latest"]
```

```yaml
# Pattern C — mix
mcpServers:
  - scholar-gateway
  - playwright:
      type: stdio
      command: npx
      args: ["-y", "@playwright/mcp@latest"]
```

Notes:

- **Inline server schema is the same as `.mcp.json`.** You can copy an existing server config verbatim.
- **Inline servers connect at subagent startup and disconnect on subagent finish.** Their tool descriptions are not added to the parent's tool catalog, so they don't pollute parent context.
- **Plugin-source subagents have this field silently ignored** for security reasons.
- As of v2.1.153, managed MCP restrictions (`--strict-mcp-config`, `--bare`, `allowedMcpServers`/`deniedMcpServers`) cover servers declared in subagent frontmatter. `--strict-mcp-config` does **not** filter servers passed inline via `--agents` JSON or the SDK `agents` option, since those are explicit caller input.

To strip MCP entirely from a subagent: set `mcpServers: []` (empty list).

### 9.3 `memory`

**Type**: enum `user` / `project` / `local`. **Default**: none (no persistent memory).

Enables a persistent memory directory for this subagent and auto-loads `MEMORY.md` from it into the system prompt at startup.

| Value | Scope |
|---|---|
| `user` | User-scope memory (visible across projects) |
| `project` | Project-scope memory (typically committed to VCS) |
| `local` | Project-scope but un-committed (kept on developer's machine only) |

Auto-loaded content is capped at **200 lines / 25 KB** — anything beyond is truncated.

The subagent is auto-granted `Read` / `Write` / `Edit` access to its memory directory **regardless of the `tools` field**. So the subagent can update its own `MEMORY.md` between invocations to maintain state.

This is the only official mechanism for cross-invocation persistence. Everything else (CLAUDE.md, skill bodies) is read-only at the per-spawn level.

### Three-field interaction summary

All three fields are loaded **once at startup**. Editing the underlying skill / MCP config / memory files mid-run does not affect the in-flight subagent — only the next spawn.

All three consume system-prompt token budget. Used together, they can dwarf the body. Profile your startup token usage if a subagent feels sluggish or expensive.

## 10. Lifecycle hooks and initial prompt

### 10.1 `hooks`

**Type**: mapping of event name to a list of `{matcher, hooks: [...]}` configs. **Default**: none.

Same schema as the `hooks` block in `settings.json`, but scoped only to this subagent.

```yaml
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/validate-readonly-query.sh"
  Stop:
    - matcher: ""
      hooks:
        - type: command
          command: "./scripts/check-output.sh"
```

Notes:

- Supported event names mirror the global hooks system: `PreToolUse`, `PostToolUse`, `Stop`, `Notification`, `UserPromptSubmit`, etc.
- **`Stop` is auto-converted to `SubagentStop` at runtime.** Writing `Stop` in a subagent's frontmatter does not intercept parent stop — only this subagent's own stop event.
- **`SubagentStop` is the one place you can block subagent completion.** A `SubagentStop` hook returning exit code 2 prevents the subagent from stopping and hands control back to it. `SubagentStart` is context-only and cannot block.
- **Plugin-source subagents have `hooks` silently ignored** for security reasons.

Common uses: validating Bash commands before execution, logging tool calls to an audit trail, gating subagent completion on a programmatic check.

### 10.2 `initialPrompt`

**Type**: string. **Default**: none.

Auto-submitted as the first user turn — **but only when this subagent runs as the main session via `claude --agent <name>` or the `agent` setting**. When spawned via the `Agent` tool from a parent, this field has no effect.

```yaml
initialPrompt: "Greet the user and ask which dataset to inspect."
```

If you only ever invoke this subagent via `Agent` from a parent agent, you can ignore this field — it is dead in that path. The first user turn comes from the parent's `prompt` parameter.

## 11. Workspace isolation

### `isolation`

**Type**: enum. **Default**: none. **Only valid value**: `worktree`.

```yaml
isolation: worktree
```

Runs the subagent in a temporary git worktree branched from the **default branch** (typically `main`), **not** the parent's HEAD. Counter-intuitive consequence: if the parent agent has uncommitted changes, the subagent does not see them.

Cleanup:

- If the subagent makes no file changes → worktree is auto-cleaned.
- If the subagent makes changes → worktree is **not** cleaned; the result includes its path and branch so the parent can decide whether to merge or discard.

The frontmatter `isolation: worktree` and the `Agent` tool's `isolation` parameter both control the same mechanism, at different granularities:

| Where set | Granularity | Behavior |
|---|---|---|
| `Agent(..., isolation="worktree")` | Per call | Just this invocation runs in a worktree |
| Frontmatter `isolation: worktree` | Per subagent | This subagent always runs in a worktree |

Common uses: exploratory refactors that shouldn't touch the working tree, destructive batch operations that may need to be discarded.

## 12. UI

### `color`

**Type**: enum `red` / `blue` / `green` / `yellow` / `purple` / `orange` / `pink` / `cyan`. **Default**: none.

Cosmetic. Used by the `/agents` task list and transcript UI to tag this subagent's entries with a color. Does not affect behavior or routing in any way.

```yaml
color: cyan
```

The behavior of unsupported color values (e.g. `magenta`) is undocumented; stick to the eight listed values.

---

# Part 4 — Deployment and invocation

## 13. File location and scope precedence

Subagents are loaded from five scopes, listed by priority (highest wins on `name` collision):

| Priority | Location | Scope |
|---|---|---|
| 1 (highest) | Managed settings directory's `.claude/agents/` | Organization-wide |
| 2 | `--agents` JSON CLI flag | Current session |
| 3 | `.claude/agents/` (project root) | Project |
| 4 | `~/.claude/agents/` | User |
| 5 (lowest) | Plugin's `agents/` directory | Where plugin is enabled |

`.claude/agents/` and `~/.claude/agents/` are scanned **recursively**, but the subdirectory path does not affect identity — only the `name` frontmatter field. `.claude/agents/team/reviewer.md` and `.claude/agents/reviewer.md` collide if both declare `name: reviewer`.

Plugin scopes work differently: a subfolder inside a plugin's `agents/` directory becomes part of a scoped name like `my-plugin:review:security`.

`--add-dir` directories grant file *access* to subagents but are **not** scanned for subagent definitions. There is no "local" scope for subagents (unlike settings, MCP, and CLAUDE.md) — to keep a subagent un-checked-in, put it in `~/.claude/agents/` or use `--agents` JSON.

Within one scope, two files declaring the same `name` cause one to be silently dropped at registration. Names must be unique across the whole tree of one scope.

### Loading and reload

Subagents are loaded at session start. Editing a subagent file directly on disk requires restarting the session for the change to take effect. Edits made through the `/agents` interactive UI take effect immediately.

## 14. Invocation: the `Agent` tool

The lead agent invokes a subagent via the `Agent` tool (renamed from `Task` in Claude Code v2.1.63; both names still work as aliases, so code inspecting raw tool-use blocks should accept both). One `Agent` tool call = one subagent spawn.

### 14.1 Inputs

The tool takes six parameters, two required:

| Field | Required | Purpose |
|---|---|---|
| `description` | ✅ | A 3-5 word label shown in the task list and transcript UI. Display-only — does not affect subagent behavior or routing. |
| `prompt` | ✅ | The full task message handed to the subagent as its first user turn. **This is the only data channel from parent to child** — the subagent does not see prior conversation history, parent-read files, or parent-invoked skills. The prompt must be self-contained. |
| `subagent_type` | optional | Selects which subagent to spawn. Resolves against the `name` frontmatter field of registered subagents, plus built-ins like `Explore`, `Plan`, `general-purpose`. Defaults to `general-purpose` when omitted. |
| `model` | optional | Per-invocation model override. The current Claude Code `Agent` tool schema accepts only the aliases `sonnet` / `opus` / `haiku`. Resolution order: `CLAUDE_CODE_SUBAGENT_MODEL` env var → this parameter → frontmatter `model` field → main conversation model. |
| `isolation` | optional | Set to `"worktree"` to run the spawn in a temporary git worktree branched from the default branch. Auto-cleaned if the subagent makes no changes; otherwise the result includes the worktree path and branch. |
| `run_in_background` | optional | If `true`, the subagent runs as a background task: the `Agent` call returns immediately and the parent is notified when the subagent finishes. Default `false`. |

### 14.2 Outputs

The `Agent` tool returns **only the subagent's final text message** as the tool result. Intermediate tool calls and partial outputs are not visible to the parent — only the final string. The result text includes an `agentId:` line that can be parsed for later inspection of the transcript.

There is no structured token-usage object returned alongside the result. Cost shows up in `/usage`, attributed under the "subagents" breakdown. Full transcripts persist at `~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl` (cleaned up by `cleanupPeriodDays`, default 30 days).

### 14.3 Parallelism vs. background — two orthogonal mechanisms

`run_in_background` controls whether *one* call blocks the parent. Issuing multiple `Agent` tool_use blocks in a single assistant message controls whether *multiple* subagents run at once. They compose freely:

| Goal | How |
|---|---|
| Run several subagents in parallel, then wait for all results before continuing | Multiple `Agent` tool_use blocks in one assistant message (foreground default) |
| Run a single subagent in the background while the parent keeps working | One `Agent` call with `run_in_background: true` |
| Run N subagents concurrently *and* keep the parent unblocked | N `Agent` tool_use blocks, each with `run_in_background: true` |

Concurrency across the whole session is bounded by `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` (default 10), shared with read-only built-in tools.

### 14.4 Routing: how a subagent gets selected

Four documented routing paths, all of which ultimately resolve to a `subagent_type` argument on an `Agent` tool call:

1. **Automatic delegation.** The lead model reads the user's request, the `description` field of every registered subagent, and the current context, and decides on its own whether to delegate.
2. **Natural-language naming.** A user message that names the subagent ("use the test-runner subagent to …") usually causes Claude to delegate, but does not force it.
3. **`@`-mention.** `@my-agent` in a user message forces exactly one task to run on that subagent. The user's request is still relayed by Claude.
4. **Session-wide via `--agent <name>` or the `agent` setting.** The main thread itself adopts the subagent's system prompt, tools, and model. CLAUDE.md and project memory still load.

A specific subagent can be excluded from auto-dispatch via `permissions.deny: ["Agent(my-custom-agent)"]` in `settings.json`.

### 14.5 Nesting limit

Subagents **cannot spawn other subagents**. The `Agent` tool is on the hard blocklist of tools subagents cannot use, regardless of their `tools` allowlist. Nesting depth is capped at 1.

If chained delegation is needed, do it from the main conversation, use Skills (which run in the calling context, not as a separate spawn), or escalate to the experimental Agent Teams feature (~7× the cost vs. subagents — see [§19](#19-token-and-cost-accounting)).

---

# Part 5 — Runtime behavior

The fields in Part 3 control most of what happens at runtime, but several behaviors are not tunable per-subagent. Knowing what's auto-loaded and how inheritance works prevents whole categories of confusion.

## 15. Context isolation and auto-loaded content

Each subagent starts with a fresh, isolated context window. It does **not** see the parent's conversation history, files the parent has read, or skills the parent has invoked. The lead agent's `prompt` argument is the only delegation message.

Each subagent's initial context is composed of:

- Its own system prompt (the body plus environment details Claude Code appends).
- The task message from the parent (the `prompt` argument).
- **CLAUDE.md and the full memory hierarchy** — `~/.claude/CLAUDE.md`, project rules, `CLAUDE.local.md`, and any managed policy files.
- A **git status** snapshot taken at the start of the parent session.
- **Preloaded skill content** for any skill listed in the `skills` frontmatter field ([§9.1](#91-skills)).
- **`MEMORY.md`** if `memory` is configured ([§9.3](#93-memory)).

The two built-in subagents `Explore` and `Plan` are the only ones that omit CLAUDE.md and git status. Every other built-in and every custom subagent receives them. There is no per-agent setting to change this. **Anything that should be hidden from a subagent must therefore be moved out of CLAUDE.md** — into a skill, for example.

### Forks are an exception

Forked subagents (`/fork`, or `CLAUDE_CODE_FORK_SUBAGENT=1`) inherit the entire parent conversation, plus the parent's system prompt, tools, and model. Their tool calls remain hidden from the parent; only the final result is returned. Forks reuse the parent's prompt cache, making them cheaper than named subagents on the first turn.

## 16. Permission inheritance

Permission rules **merge across scopes** rather than override. Priority is Managed > CLI args > Local > Project > User. Both `allow` and `deny` arrays accumulate; `deny` always wins on tie.

A subagent cannot grant itself tools or paths the parent has been denied — permission rules are checked when each tool call fires, not at subagent spawn time. A subagent *can* add restrictions on top via its own `tools`, `disallowedTools`, hooks, or a tighter `permissionMode`. The exception is the most permissive parent modes (`bypassPermissions`, `acceptEdits`, `auto`), which override the child's `permissionMode` ([§6.3](#63-permissionmode--how-to-handle-prompts)).

Foreground subagents surface permission prompts to the user as tool calls happen. Background subagents auto-deny any tool call that would otherwise prompt and keep going without it.

## 17. MCP inheritance and scoping

By default, subagents inherit all of the parent's MCP servers. The `mcpServers` frontmatter field can scope this in three ways: by inheriting a subset, by adding inline servers, or by mixing the two ([§9.2](#92-mcpservers)).

Practical guidance:

- **Subagent-only servers** (servers the parent shouldn't see at all) → declare them inline in the subagent's `mcpServers`. Their tool descriptions stay out of the parent's tool catalog.
- **Restrict to a subset of parent servers** → list only the names you want.
- **No MCP at all** → set `mcpServers: []`.

## 18. Working directory

Default cwd matches the parent's. `cd` inside a subagent does not persist between Bash calls — each Bash tool call starts at the subagent's launch directory.

`isolation: worktree` runs the subagent in a temporary git worktree branched from the default branch (not the parent's HEAD); the worktree is auto-cleaned if the subagent makes no changes ([§11](#11-workspace-isolation)).

## 19. Token and cost accounting

Subagent tokens are billed against the same account as the parent. `/usage` (Pro/Max/Team/Enterprise plans) attributes recent usage to skills, subagents, plugins, and individual MCP servers, each as a percentage of the total. The session's dollar figure aggregates parent and subagent usage.

Forks reuse the parent's prompt cache and are therefore cheaper than named subagents for the same context. Named subagents have their own cache key — the first turn pays full cost.

The experimental Agent Teams feature is documented as ~7× more expensive than subagents because each teammate maintains its own context window. Subagents are the cheap default for delegation.

## 20. Errors, interruption, and limits

### 20.1 Failure modes

The parent only sees the subagent's final text. Specific failure modes that end a subagent:

- **`maxTurns` reached** → subagent stops; whatever it last produced is returned.
- **Auto-compaction** at ~95% context capacity (`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` lowers the threshold) → subagent summarizes and continues; logged as `compact_boundary` in the transcript.
- **Background stall** — `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS` (default 600 000 ms) without streaming progress → aborted, partial output returned.
- **Tool denial** — subagent keeps running without that tool. Hard failures end the subagent and surface its last produced text to the parent.

### 20.2 Hooks for failure handling

`SubagentStart` and `SubagentStop` hooks are the documented integration points for failure handling. `SubagentStart` is context-only — it cannot block. `SubagentStop` *can* block: an exit code 2 prevents the subagent from stopping and returns control. The hook payload includes `agent_id`, `agent_type`, and `transcript_path`. `Stop` hooks declared in subagent frontmatter are auto-converted to `SubagentStop` at runtime.

### 20.3 Interruption

`Esc` interrupts whatever turn is currently active in the foreground (parent or foreground subagent), preserving completed work. `Esc` does **not** kill background subagents; they keep running until either:

- `Ctrl+X Ctrl+K` (press twice within 3 seconds to confirm) — kill all running background subagents.
- The fork panel's `x` key — dismiss a finished fork or stop a running one.
- Session exit — background tasks are auto-cleaned when Claude Code exits.

### 20.4 Limits

| Limit | Default | Source |
|---|---|---|
| Subagent nesting depth | **1** (subagents cannot spawn subagents) | hard rule |
| Concurrent subagents (shared with read-only tools) | 10 | `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` |
| Background stall timeout | 600 000 ms | `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS` |
| Auto-compact trigger | ~95% context | `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` |
| Bash inside subagent | 2 min default / 10 min hard cap | `BASH_DEFAULT_TIMEOUT_MS` / `BASH_MAX_TIMEOUT_MS` |
| Bash output | 30 000 chars default / 150 000 ceiling | `BASH_MAX_OUTPUT_LENGTH` |
| Background bash stdout | 5 GB auto-terminate | env-vars page |

`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` disables the `run_in_background` parameter, auto-backgrounding, and `Ctrl+B` entirely.

---

# Part 6 — Patterns and debugging

## 21. Common tool kits by role

Drawn from official examples and community collections. Use as a starting point, then trim to the principle of least privilege.

| Role | Tools |
|---|---|
| Read-only reviewer / auditor | `Read, Grep, Glob` |
| Reviewer that needs git diff or to run linters | `Read, Grep, Glob, Bash` |
| Modifier of existing files (debugger style) | `Read, Edit, Bash, Grep, Glob` (note `Edit`, not `Write`) |
| Greenfield writer (data scientist) | `Bash, Read, Write` |
| External research | `Read, Grep, Glob, WebFetch, WebSearch` |

**`Edit` without `Write` is the documented middle ground** for "fix existing files but do not create new ones." Granting `Write` to a reviewer is the most common subagent design mistake — the agent will sometimes "helpfully" fix what it found instead of reporting it, defeating the review/implement separation.

If the goal is "inherit MCP and most builtins but block writes," prefer `disallowedTools: Write, Edit` over enumerating an allowlist.

## 22. Anti-patterns

Consolidated checklist. Each item has a more detailed treatment in the relevant section above.

1. **Vague `description`.** `description: Reviews code` gives the routing model no signal about *when*. Always include a trigger phrase. (See [§4.2](#42-description--the-only-routing-signal).)
2. **Overlapping `description`s across subagents.** If two could plausibly match the same prompt, the descriptions are insufficiently differentiated. Make disambiguating words carry the load.
3. **One subagent doing too much.** A description that has to enumerate four trigger conditions reliably triggers on none of them. Split.
4. **Tool list mismatched to task.** Granting `Edit` or `Write` to a reviewer is the single most common mistake. See [§21](#21-common-tool-kits-by-role).
5. **Bloated body restating CLAUDE.md.** Subagents auto-load CLAUDE.md (except `Explore`/`Plan`). Repeating wastes context.
6. **Treating subagents as persistent assistants.** Each invocation is a fresh context unless `memory` is configured.
7. **Reaching for `bypassPermissions` to silence prompts.** Use a tighter `tools` list instead — it accomplishes the same thing without giving up the safety rail.
8. **Forgetting the closing anti-pattern directive in the body.** Without it, the agent can technically satisfy the prompt while missing the point. (See [§5](#5-the-body-system-prompt).)
9. **Listing `mcp__server__tool` names in `tools`.** Use `mcpServers` to scope MCP servers; reserve `tools` for built-ins.
10. **Setting `initialPrompt` on a subagent that's only ever spawned via `Agent`.** It does nothing in that path — the field only fires when the subagent runs as the main session.

## 23. Iteration and debugging

- **The `/agents` interactive UI is the fast iteration loop** — edits made through it take effect immediately. Edits to `.md` files on disk require a session restart.
- **Use `claude --agents '{...}'` JSON form** to test description-routing variants without committing files.
- **Force-dispatch with `@my-agent`** to debug body behavior in isolation, separately from auto-delegation tuning.
- **Read the transcript directly** at `~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl` when something goes wrong — it shows the full system prompt, task message, and tool calls.

### Symptom → likely cause

| Symptom | Likely cause |
|---|---|
| "My subagent never gets picked." | `description` is topical, not trigger-shaped. Or two subagents have overlapping descriptions. Or a higher-priority scope is shadowing it (check `/agents` Library tab). Or the file was added on disk and the session was not restarted. |
| "My subagent picks but does the wrong thing." | Body problem. Strengthen the "When invoked" procedure, add measurable checklist thresholds, add a closing anti-pattern directive, and tighten the tools list. |
| "Subagent silently runs out of turns." | `maxTurns` reached. Either raise it, or restructure the body to emit useful intermediate output before each major step. |
| "`model: opus` is being ignored." | Check `CLAUDE_CODE_SUBAGENT_MODEL`. Environment variable overrides frontmatter. |
| "MCP tools aren't showing up in the subagent." | Either `tools` is set without including MCP (allowlist excludes them), or `mcpServers` is set with an unintended subset, or the parent doesn't have the server mounted. |

---

# Appendix

## A. Alphabetical field reference

| Field | Type | Default | Section |
|---|---|---|---|
| `background` | bool | `false` | [§8.2](#82-background) |
| `color` | enum (8 colors) | none | [§12](#12-ui) |
| `description` | string | — (required) | [§4.2](#42-description--the-only-routing-signal) |
| `disallowedTools` | comma string or list | none | [§6.2](#62-disallowedtools--denylist) |
| `effort` | enum `low`/`medium`/`high`/`xhigh`/`max` | inherits | [§7.2](#72-effort) |
| `hooks` | mapping (event → matcher list) | none | [§10.1](#101-hooks) |
| `initialPrompt` | string | none | [§10.2](#102-initialprompt) |
| `isolation` | enum `worktree` | none | [§11](#11-workspace-isolation) |
| `maxTurns` | positive integer | unlimited | [§8.1](#81-maxturns) |
| `mcpServers` | list (names or inline defs) | inherits | [§9.2](#92-mcpservers) |
| `memory` | enum `user`/`project`/`local` | none | [§9.3](#93-memory) |
| `model` | alias / full ID / `inherit` | `inherit` | [§7.1](#71-model) |
| `name` | lowercase letters and hyphens | — (required) | [§4.1](#41-name) |
| `permissionMode` | enum (6 values) | inherits | [§6.3](#63-permissionmode--how-to-handle-prompts) |
| `skills` | list of skill names | none | [§9.1](#91-skills) |
| `tools` | comma string or list | inherits all | [§6.1](#61-tools--allowlist) |

## B. Fields that look real but do not exist

These names appear frequently in community examples and are easy to assume exist. They do not. The right name is in the third column.

| Mistaken name | What it tries to do | Correct field |
|---|---|---|
| `allowedTools` | Allowlist | `tools` |
| `permission` | Permission mode | `permissionMode` |
| `env` | Per-subagent env vars | (no equivalent — use hooks or wrapper scripts) |
| `context` | Inject context | `skills`, `memory`, or body |
| `systemPromptAppend` | Append to system prompt | (no equivalent — the body **is** the system prompt) |
| `priority` | Routing priority | (no equivalent — tune `description` text) |
| `matchingHints` | Routing hints | (no equivalent — tune `description` text) |

There are no priority knobs for auto-delegation. The only routing signal is `description` text.

## C. Maximalist worked example

A subagent that exercises most of the documented frontmatter fields. Real production subagents typically use four to six fields; this is for illustration only.

```markdown
---
name: db-reader
description: Execute read-only database queries. Use proactively when analyzing data or generating reports.
tools: Read, Grep, Glob, Bash
disallowedTools: Write, Edit
model: sonnet
permissionMode: acceptEdits
maxTurns: 25
effort: medium
background: false
isolation: worktree
color: cyan
memory: project
skills:
  - sql-style-guide
  - bigquery-cost-tips
mcpServers:
  - github
  - playwright:
      type: stdio
      command: npx
      args: ["-y", "@playwright/mcp@latest"]
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/validate-readonly-query.sh"
initialPrompt: "Greet the user and ask which dataset to inspect."
---

You are a database analyst with read-only access. Execute SELECT queries to
answer questions about the data.

When invoked:
1. Identify the dataset and tables relevant to the request.
2. Inspect schema before querying.
3. Run the smallest query that answers the question.

Output for each query:
- The query you ran.
- The result.
- One sentence explaining what it shows.

You cannot modify data. If asked to INSERT, UPDATE, DELETE, or modify schema,
explain that you only have read access.
```

## D. Version-dependent behavior

- **v2.1.63** — `Task` renamed to `Agent`; `Task(...)` aliases still work.
- **v2.1.117** — forked subagents (`/fork`) introduced.
- **v2.1.142** — `WaitForMcpServers` introduced.
- **v2.1.153** — managed MCP restrictions cover subagent-frontmatter servers; `/model` saves the choice as the default.
- **v2.1.161** — `/fork` enabled by default; spawning forks instead of general-purpose still requires `CLAUDE_CODE_FORK_SUBAGENT=1`.
