import logging
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .. import BASE_DIR
from ..search.universe import resolve_universe_details
from ..utilities import clean_latex, trim_author
from .timing import PerformanceTimer, TimingEvent, timing_messages


logger = logging.getLogger(__name__)

SEMANTIC_PALETTE = [
    "#0d6efd",
    "#198754",
    "#ffc107",
    "#dc3545",
    "#0dcaf0",
    "#6610f2",
    "#fd7e14",
    "#20c997",
]

SEMANTIC_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "model",
    "analysis",
    "using",
    "based",
    "from",
    "study",
    "results",
    "data",
    "approach",
    "system",
    "research",
    "paper",
    "new",
    "proposed",
}

_MODEL_CACHE = {
    "transformer": None,
}
_MODEL_LOCK = threading.Lock()
_TRANSFORMER_WARMUP = {
    "started": False,
    "finished": False,
}
_UMAP_WARMUP = {
    "started": False,
    "finished": False,
}

SEMANTIC_MODEL_NAME = "all-MiniLM-L6-v2"


def get_semantic_model_cache_dir() -> Path:
    """Return the shared app-level cache directory for semantic models."""
    cache_dir = BASE_DIR / "models" / "sentence-transformers"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _configure_semantic_model_environment(cache_dir: Path) -> None:
    """Keep model downloads/cache and progress output predictable for web use."""
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(cache_dir))
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _cached_model_files_exist(cache_dir: Path) -> bool:
    model_dir_name = f"models--sentence-transformers--{SEMANTIC_MODEL_NAME}"
    model_dir = cache_dir / model_dir_name
    if not model_dir.exists():
        return False
    return any(model_dir.rglob("*.safetensors")) or any(model_dir.rglob("pytorch_model.bin"))


def get_transformer_model():
    """Lazy-load the transformer model used by semantic analysis."""
    if _MODEL_CACHE["transformer"] is None:
        with _MODEL_LOCK:
            if _MODEL_CACHE["transformer"] is None:
                cache_dir = get_semantic_model_cache_dir()
                _configure_semantic_model_environment(cache_dir)
                logger.info(
                    "Loading SentenceTransformer model %r from cache %s",
                    SEMANTIC_MODEL_NAME,
                    cache_dir,
                )
                from sentence_transformers import SentenceTransformer

                try:
                    from huggingface_hub.utils import disable_progress_bars

                    disable_progress_bars()
                except Exception:
                    pass

                try_local_only = _cached_model_files_exist(cache_dir)
                try:
                    _MODEL_CACHE["transformer"] = SentenceTransformer(
                        SEMANTIC_MODEL_NAME,
                        cache_folder=str(cache_dir),
                        local_files_only=try_local_only,
                    )
                except Exception:
                    if try_local_only:
                        logger.warning(
                            "Cached semantic model could not be loaded locally; retrying with Hub fallback.",
                            exc_info=True,
                        )
                        _MODEL_CACHE["transformer"] = SentenceTransformer(
                            SEMANTIC_MODEL_NAME,
                            cache_folder=str(cache_dir),
                            local_files_only=False,
                        )
                    else:
                        raise
    return _MODEL_CACHE["transformer"]


def warm_transformer_model(background: bool = True) -> None:
    """Warm the transformer model and first encode path once per process."""

    def run_warmup():
        try:
            model = get_transformer_model()
            model.encode(["semantic warmup"], batch_size=1, show_progress_bar=False)
            _TRANSFORMER_WARMUP["finished"] = True
            logger.info("Semantic transformer warmup complete.")
        except Exception:
            logger.warning("Semantic transformer warmup failed.", exc_info=True)

    if _TRANSFORMER_WARMUP["started"]:
        return
    _TRANSFORMER_WARMUP["started"] = True

    if background:
        thread = threading.Thread(target=run_warmup, name="semantic-transformer-warmup", daemon=True)
        thread.start()
    else:
        run_warmup()


