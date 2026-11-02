"""Rank Runner: orchestrates the three-phase ranking pipeline.

Phase 0: A literature review agent builds a comprehensive topic-level survey (shared).
Phase 1: Two research agents run in parallel, each evaluating one proposal against the shared review.
Phase 2: A judge agent compares both research reports and produces a ranking.

Messages are persisted via LocalMessageService with atomic writes, UUID tracking,
and full context compaction support (LLM-based summarization when context grows large).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import re

from agents.agenthub.research_agent import ResearchAgent
from agents.agenthub.judge_agent import JudgeAgent
from agents.agenthub.literature_review_agent import LiteratureReviewAgent
from agents.config import get_config
from agents.llm.client_factory import create_llm_client
from agents.llm.token_counter import TokenCounter
from agents.utils.exceptions import ContentPolicyViolationError
from hooks import ContentFilterRecoveryHook, ContextCompactionHook, HookContext, HookExecutor, ResearchReportFilesHook
from hooks.literature_review_files import LiteratureReviewFilesHook
from message_service.local_message_service import LocalMessageService
from message_service.message import TaskMessage, MessageType
from toolkit.toolkit import Toolkit
from utils.logger import Logger

logger = Logger()


@dataclass(frozen=True)
class RankRunConfig:
    proposal_a: str
    proposal_b: str
    cutoff_date: str
    topic_label: str = ""
    topic_description: str = ""
    max_tool_calls_per_turn: int = 8
    judge_max_tool_calls_per_turn: int = 5
    task_root: str = "./workspace"
    # Metadata for visualization (optional)
    meta: dict = None


def _setup_workspace(task_root: str) -> dict[str, Path]:
    """Create structured workspace directories and return path map."""
    root = Path(task_root).resolve()
    dirs = {
        "root": root,
        "literature_review": root / "agents" / "literature_review",
        "literature_review_workspace": root / "agents" / "literature_review" / "workspace",
        "research_a": root / "agents" / "research_a",
        "research_b": root / "agents" / "research_b",
        "judge": root / "agents" / "judge",
        "research_a_workspace": root / "agents" / "research_a" / "workspace",
        "research_b_workspace": root / "agents" / "research_b" / "workspace",
        "judge_workspace": root / "agents" / "judge" / "workspace",
        "reports": root / "reports",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


@dataclass
class RankResult:
    exit_code: int
    ranking: Optional[dict] = None
    report_a: str = ""
    report_b: str = ""
    error: Optional[str] = None


def _load_proposal(proposal: str) -> str:
    """Load proposal text from file path or return inline text."""
    if os.path.isfile(proposal):
        return Path(proposal).read_text(encoding="utf-8")
    return proposal


def _load_research_workspace_report(workspace_dir: Path, fallback_text: str) -> str:
    """Load the judge-facing research report, falling back to final chat text.

    `revision_feedback.md` is generator-facing and must not be fed to the judge.
    The report returned here is therefore only `analysis_report.md`.
    """
    analysis_path = workspace_dir / "analysis_report.md"
    if analysis_path.is_file():
        return analysis_path.read_text(encoding="utf-8").strip()
    return fallback_text


def _copy_literature_workspace_for_judge(literature_workspace: Path | None, judge_workspace: Path) -> None:
    """Expose literature-review local evidence inside a judge workspace.

    The judge should not search the web, but it can inspect the papers that the
    literature agent already fetched. Keep this scoped to evidence artifacts and
    lightweight planning files rather than copying runtime internals. Large
    paper archives are linked rather than copied so high-concurrency judge-only
    runs do not duplicate many GB of scraper outputs.
    """
    if not literature_workspace or not literature_workspace.is_dir():
        return

    (judge_workspace / "literature_archive_source.txt").write_text(
        str(literature_workspace.resolve()) + "\n",
        encoding="utf-8",
    )

    for subdir in ("paper_summaries", "scraped_papers"):
        src = literature_workspace / subdir
        dst = judge_workspace / subdir
        if src.is_dir() and not dst.exists():
            try:
                os.symlink(src.resolve(), dst, target_is_directory=True)
            except OSError:
                shutil.copytree(src, dst)

    for filename in ("key_findings.md", "coverage_gaps.md", "research_plan.md"):
        src = literature_workspace / filename
        dst = judge_workspace / filename
        if src.is_file() and not dst.exists():
            try:
                os.symlink(src.resolve(), dst)
            except OSError:
                shutil.copy2(src, dst)


def _extract_text_from_response(response: dict[str, Any]) -> str:
    """Extract text content from LLM response."""
    parts = []
    for block in response.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


def _extract_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tool_use blocks from LLM response."""
    tool_calls = []
    for block in response.get("content", []):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_calls.append(block)
    return tool_calls


def _has_tool_use(response: dict[str, Any]) -> bool:
    """Check if response contains any tool_use blocks."""
    for block in response.get("content", []):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return True
    return False


