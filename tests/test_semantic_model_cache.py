from __future__ import annotations

import sys
import threading
import time
import types

import numpy as np
import pandas as pd


def test_transformer_model_uses_archivum_cache_folder(monkeypatch, tmp_path):
    from archivum.analytics import semantic

    calls = []

    class FakeSentenceTransformer:
        def __init__(self, model_name, **kwargs):
            calls.append((model_name, kwargs))

        def encode(self, text, show_progress_bar=False):
            return np.array([1.0, 2.0, 3.0])

    fake_module = types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(semantic, "BASE_DIR", tmp_path)
    monkeypatch.setitem(semantic._MODEL_CACHE, "transformer", None)

    cache_dir = semantic.get_semantic_model_cache_dir()
    model = semantic.get_transformer_model()

    assert isinstance(model, FakeSentenceTransformer)
    assert len(calls) == 1
    model_name, kwargs = calls[0]
    assert model_name == semantic.SEMANTIC_MODEL_NAME
    assert kwargs["cache_folder"] == str(cache_dir)
    assert kwargs["local_files_only"] is False


def test_transformer_model_is_reused_in_process(monkeypatch, tmp_path):
    from archivum.analytics import semantic

    calls = []

    class FakeSentenceTransformer:
        def __init__(self, model_name, **kwargs):
            calls.append((model_name, kwargs))

        def encode(self, text, show_progress_bar=False):
            return np.array([1.0, 2.0, 3.0])

    fake_module = types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(semantic, "BASE_DIR", tmp_path)
    monkeypatch.setitem(semantic._MODEL_CACHE, "transformer", None)

    first = semantic.get_transformer_model()
    second = semantic.get_transformer_model()

    assert first is second
    assert len(calls) == 1


