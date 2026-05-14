from __future__ import annotations

import inspect
import json
import types

import numpy as np
import pandas as pd


def _fake_lib(tmp_path):
    return types.SimpleNamespace(
        config=types.SimpleNamespace(
            bibtex_file=str(tmp_path / "library.bib"),
            csl_file=str(tmp_path / "style.csl"),
        ),
        ref_doc_df=pd.DataFrame(columns=["tag", "hash", "version"]),
        doc_df=pd.DataFrame(columns=["hash", "version", "path"]),
        textpath=lambda _path: tmp_path / "missing.txt",
        abspath=lambda path: tmp_path / str(path),
    )


def test_query_report_respects_no_abstracts(tmp_path):
    from archivum.quarto import generate_qmd_report

    lib = _fake_lib(tmp_path)
    out_path = tmp_path / "query-report.qmd"
    df = pd.DataFrame(
        [
            {
                "tag": "Smith2024",
                "type": "article",
                "author": "Smith, A",
                "title": "A Reported Paper",
                "year": 2024,
                "hash": "h1",
                "path": "h1.pdf",
            }
        ]
    )

    generate_qmd_report(
        lib,
        df,
        out_path,
        title="Query Report",
        include_abstract=False,
        query="q Smith",
    )

    text = out_path.read_text(encoding="utf-8")
    assert "Smith2024" in text
    assert "## Query" in text
    assert "\n\n> " not in text


def test_semantic_report_writes_svg_assets_and_groups_background_last(tmp_path):
    from archivum.analytics.semantic import SemanticResult
    from archivum.quarto import generate_semantic_qmd_report

    lib = _fake_lib(tmp_path)
    out_path = tmp_path / "semantic-report.qmd"
    result_df = pd.DataFrame(
        [
            {"hash": "h1", "tag": "Alpha2024", "title": "Alpha Paper", "author": "Alpha, A", "year": 2024},
            {"hash": "h2", "tag": "Beta2023", "title": "Beta Paper", "author": "Beta, B", "year": 2023},
            {"hash": "h3", "tag": "Noise2022", "title": "Noise Paper", "author": "Noise, N", "year": 2022},
        ]
    )
    result = SemanticResult(
        result_df=result_df,
        relevant_idx=pd.DataFrame(
            [
                {"hash": "h1", "source": "title", "embedding": [1.0, 0.0]},
                {"hash": "h2", "source": "title", "embedding": [0.0, 1.0]},
                {"hash": "h3", "source": "title", "embedding": [0.2, 0.2]},
            ]
        ),
        cluster_labels=np.array([0, 0, -1]),
        coords=np.array([[0.0, 0.0], [1.0, 0.2], [4.0, 4.0]]),
        source_type="title",
        cluster_summary=[
            {
                "id": 0,
                "number": 1,
                "name": "Risk Measures",
                "count": 2,
                "samples": ["Alpha Paper", "Beta Paper"],
                "color": "#0d6efd",
            }
        ],
        cluster_themes={0: "Risk Measures"},
    )

    generate_semantic_qmd_report(
        lib,
        result,
        out_path,
        title="Semantic Report",
        query="q risk",
        include_abstract=False,
    )

    text = out_path.read_text(encoding="utf-8")
    assert "![Semantic cluster overview](/reports/asset/semantic-report-semantic-hulls.svg)" in text
    assert "![Semantic galaxy map](/reports/asset/semantic-report-semantic-galaxy.svg)" in text
    assert "Related terms:" in text
    assert "cluster-summary-table" in text
    assert "## Cluster Description" in text
    assert "Expanded description" in text
    assert (tmp_path / "semantic-report-semantic-hulls.svg").exists()
    assert (tmp_path / "semantic-report-semantic-galaxy.svg").exists()
    assert text.index("### 1: Risk Measures") < text.index("### Lone Star")


def test_social_report_writes_svg_and_summary(tmp_path):
    from archivum.analytics.networks import SocialNetworkResult
    from archivum.quarto import generate_social_qmd_report

    lib = _fake_lib(tmp_path)
    out_path = tmp_path / "social-report.qmd"
    result = SocialNetworkResult(
        result_df=pd.DataFrame(
            [
                {"hash": "h1", "tag": "Smith2024", "title": "Network Paper", "author": "Smith, A and Jones, B", "year": 2024},
            ]
        ),
        nodes=[
            {"data": {"id": "Smith, A", "label": "Smith, A", "weight": 1, "papers": []}},
            {"data": {"id": "Jones, B", "label": "Jones, B", "weight": 1, "papers": []}},
        ],
        edges={("Jones, B", "Smith, A"): {"weight": 1, "papers": []}},
    )

    generate_social_qmd_report(lib, result, out_path, title="Social Report", query="q Smith")

    text = out_path.read_text(encoding="utf-8")
    assert "![Social network](/reports/asset/social-report-social-network.svg)" in text
    assert "## Top Authors" in text
    assert "## Top Collaborations" in text
    assert (tmp_path / "social-report-social-network.svg").exists()


def test_library_init_does_not_auto_cleanup_exports():
    from archivum.library import Library

    source = inspect.getsource(Library.__init__)
    assert "_cleanup_exports(" not in source


def test_report_metadata_round_trips(tmp_path):
    from archivum.web.routes.reports import _load_report_meta, _write_report_meta

    lib = types.SimpleNamespace(exports_dir_path=tmp_path)
    out_path = tmp_path / "saved-report.qmd"

    _write_report_meta(
        lib,
        out_path,
        title="Saved Report",
        filename="saved-report.qmd",
        intro="Initial intro",
        raw_query="q top 5 recent",
        source="query",
        semantic_source="title",
        case_mode="insensitive",
        include_abstract=False,
    )

    meta = _load_report_meta(lib, "saved-report")
    assert meta["version"] == 1
    assert meta["title"] == "Saved Report"
    assert meta["include_abstract"] is False
    assert meta["intro"] == "Initial intro"

    raw = json.loads((tmp_path / "saved-report.report.json").read_text(encoding="utf-8"))
    assert raw["filename"] == "saved-report.qmd"