def warm_semantic_projection(background: bool = True) -> None:
    """Warm UMAP/Numba projection code without blocking regular requests."""

    def run_warmup():
        try:
            import umap

            rng = np.random.default_rng(42)
            embeddings = rng.normal(size=(12, 8))
            reducer = umap.UMAP(
                n_neighbors=5,
                min_dist=0.1,
                n_components=2,
                metric="cosine",
                n_jobs=-1,
            )
            reducer.fit_transform(embeddings)
            _UMAP_WARMUP["finished"] = True
            logger.info("Semantic UMAP warmup complete.")
        except Exception:
            logger.warning("Semantic UMAP warmup failed.", exc_info=True)

    if _UMAP_WARMUP["started"]:
        return
    _UMAP_WARMUP["started"] = True

    if background:
        thread = threading.Thread(target=run_warmup, name="semantic-umap-warmup", daemon=True)
        thread.start()
    else:
        run_warmup()


def _source_embedding_index(idx_df: pd.DataFrame, source_type: str) -> tuple[pd.DataFrame, set[str]]:
    if idx_df.empty:
        source_idx = pd.DataFrame(columns=["hash", "source", "embedding"])
    else:
        source_idx = idx_df[idx_df.source == source_type].copy()
    cached_hashes = set(source_idx["hash"].dropna().astype(str)) if "hash" in source_idx else set()
    return source_idx, cached_hashes


def _prepare_embedding_texts(lib, rows, source_type: str) -> tuple[list[str], list[str]]:
    hashes = []
    texts = []
    for row in rows:
        h = str(row.hash)
        if source_type in ["text", "text-4k"]:
            from ..document import Document

            doc = Document(lib.abspath(row.path))
            doc.hash = h
            txt_p = doc.text_path(lib.text_dir_path, lib.config.extractor)
            window = 4000 if source_type == "text-4k" else 2000
            text = txt_p.read_text(encoding="utf-8", errors="ignore")[:window]
        else:
            text = f"{row.title}. {row.author}."
        hashes.append(h)
        texts.append(text)
    return hashes, texts


def _as_embedding_list(embeddings) -> list:
    if hasattr(embeddings, "tolist"):
        return embeddings.tolist()
    return list(embeddings)