def test_transformer_model_load_is_thread_safe(monkeypatch, tmp_path):
    from archivum.analytics import semantic

    calls = []

    class FakeSentenceTransformer:
        def __init__(self, model_name, **kwargs):
            time.sleep(0.02)
            calls.append((model_name, kwargs))

        def encode(self, text, **_kwargs):
            return np.array([[1.0, 2.0, 3.0]]) if isinstance(text, list) else np.array([1.0, 2.0, 3.0])

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer))
    monkeypatch.setattr(semantic, "BASE_DIR", tmp_path)
    monkeypatch.setitem(semantic._MODEL_CACHE, "transformer", None)

    models = []
    threads = [
        threading.Thread(target=lambda: models.append(semantic.get_transformer_model()))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert models[0] is models[1]


def test_verbose_semantic_payload_reports_model_cache(tmp_path):
    from archivum.analytics.semantic import SEMANTIC_MODEL_NAME, SemanticResult
    from archivum.analytics.timing import PerformanceTimer

    timer = PerformanceTimer()
    timer.mark("test phase")

    result = SemanticResult(
        result_df=pd.DataFrame(columns=["hash", "title"]),
        relevant_idx=pd.DataFrame(columns=["hash", "source", "embedding"]),
        cluster_labels=np.array([]),
        coords=np.empty((0, 2)),
        model_cache_dir=str(tmp_path),
        timings=timer.events,
        rg_command='rg --line-buffered --stats -C 1 --encoding utf-8 -n -H -i -g *.md "risk" .',
    )

    payload = result.to_cytoscape_json(verbosity="verbose")
    messages = payload["log_messages"]

    assert f"Model: {SEMANTIC_MODEL_NAME}" in messages
    assert f"Model cache: {tmp_path}" in messages
    assert 'Ripgrep command: rg --line-buffered --stats -C 1 --encoding utf-8 -n -H -i -g *.md "risk" .' in messages
    assert payload["embedded_count"] == 0
    assert payload["cached_embedding_count"] == 0
    assert payload["embedding_work_count"] == 0
    assert payload["embedding_work_pending"] is False
    assert any(message.startswith("Timing: test phase ") for message in messages)
    assert any(message.startswith("Timing: semantic payload serialization ") for message in messages)


def test_verbose_social_payload_reports_timings():
    from archivum.analytics.networks import SocialNetworkResult
    from archivum.analytics.timing import PerformanceTimer

    timer = PerformanceTimer()
    timer.mark("test social phase")
    result = SocialNetworkResult(
        result_df=pd.DataFrame([{"hash": "abc123"}]),
        nodes=[{"data": {"id": "Author", "label": "Author", "weight": 1, "papers": []}}],
        elements=[],
        timings=timer.events,
        rg_command='rg --line-buffered --stats -C 1 --encoding utf-8 -n -H -i -g *.md "risk" .',
    )

    payload = result.to_cytoscape_json(verbosity="verbose")
    messages = payload["log_messages"]

    assert 'Ripgrep command: rg --line-buffered --stats -C 1 --encoding utf-8 -n -H -i -g *.md "risk" .' in messages
    assert any(message.startswith("Timing: test social phase ") for message in messages)
    assert any(message.startswith("Timing: social payload serialization ") for message in messages)


def test_semantic_umap_uses_parallel_workers(monkeypatch, tmp_path):
    from archivum.analytics import semantic

    calls = []

    class FakeUMAP:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def fit_transform(self, embeddings):
            return np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])

    class FakeHDBSCAN:
        def __init__(self, **_kwargs):
            pass

        def fit_predict(self, _coords):
            return np.array([0, 0, 0])

    monkeypatch.setitem(sys.modules, "umap", types.SimpleNamespace(UMAP=FakeUMAP))
    monkeypatch.setitem(sys.modules, "hdbscan", types.SimpleNamespace(HDBSCAN=FakeHDBSCAN))
    monkeypatch.setattr(
        semantic,
        "resolve_universe_details",
        lambda _lib, _query, case_sensitive=False: types.SimpleNamespace(
            hashes={"h1", "h2", "h3"},
            rg_command="",
            rg_cache_hit=False,
        ),
    )

    idx_path = tmp_path / "semantic-embeddings.feather"
    pd.DataFrame(
        [
            {"hash": "h1", "source": "title", "embedding": [1.0, 0.0]},
            {"hash": "h2", "source": "title", "embedding": [0.0, 1.0]},
            {"hash": "h3", "source": "title", "embedding": [1.0, 1.0]},
        ]
    ).to_feather(idx_path)

    df = pd.DataFrame(
        [
            {"hash": "h1", "tag": "A", "title": "A", "author": "Author A", "year": 2020},
            {"hash": "h2", "tag": "B", "title": "B", "author": "Author B", "year": 2021},
            {"hash": "h3", "tag": "C", "title": "C", "author": "Author C", "year": 2022},
        ]
    )
    lib = types.SimpleNamespace(database=df, config_path=tmp_path)

    semantic.analyze_semantic(lib, "q anything", "title")

    assert calls
    assert calls[0]["n_jobs"] == -1
    assert "random_state" not in calls[0]


def test_semantic_embedding_lookup_counts_cached_and_new(monkeypatch, tmp_path):
    from archivum.analytics import semantic

    class FakeModel:
        def encode(self, texts, batch_size=32, show_progress_bar=False):
            assert isinstance(texts, list)
            assert batch_size == 32
            assert show_progress_bar is False
            return np.array([[0.0, 1.0] for _ in texts])

    class FakeUMAP:
        def __init__(self, **_kwargs):
            pass

        def fit_transform(self, embeddings):
            return np.array([[0.0, 0.0], [1.0, 1.0]])

    class FakeHDBSCAN:
        def __init__(self, **_kwargs):
            pass

        def fit_predict(self, _coords):
            return np.array([0, 0])

    monkeypatch.setattr(
        semantic,
        "resolve_universe_details",
        lambda _lib, _query, case_sensitive=False: types.SimpleNamespace(
            hashes={"h1", "h2"},
            rg_command="",
            rg_cache_hit=False,
        ),
    )
    monkeypatch.setattr(semantic, "get_transformer_model", lambda: FakeModel())
    monkeypatch.setitem(sys.modules, "umap", types.SimpleNamespace(UMAP=FakeUMAP))
    monkeypatch.setitem(sys.modules, "hdbscan", types.SimpleNamespace(HDBSCAN=FakeHDBSCAN))

    pd.DataFrame(
        [{"hash": "h1", "source": "title", "embedding": [1.0, 0.0]}]
    ).to_feather(tmp_path / "semantic-embeddings.feather")
    df = pd.DataFrame(
        [
            {"hash": "h1", "tag": "A", "title": "A", "author": "Author A", "year": 2020},
            {"hash": "h2", "tag": "B", "title": "B", "author": "Author B", "year": 2021},
        ]
    )
    lib = types.SimpleNamespace(database=df, config_path=tmp_path)

    result = semantic.analyze_semantic(lib, "q anything", "title")

    assert result.embedded_count == 1
    assert result.cached_embedding_count == 1
    assert set(result.relevant_idx.hash.astype(str)) == {"h1", "h2"}