def _serialize_response_for_messages(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Serialize LLM response content blocks for message history.

    Strips thinking blocks and extra fields (e.g. Bedrock's 'caller') that
    cause validation errors when sent back.
    """
    blocks = []
    for block in response.get("content", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            blocks.append({"type": "text", "text": block.get("text", "")})
        elif block.get("type") == "tool_use":
            blocks.append({
                "type": "tool_use",
                "id": block["id"],
                "name": block["name"],
                "input": block.get("input", {}),
            })
    return blocks


@dataclass
class ToolLoopTrace:
    """Records the trace of a tool loop execution."""
    rounds: list[dict[str, Any]] = field(default_factory=list)
    total_tool_calls: int = 0
    final_output: str = ""


async def _run_tool_loop(
    agent,
    toolkit: Toolkit,
    max_tool_calls_per_turn: int,
    message_service: LocalMessageService,
    task_id: str,
    agent_name: str,
    system_prompt: str,
    tools: list[dict[str, Any]] | None = None,
    max_context_tokens: int = 150000,
    enable_thinking: bool = False,
    hook_executor: HookExecutor | None = None,
    trace: ToolLoopTrace | None = None,
    agent_dir: Path | None = None,
) -> str:
    """Core tool-use loop for both research agents and judge.

    The agent runs until it produces a text response with no tool calls.
    Hooks (e.g. ResearchReportFilesHook) can reject the text response and
    inject a reminder, causing the loop to continue.

    Messages are persisted via LocalMessageService with atomic writes.
    ContextCompactionHook (via hook_executor) handles LLM-based summarization
    when context grows too large.

    Returns the final text output from the agent.
    """
    if trace is None:
        trace = ToolLoopTrace()

    # URL allowlist: WebFetch can only access URLs seen in prior search results
    seen_urls: set[str] = set()

    async def _prepare_llm_messages() -> list[dict[str, Any]]:
        """Fetch in-context messages, run pre-hooks, and convert for the LLM."""
        nonlocal hook_context
        in_context_messages = await message_service.fetch_in_context_messages(task_id, agent_name)
        hook_context = None
        if hook_executor and in_context_messages:
            hook_context = HookContext(
                task_id=task_id,
                agent_name=agent_name,
                messages=in_context_messages,
                system_prompt=system_prompt,
                tools=tools or [],
                max_context_tokens=max_context_tokens,
                enable_thinking=enable_thinking,
            )
            hook_context = await hook_executor.run_pre_hooks(hook_context)
            in_context_messages = hook_context.messages
        return LocalMessageService.get_llm_compatible_messages(in_context_messages)

    hook_context: HookContext | None = None

    async def _call_llm_with_content_filter_recovery(use_tools: bool) -> dict[str, Any]:
        """Call the agent, retrying with sanitized guidance if output is filtered."""
        max_recoveries = int(os.getenv("RANKER_CONTENT_FILTER_RECOVERY_ATTEMPTS", "3"))
        for recovery_idx in range(max_recoveries + 1):
            llm_messages = await _prepare_llm_messages()
            try:
                return await agent.call_llm(llm_messages, use_tools=use_tools)
            except ContentPolicyViolationError as exc:
                if recovery_idx >= max_recoveries or not hook_executor or hook_context is None:
                    raise
                handled = await hook_executor.run_content_filter_hooks(hook_context, exc)
                if not handled:
                    raise
                logger.warning(
                    f"[{agent_name}] Content-filter recovery retry "
                    f"{recovery_idx + 1}/{max_recoveries}"
                )
        raise RuntimeError("unreachable content-filter recovery state")

    def _track_urls(text: str):
        for url in re.findall(r'https?://[^\s\)\]>\"\']+', text):
            url = url.rstrip('.,;:')
            seen_urls.add(url)
            m = re.search(r'arxiv\.org/abs/(\d{4}\.\d{4,5})', url)
            if m:
                aid = m.group(1)
                seen_urls.add(f"https://arxiv.org/abs/{aid}")
                seen_urls.add(f"https://arxiv.org/pdf/{aid}")

    def _url_allowed(url: str) -> bool:
        url = url.strip()
        if url in seen_urls:
            return True
        m = re.search(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})', url)
        if m:
            aid = m.group(1)
            if f"https://arxiv.org/abs/{aid}" in seen_urls or f"https://arxiv.org/pdf/{aid}" in seen_urls:
                return True
        return any(url.rstrip('/') == s.rstrip('/') for s in seen_urls)

    async def _dispatch(tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "WebFetch":
            url = tool_input.get("url", "").strip()
            if not _url_allowed(url):
                return (
                    f"(error: URL '{url}' was not found in any prior search results. "
                    "WebFetch can only access URLs that appeared in WebSearch results. "
                    "You cannot construct URLs from background knowledge.)"
                )

        # Hard prohibition for the literature-review agent: paper_summaries/ and
        # scraped_papers/ are populated exclusively by the Scraper tool. Refuse
        # any Write/Edit targeting these directories so the agent cannot
        # fabricate paper summaries from training-data memory when Scraper
        # fails or a paper cannot be retrieved.
        if agent_name == "literature_review_agent" and tool_name in ("Write", "Edit"):
            fp = str(tool_input.get("file_path", ""))
            fp_norm = fp.replace("\\", "/")
            if "paper_summaries/" in fp_norm or "scraped_papers/" in fp_norm:
                return (
                    f"(error: refusing Write/Edit on '{fp}'. paper_summaries/ "
                    "and scraped_papers/ are populated exclusively by the "
                    "Scraper tool. To add a paper summary, call Scraper on "
                    "the paper's URL — do NOT write a summary from your own "
                    "knowledge. If Scraper repeatedly fails on this URL, cite "
                    "the URL directly in the narrative and move on.)"
                )

        handler = toolkit.tool_handlers.get(tool_name)
        if handler is None:
            return f"(error: unknown tool '{tool_name}')"

        try:
            result = await handler.execute(tool_input)
            result_text = result.get("result", "") if result.get("success") else result.get("result", f"(tool '{tool_name}' failed)")
            if tool_name in ("WebSearch", "Scraper"):
                _track_urls(str(result_text))
            return result_text if isinstance(result_text, str) else json.dumps(result_text, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[_dispatch] Error in tool '{tool_name}': {e}")
            return f"(tool '{tool_name}' error: {e})"

    # Store the initial user message
    user_prompt_text = agent.user_prompt if hasattr(agent, 'user_prompt') else ""
    initial_msg = TaskMessage(
        task_id=task_id,
        agent_name=agent_name,
        role="user",
        content=[{"type": "text", "text": user_prompt_text}],
        msg_type=MessageType.TEXT,
    )
    await message_service.on_receive_user_message(
        task_id=task_id,
        message=initial_msg,
        agent_name=agent_name,
    )

    tool_call_counter = [0]
    _round = 0

    while True:
        _round += 1

        response = await _call_llm_with_content_filter_recovery(use_tools=True)

        if not _has_tool_use(response):
            text = _extract_text_from_response(response)
            if text:
                # Persist the final text response
                await message_service.on_receive_agent_response(
                    task_id=task_id,
                    response=response,
                    agent_name=agent_name,
                )
                if hook_executor and hook_context is not None:
                    should_continue = await hook_executor.run_text_response_hooks(hook_context, response)
                    if should_continue:
                        continue
                trace.final_output = text
                trace.total_tool_calls = tool_call_counter[0]
                _export_messages_json(message_service, task_id, agent_name, agent_dir)
                return text
            # Empty text with no tool calls — check if there was thinking content.
            # If so, the model used its output on thinking but didn't produce an action;
            # give it another turn rather than stopping.
            has_thinking = any(
                isinstance(block, dict) and block.get("type") == "thinking"
                for block in response.get("content", [])
            )
            if has_thinking:
                logger.warning(
                    f"[{agent_name}] Empty text response with thinking content — retrying"
                )
                continue
            break

        tool_calls = _extract_tool_calls(response)

        # Persist the assistant response (containing the tool_use blocks) BEFORE
        # dispatching the tools. Tools may have visible side effects (files,
        # network calls); if execution or a downstream persist step crashes
        # after a tool ran, the assistant message must still reflect what the
        # LLM asked for, otherwise the trace looks like "scraped_papers exists
        # but no Scraper call was recorded".
        assistant_text = _extract_text_from_response(response)
        round_trace = {
            "round": _round,
            "assistant_text": assistant_text[:1000] if assistant_text else "",
            "tool_calls": [],
        }
        trace.rounds.append(round_trace)

        await message_service.on_receive_agent_response(
            task_id=task_id,
            response=response,
            agent_name=agent_name,
        )

        # Per-turn cap: only the first N calls run; the rest become "dropped"
        # tool_results so the model has a slot for every tool_use it issued.
        kept: list[dict[str, Any]] = []
        dropped_ids: set[str] = set()
        for idx, tc in enumerate(tool_calls):
            if max_tool_calls_per_turn and idx >= max_tool_calls_per_turn:
                dropped_ids.add(tc["id"])
            else:
                kept.append(tc)

        # Read-only / non-mutating tools are dispatched in parallel; everything
        # else runs serially to preserve workspace-mutation ordering for Write/Edit/etc.
        PARALLEL_SAFE_TOOLS = {"SummarizePaper", "Scraper", "WebSearch", "WebFetch"}
        parallel_calls = [tc for tc in kept if tc["name"] in PARALLEL_SAFE_TOOLS]
        serial_calls = [tc for tc in kept if tc["name"] not in PARALLEL_SAFE_TOOLS]

        # Dedupe parallel calls by (name, input). Reasoning models with parallel
        # tool calls occasionally emit the same tool_use twice in one turn; both
        # would otherwise contend on per-paper FileLocks and the duplicate eats
        # a 600 s timeout. Each duplicate id shares the primary's result.
        unique_parallel: list[dict[str, Any]] = []
        primary_for_input: dict[tuple[str, str], str] = {}
        duplicate_to_primary: dict[str, str] = {}
        for tc in parallel_calls:
            dedup_key = (tc["name"], json.dumps(tc.get("input", {}), sort_keys=True))
            primary_id = primary_for_input.get(dedup_key)
            if primary_id is not None:
                duplicate_to_primary[tc["id"]] = primary_id
            else:
                primary_for_input[dedup_key] = tc["id"]
                unique_parallel.append(tc)

        if duplicate_to_primary:
            logger.info(
                f"[ToolLoop] Collapsed {len(duplicate_to_primary)} duplicate "
                f"parallel tool_use call(s) into {len(unique_parallel)} unique "
                "dispatch(es)."
            )

        results_by_id: dict[str, str] = {}
        durations_by_id: dict[str, int] = {}

        async def _timed_dispatch(tc):
            t0 = time.monotonic()
            try:
                res = await _dispatch(tc["name"], tc.get("input", {}))
            finally:
                durations_by_id[tc["id"]] = int((time.monotonic() - t0) * 1000)
            return res

        if unique_parallel:
            par_results = await asyncio.gather(
                *(_timed_dispatch(tc) for tc in unique_parallel),
                return_exceptions=True,
            )
            for tc, res in zip(unique_parallel, par_results):
                if isinstance(res, BaseException):
                    res = f"(tool '{tc['name']}' error: {res})"
                results_by_id[tc["id"]] = res

        # Fan-out the primary result and duration to every duplicate id so the
        # model gets a tool_result for every tool_use it issued.
        for dup_id, primary_id in duplicate_to_primary.items():
            results_by_id[dup_id] = results_by_id[primary_id]
            if primary_id in durations_by_id:
                durations_by_id[dup_id] = durations_by_id[primary_id]

        for tc in serial_calls:
            results_by_id[tc["id"]] = await _timed_dispatch(tc)

        # Assemble tool_results and trace in original tool_call order.
        tool_results: list[dict[str, Any]] = []
        for tc in tool_calls:
            tc_trace = {
                "tool_name": tc["name"],
                "tool_input": tc.get("input", {}),
                "status": "executed",
                "result_preview": "",
            }
            if tc["id"] in dropped_ids:
                tc_trace["status"] = "dropped_per_turn_cap"
                tc_trace["result_preview"] = "(per-turn cap reached)"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": (
                        f"(tool_use dropped: per-turn cap of {max_tool_calls_per_turn} reached. "
                        "Reissue in next turn if needed.)"
                    ),
                })
            else:
                result_text = results_by_id[tc["id"]]
                tc_trace["result_preview"] = (
                    result_text[:500] if isinstance(result_text, str) else str(result_text)[:500]
                )
                duration_ms = durations_by_id.get(tc["id"])
                if duration_ms is not None:
                    tc_trace["duration_ms"] = duration_ms
                    if tc["id"] in duplicate_to_primary:
                        tc_trace["deduplicated"] = True
                result_block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_text,
                }
                if duration_ms is not None:
                    result_block["duration_ms"] = duration_ms
                if tc["id"] in duplicate_to_primary:
                    result_block["deduplicated"] = True
                tool_results.append(result_block)
                tool_call_counter[0] += 1
            round_trace["tool_calls"].append(tc_trace)

        # Persist tool results as user message
        tool_result_msg = TaskMessage(
            task_id=task_id,
            agent_name=agent_name,
            role="user",
            content=tool_results,
            msg_type=MessageType.TOOL_RESULT,
        )
        await message_service.on_receive_user_message(
            task_id=task_id,
            message=tool_result_msg,
            agent_name=agent_name,
        )

    # Loop exited via break (empty response with no thinking) — shouldn't normally happen
    trace.final_output = "(agent stopped without producing output)"
    trace.total_tool_calls = tool_call_counter[0]
    _export_messages_json(message_service, task_id, agent_name, agent_dir)
    return trace.final_output


def _export_messages_json(
    message_service: LocalMessageService,
    task_id: str,
    agent_name: str,
    agent_dir: Path | None,
) -> None:
    """Export messages as raw list to messages.json for trace viewer compatibility."""
    if agent_dir is None:
        return
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context; schedule a sync-compatible fetch
            # by reading the agent data file directly.
            path = message_service._get_agent_data_path(task_id, agent_name)
            if not os.path.exists(path):
                return
            with open(path, 'r', encoding='utf-8') as f:
                agent_data = json.load(f)
            raw_messages = []
            for msg in agent_data.get("messages", []):
                raw_messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", []),
                })
            out_path = agent_dir / "messages.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(raw_messages, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[rank_runner] Failed to export messages.json: {e}")


def _create_hook_executor(
    agent_config_name: str,
    message_service: LocalMessageService,
    workspace_dir: Path | None = None,
) -> HookExecutor:
    """Create a HookExecutor with ContextCompactionHook for a research/judge agent."""
    config = get_config()
    token_counter = TokenCounter(config.get_token_counter_config(agent_config_name))
    compaction_llm = create_llm_client("context_compression")

    hook_executor = HookExecutor()
    hook_executor.register(ContentFilterRecoveryHook(message_service))
    if agent_config_name == "research_agent" and workspace_dir is not None:
        hook_executor.register(ResearchReportFilesHook(message_service, workspace_dir))
    if agent_config_name == "literature_review_agent" and workspace_dir is not None:
        hook_executor.register(LiteratureReviewFilesHook(message_service, workspace_dir))
    hook_executor.register(
        ContextCompactionHook(
            config=config.context_compression,
            token_counter=token_counter,
            message_service=message_service,
            llm_client=compaction_llm,
        )
    )
    return hook_executor


async def _run_literature_review_agent(
    topic_label: str,
    topic_description: str,
    cutoff_date: str,
    config: RankRunConfig,
    agent_dir: Path,
) -> str:
    """Run the literature review agent. Returns the literature review text."""
    agent_label = "literature_review"

    sink_id = Logger.add_agent_log(agent_label, agent_dir)

    try:
        with logger.agent_scope(agent_label):
            workspace_dir = agent_dir / "workspace"
            workspace_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"[{agent_label}] Topic: {topic_label}")
            logger.info(f"[{agent_label}] Cutoff date: {cutoff_date}")

            # Copy only the literature-review skill into workspace
            _internal_context_src = Path(__file__).resolve().parent.parent / "internal_context"
            _skills_dst = workspace_dir / "internal_context" / "skills" / "literature-review"
            if _internal_context_src.is_dir() and not _skills_dst.exists():
                _skills_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(_internal_context_src / "skills" / "literature-review", _skills_dst)

            agent = LiteratureReviewAgent(
                topic_label=topic_label,
                topic_description=topic_description,
                cutoff_date=cutoff_date,
                workspace_dir=str(workspace_dir),
            )

            # Set up message persistence
            data_dir = agent_dir / ".runtime" / "data_storage"
            data_dir.mkdir(parents=True, exist_ok=True)
            message_service = LocalMessageService(data_dir=str(data_dir))
            task_id = await message_service.create_task(working_dir=str(workspace_dir))
            if not task_id:
                raise RuntimeError("Failed to create task for literature review agent")

            toolkit = Toolkit(
                task_id=task_id,
                working_dir=str(workspace_dir),
                agent_name=agent.agent_name,
                message_service=message_service,
                cutoff_date=cutoff_date,
            )

            hook_executor = _create_hook_executor("literature_review_agent", message_service, workspace_dir=workspace_dir)

            app_config = get_config()
            agent_cfg = app_config.get_agent_config("literature_review_agent")

            class _AgentProxy:
                def __init__(self, real_agent):
                    self._agent = real_agent
                    self.user_prompt = real_agent.user_prompt

                async def call_llm(self, messages, use_tools=True):
                    return await self._agent.call_llm(messages, use_tools=use_tools)

            proxy = _AgentProxy(agent)
            trace = ToolLoopTrace()

            await _run_tool_loop(
                agent=proxy,
                toolkit=toolkit,
                max_tool_calls_per_turn=config.max_tool_calls_per_turn,
                message_service=message_service,
                task_id=task_id,
                agent_name=agent.agent_name,
                system_prompt=agent.system_prompt,
                tools=agent.tools,
                max_context_tokens=agent_cfg.max_context_tokens,
                enable_thinking=agent_cfg.enable_thinking,
                hook_executor=hook_executor,
                trace=trace,
                agent_dir=agent_dir,
            )

            # Read the produced literature review
            lit_review_path = workspace_dir / "literature_review.md"
            if lit_review_path.is_file():
                return lit_review_path.read_text(encoding="utf-8")
            else:
                logger.warning(f"[{agent_label}] literature_review.md not found in workspace")
                return ""
    finally:
        Logger.remove_agent_log(sink_id)


async def _run_research_agent(
    role: str,
    proposal_text: str,
    cutoff_date: str,
    config: RankRunConfig,
    agent_dir: Path | None = None,
    literature_review: str = "",
    literature_review_workspace: Path | None = None,
) -> tuple[str, ToolLoopTrace]:
    """Run one research agent end-to-end. Returns (report, trace)."""
    agent_label = f"research_{role}"

    sink_id = None
    if agent_dir:
        sink_id = Logger.add_agent_log(agent_label, agent_dir)

    try:
        with logger.agent_scope(agent_label):
            workspace_dir = agent_dir / "workspace" if agent_dir else Path(config.task_root)
            workspace_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"[{agent_label}] Cutoff date: {cutoff_date}")

            # Copy internal_context into workspace, excluding literature-review skill
            _internal_context_src = Path(__file__).resolve().parent.parent / "internal_context"
            _internal_context_dst = workspace_dir / "internal_context"
            if _internal_context_src.is_dir() and not _internal_context_dst.exists():
                shutil.copytree(
                    _internal_context_src, _internal_context_dst,
                    ignore=shutil.ignore_patterns("literature-review"),
                )

            agent = ResearchAgent(role=role, cutoff_date=cutoff_date, workspace_dir=str(workspace_dir))

            # Write proposal to workspace so it survives context compaction
            proposal_path = workspace_dir / "proposal.md"
            proposal_path.write_text(proposal_text, encoding="utf-8")

            # Inject shared literature review and paper data from Phase 0
            if literature_review:
                (workspace_dir / "literature_review.md").write_text(literature_review, encoding="utf-8")
            if literature_review_workspace:
                for subdir in ("scraped_papers", "paper_summaries"):
                    src = literature_review_workspace / subdir
                    dst = workspace_dir / subdir
                    if src.is_dir() and not dst.exists():
                        shutil.copytree(src, dst)

            # Set up structured message persistence
            data_dir = agent_dir / ".runtime" / "data_storage" if agent_dir else Path(config.task_root) / ".runtime" / "data_storage"
            data_dir.mkdir(parents=True, exist_ok=True)
            message_service = LocalMessageService(data_dir=str(data_dir))
            task_id = await message_service.create_task(working_dir=str(workspace_dir))
            if not task_id:
                raise RuntimeError(f"Failed to create task for research agent {role}")

            toolkit = Toolkit(
                task_id=task_id,
                working_dir=str(workspace_dir),
                agent_name=agent.agent_name,
                message_service=message_service,
                cutoff_date=cutoff_date,
            )

            # Set up context compaction
            hook_executor = _create_hook_executor("research_agent", message_service, workspace_dir=workspace_dir)

            # Get agent config for context tokens
            app_config = get_config()
            agent_cfg = app_config.get_agent_config("research_agent")

            class _AgentProxy:
                def __init__(self, real_agent):
                    self._agent = real_agent
                    self.user_prompt = real_agent.user_prompt

                async def call_llm(self, messages, use_tools=True):
                    return await self._agent.call_llm(messages, use_tools=use_tools)

            proxy = _AgentProxy(agent)
            trace = ToolLoopTrace()

            report = await _run_tool_loop(
                agent=proxy,
                toolkit=toolkit,
                max_tool_calls_per_turn=config.max_tool_calls_per_turn,
                message_service=message_service,
                task_id=task_id,
                agent_name=agent.agent_name,
                system_prompt=agent.system_prompt,
                tools=agent.tools,
                max_context_tokens=agent_cfg.max_context_tokens,
                enable_thinking=agent_cfg.enable_thinking,
                hook_executor=hook_executor,
                trace=trace,
                agent_dir=agent_dir,
            )

            report = _load_research_workspace_report(workspace_dir, report)
            return report, trace
    finally:
        if sink_id is not None:
            Logger.remove_agent_log(sink_id)



async def _run_judge(
    proposal_a: str,
    proposal_b: str,
    report_a: str,
    report_b: str,
    cutoff_date: str,
    config: RankRunConfig,
    agent_dir: Path | None = None,
    literature_review: str | None = None,
    literature_review_workspace: Path | None = None,
) -> tuple[str, ToolLoopTrace]:
    """Run the judge agent end-to-end. Returns (output_text, trace)."""
    agent_label = "judge"

    sink_id = None
    if agent_dir:
        sink_id = Logger.add_agent_log(agent_label, agent_dir)

    try:
        with logger.agent_scope(agent_label):
            workspace_dir = agent_dir / "workspace" if agent_dir else Path(config.task_root)
            workspace_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"[{agent_label}] Cutoff date: {cutoff_date}")

            # Materialize literature review, proposals, and the local literature
            # archive into the judge workspace so the agent can inspect evidence
            # with View/Grep/Glob instead of receiving analysis reports inline.
            literature_text = literature_review or (
                "# Literature Review\n\n"
                "No literature review was provided to this judge run. Treat broad field "
                "context as unavailable and rely only on proposal evidence."
            )
            (workspace_dir / "literature_review.md").write_text(literature_text, encoding="utf-8")
            (workspace_dir / "proposal_a.md").write_text(proposal_a, encoding="utf-8")
            (workspace_dir / "proposal_b.md").write_text(proposal_b, encoding="utf-8")
            _copy_literature_workspace_for_judge(literature_review_workspace, workspace_dir)

            agent = JudgeAgent(cutoff_date=cutoff_date, workspace_dir=str(workspace_dir))

            user_prompt = agent.build_user_prompt(
                proposal_a,
                proposal_b,
                report_a,
                report_b,
                literature_text,
            )

            # Set up structured message persistence
            data_dir = agent_dir / ".runtime" / "data_storage" if agent_dir else Path(config.task_root) / ".runtime" / "data_storage"
            data_dir.mkdir(parents=True, exist_ok=True)
            message_service = LocalMessageService(data_dir=str(data_dir))
            task_id = await message_service.create_task(working_dir=str(workspace_dir))
            if not task_id:
                raise RuntimeError("Failed to create task for judge agent")

            toolkit = Toolkit(
                task_id=task_id,
                working_dir=str(workspace_dir),
                agent_name=agent.agent_name,
                message_service=message_service,
                cutoff_date=cutoff_date,
            )

            # Set up context compaction
            hook_executor = _create_hook_executor("judge_agent", message_service)

            # Get agent config for context tokens
            app_config = get_config()
            agent_cfg = app_config.get_agent_config("judge_agent")

            class _JudgeProxy:
                def __init__(self, real_agent, prompt):
                    self._agent = real_agent
                    self.user_prompt = prompt

                async def call_llm(self, messages, use_tools=True):
                    return await self._agent.call_llm(messages, use_tools=use_tools)

            proxy = _JudgeProxy(agent, user_prompt)
            trace = ToolLoopTrace()

            text = await _run_tool_loop(
                agent=proxy,
                toolkit=toolkit,
                max_tool_calls_per_turn=config.judge_max_tool_calls_per_turn,
                message_service=message_service,
                task_id=task_id,
                agent_name=agent.agent_name,
                system_prompt=agent.system_prompt,
                tools=agent.tools,
                max_context_tokens=agent_cfg.max_context_tokens,
                enable_thinking=agent_cfg.enable_thinking,
                hook_executor=hook_executor,
                trace=trace,
                agent_dir=agent_dir,
            )

            return text, trace
    finally:
        if sink_id is not None:
            Logger.remove_agent_log(sink_id)


def _parse_ranking_json(text: str) -> dict | None:
    """Try to parse the ranking JSON from judge output."""
    text = text.strip()
    # Remove markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return None


async def run_ranker_task(run_config: RankRunConfig) -> RankResult:
    """Main entry point: orchestrates the full ranking pipeline.

    NOTE: this function does NOT initialize the global logger. The process-level
    `Logger.setup()` must be called once by the entry point (rank_main.py or
    run_benchmark.py) before invoking this. Calling Logger.setup() per task
    here would race-condition with concurrent tasks (logger.remove() nukes all
    sinks globally, causing cleanup ValueErrors that mask real return values).
    """
    # Set up structured workspace
    workspace = _setup_workspace(run_config.task_root)

    # Derive a stable pair_id for log routing. Prefer the explicit meta value
    # so per-pair logs land in the same file across re-runs of the same pair.
    pair_id = None
    if run_config.meta and isinstance(run_config.meta, dict):
        pair_id = run_config.meta.get("pair_id") or run_config.meta.get("cluster_id")
    if not pair_id:
        pair_id = f"rank_{int(time.time() * 1000)}"

    pair_sink_id = Logger.add_pair_log(pair_id, workspace["root"])
    pair_scope_cm = logger.pair_scope(pair_id)
    pair_scope_cm.__enter__()
    try:
        logger.set_task_id(f"rank_{int(time.time())}")

        logger.info("=" * 60)
        logger.info("RANKER AGENT STARTED")
        logger.info(f"  Task root: {run_config.task_root}")
        logger.info(f"  Cutoff date: {run_config.cutoff_date}")
        logger.info("=" * 60)

        return await _run_ranker_pipeline(run_config, workspace)
    finally:
        pair_scope_cm.__exit__(None, None, None)
        Logger.remove_pair_log(pair_sink_id)


async def _run_ranker_pipeline(run_config: RankRunConfig, workspace: dict[str, Path]) -> RankResult:
    """Body of run_ranker_task split out so the pair_scope/sink lifecycle in the
    caller stays simple. All logging inside this function flows through the
    pair_id contextvar set by the caller.
    """

    # Load proposals
    proposal_a_text = _load_proposal(run_config.proposal_a)
    proposal_b_text = _load_proposal(run_config.proposal_b)

    logger.info(f"Proposal A length: {len(proposal_a_text)} chars")
    logger.info(f"Proposal B length: {len(proposal_b_text)} chars")

    # Phase 0: Literature Review (if topic_label is available)
    literature_review = ""
    lit_review_workspace = None
    if run_config.topic_label:
        logger.info("-" * 40)
        logger.info("PHASE 0: Literature Review Agent")
        logger.info(f"  Topic: {run_config.topic_label}")
        logger.info("-" * 40)

        literature_review = await _run_literature_review_agent(
            topic_label=run_config.topic_label,
            topic_description=run_config.topic_description,
            cutoff_date=run_config.cutoff_date,
            config=run_config,
            agent_dir=workspace["literature_review"],
        )
        lit_review_workspace = workspace["literature_review_workspace"]
        logger.info(f"Literature review length: {len(literature_review)} chars")
    else:
        logger.info("No topic_label provided — skipping Phase 0, research agents will build their own reviews")

    # Phase 1: Parallel research agents
    logger.info("-" * 40)
    logger.info("PHASE 1: Parallel Research Agents")
    logger.info("-" * 40)

    results = await asyncio.gather(
        _run_research_agent("a", proposal_a_text, run_config.cutoff_date, run_config, workspace["research_a"],
                           literature_review=literature_review, literature_review_workspace=lit_review_workspace),
        _run_research_agent("b", proposal_b_text, run_config.cutoff_date, run_config, workspace["research_b"],
                           literature_review=literature_review, literature_review_workspace=lit_review_workspace),
        return_exceptions=True,
    )

    result_a, result_b = results
    trace_a = ToolLoopTrace()
    trace_b = ToolLoopTrace()

    def _fallback_report_from_workspace(role: str, exc: BaseException) -> str:
        """If research_<role>'s tool loop raised AFTER it had already written
        analysis_report.md to the workspace, prefer that real judge-facing file
        over the synthetic 'RESEARCH FAILED' placeholder.

        The original failure mode (2026-05-24, pair_0144): research_a wrote
        the workspace deliverables, then its next LLM call hit an upstream
        content-policy 400 error; the exception propagated up but the files
        on disk are valid. The runner would otherwise discard the real
        judge-facing analysis and feed a 'RESEARCH FAILED' placeholder to the
        judge.
        """
        wsp = workspace[f"research_{role}_workspace"]
        recovered = _load_research_workspace_report(wsp, "")
        if recovered and "RESEARCH FAILED" not in recovered:
            logger.warning(
                f"Research agent {role.upper()} raised ({exc!r}) but its workspace "
                f"contains analysis_report.md "
                f"({len(recovered)} chars). Using the on-disk analysis instead "
                f"of the synthetic 'RESEARCH FAILED' placeholder."
            )
            return recovered
        return (
            f"## Analysis Report: Proposal {role.upper()}\n\n"
            f"**RESEARCH FAILED**: {exc}\n\n"
            "### Search Log\nCoverage: failed\n"
        )

    if isinstance(result_a, BaseException) and isinstance(result_b, BaseException):
        # Even in the double-failure case, try to recover from workspace before
        # short-circuiting. If both workspaces are usable, we can still judge.
        report_a_fallback = _fallback_report_from_workspace("a", result_a)
        report_b_fallback = _fallback_report_from_workspace("b", result_b)
        a_recovered = "RESEARCH FAILED" not in report_a_fallback
        b_recovered = "RESEARCH FAILED" not in report_b_fallback
        if a_recovered and b_recovered:
            logger.error(
                f"Both research agents raised exceptions (A={result_a!r}, B={result_b!r}) "
                "but both workspaces contain complete deliverables — proceeding with "
                "on-disk reports."
            )
            report_a, report_b = report_a_fallback, report_b_fallback
            # Skip the rest of the per-side branches by short-circuiting below
            # (the function continues with valid report_a/report_b).
        else:
            error_msg = f"Both research agents failed: A={result_a}, B={result_b}"
            logger.error(error_msg)
            return RankResult(exit_code=1, error=error_msg)
    else:
        if isinstance(result_a, BaseException):
            logger.error(f"Research agent A failed: {result_a}")
            report_a = _fallback_report_from_workspace("a", result_a)
        else:
            report_a, trace_a = result_a

        if isinstance(result_b, BaseException):
            logger.error(f"Research agent B failed: {result_b}")
            report_b = _fallback_report_from_workspace("b", result_b)
        else:
            report_b, trace_b = result_b

    logger.info(f"Analysis Report A length: {len(report_a)} chars")
    logger.info(f"Analysis Report B length: {len(report_b)} chars")

    # Save research reports to shared location
    reports_dir = workspace["reports"]
    (reports_dir / "report_a.md").write_text(report_a, encoding="utf-8")
    (reports_dir / "report_b.md").write_text(report_b, encoding="utf-8")
    logger.info(f"Research reports saved to {reports_dir}")

    # Phase 2: Judge
    logger.info("-" * 40)
    logger.info("PHASE 2: Judge Agent")
    logger.info("-" * 40)

    judge_output, trace_judge = await _run_judge(
        proposal_a=proposal_a_text,
        proposal_b=proposal_b_text,
        report_a=report_a,
        report_b=report_b,
        cutoff_date=run_config.cutoff_date,
        config=run_config,
        agent_dir=workspace["judge"],
        literature_review=literature_review,
        literature_review_workspace=lit_review_workspace,
    )

    logger.info(f"Judge output length: {len(judge_output)} chars")

    # Parse ranking result
    ranking = _parse_ranking_json(judge_output)
    if ranking is None:
        logger.warning("Failed to parse judge output as JSON, saving raw output")
        ranking = {"raw_output": judge_output}

    # Save final result
    result_path = Path(run_config.task_root) / "ranking_result.json"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(ranking, f, indent=2, ensure_ascii=False)
    logger.info(f"Ranking result saved to {result_path}")

    # Save trace data
    trace_data = {
        "research_agent_a": {
            "rounds": trace_a.rounds,
            "total_tool_calls": trace_a.total_tool_calls,
        },
        "research_agent_b": {
            "rounds": trace_b.rounds,
            "total_tool_calls": trace_b.total_tool_calls,
        },
        "judge": {
            "rounds": trace_judge.rounds,
            "total_tool_calls": trace_judge.total_tool_calls,
        },
    }
    trace_path = Path(run_config.task_root) / "trace.json"
    with open(trace_path, "w", encoding="utf-8") as f:
        json.dump(trace_data, f, indent=2, ensure_ascii=False)

    # Also save full output with reports
    full_result = {
        "meta": run_config.meta or {},
        "ranking": ranking,
        "report_a": report_a,
        "report_b": report_b,
        "trace": trace_data,
        "config": {
            "cutoff_date": run_config.cutoff_date,
        },
    }
    full_path = Path(run_config.task_root) / "full_ranking_result.json"
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(full_result, f, indent=2, ensure_ascii=False)

    logger.info("=" * 60)
    logger.info("RANKER AGENT COMPLETED")
    if "result" in ranking:
        logger.info(f"  Result: {ranking['result']}")
        logger.info(f"  Confidence: {ranking.get('confidence', 'N/A')}")
    logger.info("=" * 60)

    return RankResult(
        exit_code=0,
        ranking=ranking,
        report_a=report_a,
        report_b=report_b,
    )
