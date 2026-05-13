from __future__ import annotations

import sys
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
    )

    payload = result.to_cytoscape_json(verbosity="verbose")
    messages = payload["log_messages"]

    assert f"Model: {SEMANTIC_MODEL_NAME}" in messages
    assert f"Model cache: {tmp_path}" in messages
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
    )

    payload = result.to_cytoscape_json(verbosity="verbose")
    messages = payload["log_messages"]

    assert any(message.startswith("Timing: test social phase ") for message in messages)
    assert any(message.startswith("Timing: social payload serialization ") for message in messages)
