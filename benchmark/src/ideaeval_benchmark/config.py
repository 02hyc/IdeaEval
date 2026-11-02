"""Configuration loading: YAML file + .env for secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_path(p: str, root: Path = PROJECT_ROOT) -> Path:
    """Resolve a path: absolute paths returned as-is, relative resolved against root."""
    path = Path(p)
    if path.is_absolute():
        return path
    return root / path


@dataclass(frozen=True)
class S2Settings:
    api_keys: list[str] = field(default_factory=list)
    rps: float = 1.0
    search_verify_threshold: float = 0.90


@dataclass(frozen=True)
class ArxivSettings:
    timeout_seconds: float = 120.0
    user_agent: str = "ideaeval-benchmark/1.0"


@dataclass(frozen=True)
class SomarkSettings:
    api_url: str = ""  # set somark.api_url in config.yaml
    auth_user: str = ""
    auth_password: str = ""
    extract_config: dict = field(default_factory=lambda: {
        "image_understanding": False,
        "enable_title_level_recognition": True,
    })

    @property
    def auth(self) -> Optional[tuple[str, str]]:
        if self.auth_user:
            return (self.auth_user, self.auth_password)
        return None


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "openai"
    model: str = "deepseek-v4-pro"
    api_key: str = ""
    base_url: str = ""
    max_retries: int = 3
    max_tokens: int = 2048
    temperature: float = 0.1


@dataclass(frozen=True)
class PipelineSettings:
    workers: int = 4
    cache_dir: str = "cache"
    max_table_chars: int = 16000


@dataclass(frozen=True)
class ClusteringSettings:
    embedding_model: str = "specter2"
    umap_n_components: int = 10
    umap_n_neighbors: int = 15
    umap_min_dist: float = 0.0
    hdbscan_min_cluster_size: int = 3
    hdbscan_min_samples: int = 2
    bertopic_nr_topics: int = 80
    random_seed: int = 42


@dataclass(frozen=True)
class PathSettings:
    data_dir: str = "data"
    markdown_dir: str = "data/markdown"
    metadata_file: str = "data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl"
    experiments_dir: str = "experiments"
    datasets_dir: str = "datasets"
    cache_dir: str = "cache"
    tmp_pdf_dir: str = "cache/tmp_pdf"
    errors_file: str = "logs/batch_pdf2md_errors.jsonl"

    def resolve(self, field_name: str, root: Path = PROJECT_ROOT) -> Path:
        """Resolve a path field to an absolute Path."""
        return resolve_path(getattr(self, field_name), root)


@dataclass(frozen=True)
class Settings:
    s2: S2Settings = field(default_factory=S2Settings)
    arxiv: ArxivSettings = field(default_factory=ArxivSettings)
    somark: SomarkSettings = field(default_factory=SomarkSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    pipeline: PipelineSettings = field(default_factory=PipelineSettings)
    clustering: ClusteringSettings = field(default_factory=ClusteringSettings)
    paths: PathSettings = field(default_factory=PathSettings)


def load_settings(
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
) -> Settings:
    """Load settings from YAML config file, overlaid with environment variables."""
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()

    raw: dict = {}
    if config_path and config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    s2_raw = raw.get("s2", {})
    s2_keys_env = os.environ.get("S2_API_KEYS", "")
    s2_keys = [k.strip() for k in s2_keys_env.split(",") if k.strip()] if s2_keys_env else s2_raw.get("api_keys", [])

    s2 = S2Settings(
        api_keys=s2_keys,
        rps=s2_raw.get("rps", 1.0),
        search_verify_threshold=s2_raw.get("search_verify_threshold", 0.90),
    )

    arxiv_raw = raw.get("arxiv", {})
    arxiv = ArxivSettings(
        timeout_seconds=arxiv_raw.get("timeout_seconds", 120.0),
        user_agent=arxiv_raw.get("user_agent", "ideaeval-benchmark/1.0"),
    )

    somark_raw = raw.get("somark", {})
    somark = SomarkSettings(
        api_url=somark_raw.get("api_url", ""),
        auth_user=os.environ.get("SOMARK_AUTH_USER", somark_raw.get("auth_user", "")),
        auth_password=os.environ.get("SOMARK_AUTH_PASSWORD", somark_raw.get("auth_password", "")),
        extract_config=somark_raw.get("extract_config", {
            "image_understanding": False,
            "enable_title_level_recognition": True,
        }),
    )

    llm_raw = raw.get("llm", {})
    llm = LLMSettings(
        provider=llm_raw.get("provider", "openai"),
        model=llm_raw.get("model", "deepseek-v4-pro"),
        api_key=os.environ.get("OPENAI_API_KEY", llm_raw.get("api_key", "")),
        base_url=os.environ.get("OPENAI_BASE_URL", llm_raw.get("base_url", "")),
        max_retries=llm_raw.get("max_retries", 3),
        max_tokens=llm_raw.get("max_tokens", 2048),
        temperature=llm_raw.get("temperature", 0.1),
    )

    pipe_raw = raw.get("pipeline", {})
    pipeline = PipelineSettings(
        workers=pipe_raw.get("workers", 4),
        cache_dir=pipe_raw.get("cache_dir", "cache"),
        max_table_chars=pipe_raw.get("max_table_chars", 16000),
    )

    cl_raw = raw.get("clustering", {})
    clustering = ClusteringSettings(
        embedding_model=cl_raw.get("embedding_model", "specter2"),
        umap_n_components=cl_raw.get("umap_n_components", 10),
        umap_n_neighbors=cl_raw.get("umap_n_neighbors", 15),
        umap_min_dist=cl_raw.get("umap_min_dist", 0.0),
        hdbscan_min_cluster_size=cl_raw.get("hdbscan_min_cluster_size", 3),
        hdbscan_min_samples=cl_raw.get("hdbscan_min_samples", 2),
        bertopic_nr_topics=cl_raw.get("bertopic_nr_topics", 80),
        random_seed=cl_raw.get("random_seed", 42),
    )

    paths_raw = raw.get("paths", {})
    paths = PathSettings(
        data_dir=os.environ.get("DATA_DIR", paths_raw.get("data_dir", "data")),
        markdown_dir=os.environ.get("MARKDOWN_DIR", paths_raw.get("markdown_dir", "data/markdown")),
        metadata_file=os.environ.get("METADATA_FILE", paths_raw.get("metadata_file", "data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl")),
        experiments_dir=paths_raw.get("experiments_dir", "experiments"),
        datasets_dir=paths_raw.get("datasets_dir", "datasets"),
        cache_dir=paths_raw.get("cache_dir", "cache"),
        tmp_pdf_dir=paths_raw.get("tmp_pdf_dir", "cache/tmp_pdf"),
        errors_file=paths_raw.get("errors_file", "logs/batch_pdf2md_errors.jsonl"),
    )

    return Settings(s2=s2, arxiv=arxiv, somark=somark, llm=llm, pipeline=pipeline, clustering=clustering, paths=paths)
