"""
Summarize Paper Tool Handler - Single-shot paper summarization.

The configured LLM (deepseek-v4-pro, 1M context) is given the full paper
content inline in a single user message; the model's text response is the
summary markdown. No agent loop, no tool calls, no message-service plumbing.
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from filelock import FileLock

from .base_tool_handler import BaseToolHandler, ToolFailedError, ToolState
from utils.token_usage import aggregate_token_usages, build_raw_token_usage_records

logger = logging.getLogger("agent.tool.summarize_paper")


class SummarizePaperToolHandler(BaseToolHandler):
    """Tool handler that summarizes a scraped paper via one LLM call."""

    def __init__(self, tool_state: ToolState):
        super().__init__(tool_state=tool_state)
        # Cache: canonical source key -> summary path (built lazily per workspace).
        self._summary_index: Optional[Dict[str, str]] = None
        self._summary_index_root: Optional[str] = None

    @property
    def name(self) -> str:
        return "SummarizePaper"

    @property
    def display_result_type(self) -> str:
        return "summarize_paper_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "paper_folder": {
                    "type": "string",
                    "description": (
                        "Path to the paper folder created by the Scraper tool. "
                        "Relative paths are resolved against the workspace working directory."
                    ),
                }
            },
            "required": ["paper_folder"],
            "additionalProperties": False
        }

    @staticmethod
    def _build_text_display_result(message: str) -> Dict[str, Any]:
        return {
            "content": [
                {"type": "text", "text": message}
            ]
        }

    # ------------------------------------------------------------------
    # Helpers: canonical key / source URL / summary index
    # ------------------------------------------------------------------

    @staticmethod
    def _canonical_key_from_url(url: str) -> str:
        url = (url or "").strip()
        if not url:
            return "unknown:"
        m = re.search(r"arxiv\.org/(abs|pdf|html)/(?P<id>\d{4}\.\d{4,5})(v\d+)?", url, re.IGNORECASE)
        if m:
            return f"arxiv:{m.group('id')}"
        m = re.search(r"ar5iv\.labs\.arxiv\.org/html/(?P<id>\d{4}\.\d{4,5})(v\d+)?", url, re.IGNORECASE)
        if m:
            return f"arxiv:{m.group('id')}"
        try:
            from urllib.parse import urlparse, urlunparse
            p = urlparse(url)
            return f"url:{urlunparse(('', (p.netloc or '').lower(), (p.path or '').rstrip('/'), '', '', '')).lower()}"
        except Exception:
            return f"raw:{url}"

    @staticmethod
    def _title_based_filename(folder: str, fallback: str) -> str:
        """Build a human-readable filename slug from paper_info.json title.

        Strips any leading '[arxiv_id] ' prefix, sanitizes to filesystem-safe
        characters, collapses runs of separators, and truncates to ~100 chars.
        Falls back to the scraper-assigned folder slug when no title is available.
        """
        info_path = os.path.join(folder, "paper_info.json")
        title = ""
        if os.path.exists(info_path):
            try:
                with open(info_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                title = (data.get("title") or "").strip()
            except (OSError, json.JSONDecodeError):
                title = ""
        if not title or "untitled" in title.lower():
            return fallback
        # Strip leading "[arxiv_id] " prefix common in paper_info.
        title = re.sub(r"^\[[^\]]+\]\s*", "", title)
        # Sanitize: alnum + dash/underscore/dot kept; everything else -> "_".
        slug = re.sub(r"[^\w\-.]+", "_", title).strip("_")
        # Collapse repeated underscores.
        slug = re.sub(r"_+", "_", slug)
        # Hard cap at 100 chars (filesystem limit safety + readability).
        if len(slug) > 100:
            slug = slug[:100].rstrip("_")
        return slug or fallback

    @staticmethod
    def _read_meta_source_url(folder: str) -> Optional[str]:
        """Best-effort source URL lookup across new and old scraper layouts."""
        info_path = os.path.join(folder, "paper_info.json")
        if os.path.exists(info_path):
            try:
                with open(info_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                url = data.get("url") or data.get("source") or ""
                if isinstance(url, str) and url.strip():
                    return url.strip()
            except (OSError, json.JSONDecodeError):
                pass

        meta_path = os.path.join(folder, "meta", "meta_info.txt")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.lower().startswith("url:"):
                            return line.split(":", 1)[1].strip()
            except OSError:
                return None
        return None

    @classmethod
    def _build_summary_index(cls, root: str) -> Dict[str, str]:
        index: Dict[str, str] = {}
        if not os.path.isdir(root):
            return index
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                if not fn.lower().endswith(".md"):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        head = f.read(4000)
                except OSError:
                    continue
                m = re.search(r"\*\*Source\*\*:\s*(\S+)", head)
                if not m:
                    m = re.search(r"^-\s*\\*\\*Source\\*\\*:\s*(\S+)", head, re.MULTILINE)
                if not m:
                    continue
                src_url = m.group(1).strip()
                index[cls._canonical_key_from_url(src_url)] = path
        return index

    # ------------------------------------------------------------------
    # Paper content loading
    # ------------------------------------------------------------------

    # /scrape_v1 (grobid) emits a sections/<paper_title>.md that's the whole
    # paper concatenated, in addition to per-heading section files. Skip it to
    # avoid feeding the same content twice (it can be 40-50% of total chars).
    # Also drop appendix-style sections — summaries empirically only cite them
    # as pointers, not as substance.
    _APPENDIX_RE = re.compile(
        r"(?i)(?:^|[\s\-_.])(reference|bibliograph|appendix|^[A-D]\b|^[A-D][.\s]|"
        r"ethic|broader[\s_]impact|acknowledg|declaration|safeguard|license)",
    )
    # Note: "Limitations" intentionally KEPT — useful for ΔK demand assessment
    # (a paper's own acknowledged constraints signal where future work is needed).

    @staticmethod
    def _normalize_title(s: str) -> str:
        """Lower-case alnum-only for fuzzy matching paper title vs filename."""
        return re.sub(r"[^a-z0-9]+", "", s.lower())

    @classmethod
    def _collect_paper_content(cls, paper_folder: str) -> str:
        """Concatenate metadata + every relevant section .md.

        Filtering rules (applied to sections only, not metadata):
          - skip the grobid 'whole paper duplicate' (section name fuzzy-matches
            the paper_info.json title)
          - skip appendix / references / ethics / limitations / acks etc.
        """
        parts: List[str] = []

        paper_title_norm = ""
        for rel in ("paper_info.json", "meta/meta_info.txt", "meta/toc.txt"):
            path = os.path.join(paper_folder, rel)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                parts.append(f"=== {rel} ===\n{content}")
                if rel == "paper_info.json" and not paper_title_norm:
                    try:
                        data = json.loads(content)
                        title = (data.get("title") or "").strip()
                        # Strip leading "[arxiv_id] " prefix common in paper_info.
                        title_core = re.sub(r"^\[[^\]]+\]\s*", "", title)
                        paper_title_norm = cls._normalize_title(title_core)
                    except json.JSONDecodeError:
                        pass
            except OSError as e:
                parts.append(f"=== {rel} ===\n[read error: {e}]")

        sections_dir = os.path.join(paper_folder, "sections")
        if os.path.isdir(sections_dir):
            section_files = sorted(
                fn for fn in os.listdir(sections_dir) if fn.lower().endswith(".md")
            )
            skipped: List[str] = []
            for fn in section_files:
                stem = fn[:-3]  # strip .md
                stem_norm = cls._normalize_title(stem)
                # Skip the whole-paper duplicate: section name == paper title.
                # Use prefix match in either direction since grobid may
                # truncate the section filename slightly.
                if paper_title_norm and len(paper_title_norm) >= 20 and (
                    paper_title_norm.startswith(stem_norm[:40])
                    or stem_norm.startswith(paper_title_norm[:40])
                ):
                    skipped.append(f"{fn} (title-duplicate)")
                    continue
                if cls._APPENDIX_RE.search(stem):
                    skipped.append(f"{fn} (appendix-style)")
                    continue
                path = os.path.join(sections_dir, fn)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        parts.append(f"=== sections/{fn} ===\n{f.read()}")
                except OSError as e:
                    parts.append(f"=== sections/{fn} ===\n[read error: {e}]")
            if skipped:
                logger.info(
                    f"[SummarizePaper] dropped {len(skipped)} section(s) from "
                    f"{os.path.basename(paper_folder)}: {skipped[:6]}"
                    + (" ..." if len(skipped) > 6 else "")
                )

        return "\n\n".join(parts).strip()

    @staticmethod
    def _extract_text(content: Any) -> str:
        if not isinstance(content, list):
            return ""
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text") or ""
                if t:
                    parts.append(t)
        return "\n".join(parts).strip()

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """Strip a leading/trailing ```...``` fence the model may have added."""
        stripped = text.strip()
        if not stripped.startswith("```"):
            return stripped
        first_nl = stripped.find("\n")
        if first_nl == -1:
            return stripped
        body = stripped[first_nl + 1:].rstrip()
        if body.endswith("```"):
            body = body[:-3].rstrip()
        return body

    # ------------------------------------------------------------------
    # Main entrypoint
    # ------------------------------------------------------------------

    async def execute_async(self, **kwargs) -> Dict[str, Any]:
        working_dir = self.tool_state.get_working_dir()

        def _rel(path: str) -> str:
            try:
                return os.path.relpath(path, working_dir)
            except ValueError:
                return path

        paper_folder_arg = kwargs.get("paper_folder", "")
        if os.path.isabs(paper_folder_arg):
            paper_folder = paper_folder_arg
        else:
            paper_folder = os.path.join(working_dir, paper_folder_arg)
        paper_folder_rel = _rel(paper_folder)

        if not os.path.exists(paper_folder):
            raise ToolFailedError(f"Paper folder does not exist: {paper_folder_rel}")
        if not os.path.isdir(paper_folder):
            raise ToolFailedError(f"Path is not a directory: {paper_folder_rel}")

        sections_dir = os.path.join(paper_folder, "sections")
        if not os.path.exists(sections_dir):
            raise ToolFailedError(
                f"Paper folder missing 'sections' directory: {paper_folder_rel}"
            )

        paper_name = os.path.basename(paper_folder)
        shared_workspace = self.tool_state.get_shared_workspace()
        summaries_root = os.path.join(shared_workspace, "paper_summaries")
        os.makedirs(summaries_root, exist_ok=True)
        # Prefer a title-based filename so the agent's `ls paper_summaries`
        # is readable. Fall back to the slug when paper_info.json has no title.
        summary_basename = self._title_based_filename(paper_folder, paper_name)
        summary_output_path = os.path.join(summaries_root, f"{summary_basename}.md")

        # Local imports to avoid circulars at module load time.
        from agents.config import get_config
        from agents.llm.client_factory import create_llm_client
        from agents.utils.prompt_utils import load_prompt_template

        meta_url = self._read_meta_source_url(paper_folder)
        canonical_key = (
            self._canonical_key_from_url(meta_url) if meta_url else f"folder:{paper_name}"
        )
        safe_lock_name = re.sub(r"[^\w\-.]", "_", canonical_key)
        lock_dir = os.path.join(summaries_root, ".locks")
        os.makedirs(lock_dir, exist_ok=True)
        lock_path = os.path.join(lock_dir, f"{safe_lock_name}.lock")

        # Per-paper lock: ensures each paper is summarized once across all agents.
        with FileLock(lock_path, timeout=600.0):
            if os.path.exists(summary_output_path):
                return {
                    "tool_success": True,
                    "result": {
                        "status": "already_exists",
                        "summary_path": _rel(summary_output_path),
                        "paper_title": paper_name,
                    },
                    "display_result": self._build_text_display_result(
                        "Summary already exists."
                    ),
                }

            if meta_url:
                self._summary_index = self._build_summary_index(summaries_root)
                self._summary_index_root = summaries_root
                existing = (self._summary_index or {}).get(canonical_key)
                if existing and os.path.exists(existing):
                    return {
                        "tool_success": True,
                        "result": {
                            "status": "already_exists",
                            "summary_path": _rel(existing),
                            "paper_title": paper_name,
                        },
                        "display_result": self._build_text_display_result(
                            "Summary already exists."
                        ),
                    }

            config = get_config()
            agent_config = config.paper_summarization_agent
            agent_model_name = agent_config.model

            system_prompt = load_prompt_template(
                "sys_paper_summarization_agent_identity.md"
            ).format(paper_folder=paper_folder_rel)

            paper_content = self._collect_paper_content(paper_folder)
            if not paper_content:
                raise ToolFailedError(
                    f"No readable content found in paper folder: {paper_folder_rel}"
                )

            user_message = (
                "Below is the full content of the paper. Produce the structured "
                "markdown summary following the template in your system prompt. "
                "Output ONLY the markdown summary - no preamble, no code fences.\n\n"
                f"<paper>\n{paper_content}\n</paper>"
            )

            llm = create_llm_client("paper_summarization_agent")

            logger.info(
                f"[SummarizePaper] Calling {agent_model_name} for {paper_name} "
                f"({len(paper_content)} chars of paper content)"
            )

            try:
                response = await llm.call_llm(
                    messages=[{"role": "user", "content": user_message}],
                    system_prompt=system_prompt,
                    tools=None,
                    enable_thinking=agent_config.enable_thinking,
                )
            except Exception as e:
                logger.error(f"[{paper_name}] LLM call failed: {e}")
                return {
                    "tool_success": False,
                    "result": {
                        "status": "error",
                        "paper_title": paper_name,
                        "error": str(e),
                    },
                    "display_result": {
                        "status": "error",
                        "paper_title": paper_name,
                        "error": str(e),
                    },
                }

            actual_model_name = response.get("_actual_model_alias") or agent_model_name
            summary_text = self._extract_text(response.get("content"))
            if not summary_text:
                return {
                    "tool_success": False,
                    "result": {
                        "status": "error",
                        "paper_title": paper_name,
                        "error": "Model returned no text content",
                    },
                    "display_result": {
                        "status": "error",
                        "paper_title": paper_name,
                        "error": "Model returned no text content",
                    },
                }

            summary_text = self._strip_code_fence(summary_text).strip()

            try:
                with open(summary_output_path, "w", encoding="utf-8") as f:
                    f.write(summary_text + "\n")
            except OSError as e:
                raise ToolFailedError(f"Failed to write summary: {e}") from e

            # Token accounting (single call) — same shape the lead agent expects.
            turn_token_usage = aggregate_token_usages(response.get("token_usages"))
            sub_agent_token_infos: List[Dict[str, Any]] = []
            if any(turn_token_usage.values()):
                sub_agent_token_infos.append({
                    "model": actual_model_name,
                    "agent_name": "paper_summarization_agent",
                    "token_usage": {k: v for k, v in turn_token_usage.items() if v},
                })
            sub_agent_raw_token_usages = build_raw_token_usage_records(
                role="sub_agent",
                model_name=actual_model_name,
                raw_token_usages=response.get("raw_token_usages"),
                agent_name="paper_summarization_agent",
            )

            result: Dict[str, Any] = {
                "tool_success": True,
                "result": {
                    "status": "success",
                    "summary_path": _rel(summary_output_path),
                    "paper_title": paper_name,
                },
                "display_result": self._build_text_display_result(
                    "Summary generated successfully."
                ),
            }
            if sub_agent_token_infos:
                result["sub_agent_token_infos"] = sub_agent_token_infos
            if sub_agent_raw_token_usages:
                result["sub_agent_raw_token_usages"] = sub_agent_raw_token_usages
            return result
