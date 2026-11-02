from dataclasses import dataclass
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
INTERNAL_CONTEXT_SOURCE_DIR = REPO_ROOT / "internal_context"


@dataclass(frozen=True)
class RuntimePaths:
    repo_root: Path
    task_root: Path
    shared_workspace: Path
    agent_workspace: Path
    runtime_root: Path
    data_dir: Path
    log_dir: Path
    status_file: Path
    heartbeat_file: Path

    @classmethod
    def for_task(
        cls,
        *,
        task_root: str,
        agent_id: int,
        external_user_id: Optional[str] = None,
        external_task_id: Optional[str] = None,
        log_root: Optional[str] = None,
        log_dir: Optional[str] = None,
    ) -> "RuntimePaths":
        task_root_path = Path(task_root).expanduser().resolve()
        runtime_root = task_root_path / ".runtime"
        shared_workspace = task_root_path
        agent_workspace = shared_workspace / f"agent_{agent_id:02d}"

        if log_dir:
            resolved_log_dir = Path(log_dir).expanduser().resolve()
        elif log_root:
            log_root_path = Path(log_root).expanduser().resolve()
            if external_user_id and external_task_id:
                resolved_log_dir = log_root_path / str(external_user_id) / str(external_task_id)
            elif external_task_id:
                resolved_log_dir = log_root_path / str(external_task_id)
            else:
                resolved_log_dir = log_root_path / f"agent_{agent_id:02d}"
        else:
            resolved_log_dir = runtime_root / "logs" / f"agent_{agent_id:02d}"

        return cls(
            repo_root=REPO_ROOT,
            task_root=task_root_path,
            shared_workspace=shared_workspace,
            agent_workspace=agent_workspace,
            runtime_root=runtime_root,
            data_dir=runtime_root / "data_storage" / f"agent_{agent_id:02d}",
            log_dir=resolved_log_dir,
            status_file=runtime_root / "status.json",
            heartbeat_file=runtime_root / "heartbeat.json",
        )

    def ensure_directories(self, *, create_task_root: bool = True) -> None:
        if create_task_root:
            self.task_root.mkdir(parents=True, exist_ok=True)
        elif not self.task_root.is_dir():
            raise FileNotFoundError(
                f"Task root does not exist or is not a directory: {self.task_root}"
            )
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.agent_workspace.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        for subdir in ("memories", "research_proposal_drafts", "research_topics"):
            (self.agent_workspace / subdir).mkdir(parents=True, exist_ok=True)