@dataclass
class SemanticResult:
    result_df: pd.DataFrame
    relevant_idx: pd.DataFrame
    cluster_labels: np.ndarray
    coords: np.ndarray
    omitted_hashes: list[str] = field(default_factory=list)
    embedded_count: int = 0
    cached_embedding_count: int = 0
    embedding_index_count: int = 0
    source_type: str = "title"
    model_name: str = SEMANTIC_MODEL_NAME
    model_cache_dir: str = ""
    n_neighbors: int = 0
    min_cluster_size: int = 0
    cluster_summary: list[dict] = field(default_factory=list)
    cluster_themes: dict[int, str] = field(default_factory=dict)
    timings: list[TimingEvent] = field(default_factory=list)
    embedding_work_count: int = 0
    rg_command: str = ""
    rg_cache_hit: bool = False

    def to_cytoscape_json(self, *, verbosity: str = "minimal") -> dict:
        payload_timer = PerformanceTimer()
        elements = []
        current_year = datetime.now().year

        for i, (_, row_idx) in enumerate(self.relevant_idx.iterrows()):
            matches = self.result_df[self.result_df.hash.astype(str) == row_idx.hash]
            if matches.empty:
                continue
            meta = matches.iloc[0]

            cid = int(self.cluster_labels[i])
            try:
                year = int(str(meta.year).split(".")[0])
            except Exception:
                year = 2000

            opacity = max(0.4, 1.0 - (max(0, current_year - year) / 30.0))
            c_num = ""
            if cid >= 0:
                for cs in self.cluster_summary:
                    if cs["id"] == cid:
                        c_num = cs["number"]
                        break

            elements.append(
                {
                    "data": {
                        "id": f"paper-{row_idx.hash}",
                        "tag": str(meta.tag),
                        "title": clean_latex(str(meta.title)),
                        "authors": trim_author(str(meta.author)),
                        "year": year,
                        "cluster_id": cid,
                        "cluster_number": c_num,
                        "cluster_name": self.cluster_themes.get(cid, "Lone Star"),
                        "color": SEMANTIC_PALETTE[cid % len(SEMANTIC_PALETTE)] if cid >= 0 else "#adb5bd",
                        "opacity": opacity,
                    },
                    "position": {"x": float(self.coords[i][0] * 120), "y": float(self.coords[i][1] * 120)},
                }
            )

        status = (
            f'<i class="bi bi-stars me-2"></i> Galaxy mapped: '
            f"{len(elements)} papers, {len(self.cluster_summary)} clusters."
        )
        if verbosity == "verbose":
            status += (
                f" Source: {self.source_type}. Matched: {len(self.result_df)}. "
                f"Rendered: {len(elements)}. Omitted: {len(self.omitted_hashes)}. "
                f"Embeddings: {self.cached_embedding_count} cached, {self.embedded_count} new."
            )

        payload = {
            "elements": elements,
            "papers": len(self.result_df),
            "omitted_count": len(self.omitted_hashes),
            "omitted_reason": "No text extracts found for these papers.",
            "hashes": "|".join(self.result_df["hash"].dropna().astype(str).str[:8].unique()[:500]),
            "status_msg": status,
            "clusters": self.cluster_summary,
            "embedded_count": self.embedded_count,
            "cached_embedding_count": self.cached_embedding_count,
            "embedding_work_count": self.embedding_work_count,
            "embedding_work_pending": self.embedding_work_count > 0,
        }
        if verbosity == "verbose":
            messages = [
                f"Semantic source: {self.source_type}",
                f"Model: {self.model_name}",
                f"Model cache: {self.model_cache_dir}",
                f"Matched papers after query: {len(self.result_df)}",
                f"Embedding index rows for source: {self.embedding_index_count}",
                f"Embeddings used: {len(self.relevant_idx)} ({self.cached_embedding_count} cached, {self.embedded_count} new)",
                f"Embedding work: {self.embedding_work_count} missing {self.source_type} embeddings",
                f"Omitted papers without usable text: {len(self.omitted_hashes)}",
                f"Projection input points: {len(elements)}; UMAP neighbors: {self.n_neighbors}",
                f"Cluster pass complete: {len(self.cluster_summary)} clusters; min cluster size: {self.min_cluster_size}",
            ]
            if self.rg_command:
                messages.insert(3, f"Ripgrep command: {self.rg_command}")
                messages.insert(4, f"Ripgrep cache: {'hit' if self.rg_cache_hit else 'miss'}")
            payload_timer.mark("semantic payload serialization")
            messages.extend(timing_messages(self.timings + payload_timer.events))
            payload["log_messages"] = messages
        return payload

    def to_export_dataframe(self) -> pd.DataFrame:
        result_df = self.result_df.copy()
        hash_to_cluster = {
            str(row.hash): int(self.cluster_labels[i])
            for i, (_, row) in enumerate(self.relevant_idx.iterrows())
        }
        result_df["constellation"] = result_df["hash"].astype(str).map(hash_to_cluster).fillna(-1).astype(int)
        result_df["constellation"] = result_df["constellation"].apply(
            lambda x: f"Constellation {x}" if x >= 0 else "Lone Star"
        )

        cols = ["constellation", "tag", "author", "title", "year", "publisher", "journal", "hash"]
        existing_cols = [c for c in cols if c in result_df.columns]
        remaining = [c for c in result_df.columns if c not in existing_cols]
        return result_df[existing_cols + remaining]


