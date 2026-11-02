"""
Hook system for agent orchestration.

Provides an extensible mechanism for running pre/post hooks around agent.run().
"""

from .base import Hook, HookContext
from .executor import HookExecutor

__all__ = [
    'Hook',
    'HookContext',
    'HookExecutor',
    'ContextCompactionHook',
    'ContentFilterRecoveryHook',
    'ProposalChecklistHook',
    'ResearchReportFilesHook',
]


def __getattr__(name):
    if name == "ContextCompactionHook":
        from .context_compaction import ContextCompactionHook

        return ContextCompactionHook
    if name == "ProposalChecklistHook":
        from .proposal_checklist_hook import ProposalChecklistHook

        return ProposalChecklistHook
    if name == "ContentFilterRecoveryHook":
        from .content_filter_recovery import ContentFilterRecoveryHook

        return ContentFilterRecoveryHook
    if name == "ResearchReportFilesHook":
        from .research_report_files import ResearchReportFilesHook

        return ResearchReportFilesHook
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