def test_semantic_missing_embeddings_are_batch_encoded(monkeypatch, tmp_path):
    from archivum.analytics import semantic

    encode_calls = []

    class FakeModel:
        def encode(self, texts, batch_size=32, show_progress_bar=False):
            encode_calls.append((texts, batch_size, show_progress_bar))
            return np.array([[float(i), 1.0] for i, _ in enumerate(texts)])

    monkeypatch.setattr(
        semantic,
        "resolve_universe_details",
        lambda _lib, _query, case_sensitive=False: types.SimpleNamespace(
            hashes={"h1", "h2"},
            rg_command="",
            rg_cache_hit=False,
        ),
    )
    monkeypatch.setattr(semantic, "get_transformer_model", lambda: FakeModel())
    monkeypatch.setitem(sys.modules, "umap", types.SimpleNamespace(UMAP=object))
    monkeypatch.setitem(sys.modules, "hdbscan", types.SimpleNamespace(HDBSCAN=object))

    pd.DataFrame(columns=["hash", "source", "embedding"]).to_feather(tmp_path / "semantic-embeddings.feather")
    df = pd.DataFrame(
        [
            {"hash": "h1", "tag": "A", "title": "A", "author": "Author A", "year": 2020},
            {"hash": "h2", "tag": "B", "title": "B", "author": "Author B", "year": 2021},
        ]
    )
    lib = types.SimpleNamespace(database=df, config_path=tmp_path)

    result = semantic.analyze_semantic(lib, "q anything", "title")

    assert len(encode_calls) == 1
    texts, batch_size, show_progress_bar = encode_calls[0]
    assert texts == ["A. Author A.", "B. Author B."]
    assert batch_size == 32
    assert show_progress_bar is False
    assert result.embedded_count == 2
    assert result.embedding_work_count == 2


def test_semantic_projection_warmup_runs_once(monkeypatch):
    from archivum.analytics import semantic

    calls = []

    class FakeUMAP:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def fit_transform(self, embeddings):
            return np.zeros((len(embeddings), 2))

    monkeypatch.setitem(sys.modules, "umap", types.SimpleNamespace(UMAP=FakeUMAP))
    monkeypatch.setitem(semantic._UMAP_WARMUP, "started", False)
    monkeypatch.setitem(semantic._UMAP_WARMUP, "finished", False)

    semantic.warm_semantic_projection(background=False)
    semantic.warm_semantic_projection(background=False)

    assert len(calls) == 1
    assert calls[0]["n_jobs"] == -1
    assert "random_state" not in calls[0]
    assert semantic._UMAP_WARMUP["finished"] is True


def test_transformer_warmup_runs_once_and_encodes(monkeypatch, tmp_path):
    from archivum.analytics import semantic

    encode_calls = []

    class FakeSentenceTransformer:
        def __init__(self, *_args, **_kwargs):
            pass

        def encode(self, texts, batch_size=1, show_progress_bar=False):
            encode_calls.append((texts, batch_size, show_progress_bar))
            return np.array([[1.0, 2.0]])

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer))
    monkeypatch.setattr(semantic, "BASE_DIR", tmp_path)
    monkeypatch.setitem(semantic._MODEL_CACHE, "transformer", None)
    monkeypatch.setitem(semantic._TRANSFORMER_WARMUP, "started", False)
    monkeypatch.setitem(semantic._TRANSFORMER_WARMUP, "finished", False)

    semantic.warm_transformer_model(background=False)
    semantic.warm_transformer_model(background=False)

    assert encode_calls == [(["semantic warmup"], 1, False)]
    assert semantic._TRANSFORMER_WARMUP["finished"] is True