def analyze_semantic(
    lib,
    raw_query: str,
    source_type: str = "title",
    *,
    case_sensitive: bool = False,
) -> SemanticResult:
    timer = PerformanceTimer()
    df = lib.database
    timer.mark("database load")
    model_cache_dir = str(get_semantic_model_cache_dir())
    timer.mark("model cache directory")
    universe_result = resolve_universe_details(lib, raw_query, case_sensitive=case_sensitive)
    universe_hashes = universe_result.hashes
    timer.mark("universe resolution")
    result_df = df[df["hash"].astype(str).isin(universe_hashes)].copy()
    timer.mark("database filtering")
    if result_df.empty:
        timer.mark("total semantic analysis")
        return SemanticResult(
            result_df=result_df,
            relevant_idx=pd.DataFrame(columns=["hash", "source", "embedding"]),
            cluster_labels=np.array([]),
            coords=np.empty((0, 2)),
            source_type=source_type,
            model_cache_dir=model_cache_dir,
            timings=timer.events,
            rg_command=universe_result.rg_command,
            rg_cache_hit=universe_result.rg_cache_hit,
        )

    idx_path = lib.config_path / "semantic-embeddings.feather"
    idx_df = (
        pd.read_feather(idx_path)
        if idx_path.exists()
        else pd.DataFrame(columns=["hash", "source", "embedding"])
    )
    source_idx, cached_hashes = _source_embedding_index(idx_df, source_type)
    source_idx_count = len(source_idx)
    timer.mark("embedding cache load")

    to_embed = []
    omitted_hashes = []

    for _, row in result_df.iterrows():
        h = str(row.hash)
        if h not in cached_hashes:
            if source_type in ["text", "text-4k"]:
                from ..document import Document

                doc = Document(lib.abspath(row.path))
                doc.hash = h
                txt_p = doc.text_path(lib.text_dir_path, lib.config.extractor)
                if not txt_p.exists():
                    omitted_hashes.append(h)
                    continue
            to_embed.append(row)
    timer.mark("embedding cache lookup")

    result_df = result_df[~result_df.hash.astype(str).isin(omitted_hashes)]
    if result_df.empty:
        timer.mark("total semantic analysis")
        return SemanticResult(
            result_df=result_df,
            relevant_idx=pd.DataFrame(columns=["hash", "source", "embedding"]),
            cluster_labels=np.array([]),
            coords=np.empty((0, 2)),
            omitted_hashes=omitted_hashes,
            source_type=source_type,
            model_cache_dir=model_cache_dir,
            timings=timer.events,
            rg_command=universe_result.rg_command,
            rg_cache_hit=universe_result.rg_cache_hit,
        )

    if to_embed:
        model = get_transformer_model()
        timer.mark("transformer model load")
        embed_hashes, embed_texts = _prepare_embedding_texts(lib, to_embed, source_type)
        timer.mark("missing embedding text preparation")
        embeddings = model.encode(embed_texts, batch_size=32, show_progress_bar=False)
        embedding_values = _as_embedding_list(embeddings)
        new_rows = [
            {"hash": h, "source": source_type, "embedding": emb}
            for h, emb in zip(embed_hashes, embedding_values)
        ]
        timer.mark("missing embedding generation")

        idx_df = pd.concat([idx_df, pd.DataFrame(new_rows)]).drop_duplicates(
            ["hash", "source"], keep="last"
        )
        idx_df.reset_index(drop=True).to_feather(idx_path)
        source_idx, cached_hashes = _source_embedding_index(idx_df, source_type)
        source_idx_count = len(source_idx)
        timer.mark("embedding cache write")

    result_hashes = set(result_df.hash.astype(str))
    relevant_idx = source_idx[source_idx.hash.astype(str).isin(result_hashes)]
    cached_embedding_count = max(0, len(relevant_idx) - len(to_embed))
    timer.mark("relevant embedding selection")
    if relevant_idx.empty:
        timer.mark("total semantic analysis")
        return SemanticResult(
            result_df=result_df,
            relevant_idx=relevant_idx,
            cluster_labels=np.array([]),
            coords=np.empty((0, 2)),
            omitted_hashes=omitted_hashes,
            embedded_count=len(to_embed),
            cached_embedding_count=cached_embedding_count,
            embedding_index_count=source_idx_count,
            source_type=source_type,
            model_cache_dir=model_cache_dir,
            timings=timer.events,
            embedding_work_count=len(to_embed),
            rg_command=universe_result.rg_command,
            rg_cache_hit=universe_result.rg_cache_hit,
        )

    import hdbscan
    import umap

    embeddings = np.array(relevant_idx["embedding"].tolist())
    n_pts = len(embeddings)

    n_neighbors = min(n_pts - 1, 15)
    if n_neighbors < 2:
        coords = np.random.rand(n_pts, 2) * 200
    else:
        reducer = umap.UMAP(
            n_neighbors=n_neighbors,
            min_dist=0.1,
            n_components=2,
            metric="cosine",
            n_jobs=-1,
        )
        coords = reducer.fit_transform(embeddings)
    timer.mark("UMAP projection")

    min_cluster = max(2, min(n_pts, 5))
    if n_pts < min_cluster:
        cluster_labels = np.zeros(n_pts)
    else:
        try:
            clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster)
            cluster_labels = clusterer.fit_predict(coords)
        except Exception:
            cluster_labels = np.zeros(n_pts)
    timer.mark("HDBSCAN clustering")

    cluster_themes = {}
    unique_cids = sorted([cid for cid in set(cluster_labels) if cid >= 0])
    for cid in unique_cids:
        indices = [i for i, label in enumerate(cluster_labels) if label == cid]
        cluster_hashes = [relevant_idx.iloc[i].hash for i in indices]
        cluster_titles = result_df[result_df.hash.astype(str).isin(cluster_hashes)].title.dropna().tolist()

        words = []
        for title in cluster_titles:
            tokens = re.findall(r"\b[a-z]{4,}\b", title.lower())
            words.extend([w for w in tokens if w not in SEMANTIC_STOP_WORDS])

        top_words = [w for w, _ in Counter(words).most_common(3)]
        cluster_themes[cid] = ", ".join(top_words).title() if top_words else f"Constellation {cid}"

    cluster_summary = []
    for idx, cid in enumerate(unique_cids):
        indices = [i for i, label in enumerate(cluster_labels) if label == cid]
        cluster_hashes = [relevant_idx.iloc[i].hash for i in indices]
        sample_titles = result_df[result_df.hash.astype(str).isin(cluster_hashes)].title.head(3).tolist()
        cluster_summary.append(
            {
                "id": int(cid),
                "number": idx + 1,
                "name": cluster_themes[cid],
                "count": len(indices),
                "samples": [clean_latex(t) for t in sample_titles],
                "color": SEMANTIC_PALETTE[int(cid) % len(SEMANTIC_PALETTE)],
            }
        )
    timer.mark("cluster summary generation")
    timer.mark("total semantic analysis")

    return SemanticResult(
        result_df=result_df,
        relevant_idx=relevant_idx,
        cluster_labels=cluster_labels,
        coords=coords,
        omitted_hashes=omitted_hashes,
        embedded_count=len(to_embed),
        cached_embedding_count=cached_embedding_count,
        embedding_index_count=source_idx_count,
        source_type=source_type,
        model_cache_dir=model_cache_dir,
        n_neighbors=n_neighbors,
        min_cluster_size=min_cluster,
        cluster_summary=cluster_summary,
        cluster_themes=cluster_themes,
        timings=timer.events,
        embedding_work_count=len(to_embed),
        rg_command=universe_result.rg_command,
        rg_cache_hit=universe_result.rg_cache_hit,
    )
