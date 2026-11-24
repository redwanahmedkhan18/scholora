"""
The local semantic embedding model.

This is the backend that actually runs until someone configures a hosted provider, so its
quality is not a theoretical concern - it is the quality of search on the live site.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.core import local_semantic

CORPUS = [
    "Tuition fees and semester contribution for international students in Germany",
    "No tuition is charged in North Rhine-Westphalia, only a semester contribution",
    "Scholarship funding covering tuition and a monthly living stipend",
    "Doctoral fellowship providing a salary and research costs for PhD candidates",
    "MSc Water Resources Engineering covering hydrology and flood management",
    "MSc Computer Science covering machine learning and artificial intelligence",
    "Bachelor of Medicine admission requirements and entrance examination",
    "Cost of living, accommodation and health insurance for a student",
] * 6                                      # above MIN_DOCUMENTS


def test_it_declines_to_fit_on_too_little_data():
    """A model fitted on a handful of documents is noise; hashing is the honest fallback."""
    assert local_semantic._build(["one", "two", "three"], 256) is None


def test_it_compresses_rather_than_reproducing_tfidf(tmp_path, monkeypatch):
    """
    At full rank SVD reproduces TF-IDF exactly - no reduction, so no latent semantics.
    Measured on the real corpus, full rank scored 6/8 on synonym probes and ~90%
    variance scored 7/8. The model must therefore keep fewer components than the ceiling.
    """
    monkeypatch.setattr(local_semantic, "MODEL_PATH", tmp_path / "m.pkl")
    local_semantic.invalidate()
    local_semantic.fit(CORPUS, 256)

    info = local_semantic.info()
    assert info["fitted"]
    ceiling = min(256, len(CORPUS) - 1)
    assert info["components"] < ceiling, "no compression means no semantics were learned"


def test_vectors_are_normalised_and_padded_to_the_column_width(tmp_path, monkeypatch):
    """One pgvector column width across every backend; zero padding keeps cosine intact."""
    monkeypatch.setattr(local_semantic, "MODEL_PATH", tmp_path / "m.pkl")
    local_semantic.invalidate()
    local_semantic.fit(CORPUS, 512)

    vectors = np.array(local_semantic.transform(CORPUS[:4], 512))
    assert vectors.shape == (4, 512)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-6)


def test_it_links_words_that_co_occur_rather_than_matching_literally(tmp_path, monkeypatch):
    """
    The property that separates LSA from keyword search.

    "bursary" never appears in the target document - that one only says "scholarship".
    A hash or a plain term match cannot connect them. LSA can, because other documents
    in the corpus use both words together, and that co-occurrence is exactly what the
    decomposition learns.
    """
    bridge = [
        "Scholarship and bursary awards for international applicants",
        "The bursary scheme and the scholarship scheme are administered together",
        "Applicants may hold a bursary, a scholarship or an external grant",
    ]
    target = "A full scholarship covering tuition and a monthly living allowance"
    distractors = [
        "MSc Water Resources Engineering covering hydrology and flood management",
        "Bachelor of Medicine admission requirements and entrance examination",
        "Semester contribution and transport pass for enrolled students",
        "Machine learning and artificial intelligence research laboratory",
    ]
    corpus = (bridge * 8) + ([target] * 8) + (distractors * 8)

    monkeypatch.setattr(local_semantic, "MODEL_PATH", tmp_path / "m.pkl")
    local_semantic.invalidate()
    local_semantic.fit(corpus, 256)

    candidates = [target] + distractors
    docs = np.array(local_semantic.transform(candidates, 256))
    query = np.array(local_semantic.transform(["bursary"], 256)[0])

    best = candidates[int((docs @ query).argmax())]
    assert best == target, f"'bursary' did not reach the scholarship document, got: {best}"


def test_an_unfitted_model_returns_none_so_the_caller_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(local_semantic, "MODEL_PATH", tmp_path / "missing.pkl")
    local_semantic.invalidate()
    assert local_semantic.transform(["anything"], 256) is None
    assert local_semantic.is_fitted() is False


def test_the_fingerprint_changes_when_the_fit_changes(tmp_path, monkeypatch):
    """
    Vectors from different fits are not comparable. The fingerprint is what makes a refit
    visible to the existing drift detection instead of silently poisoning the index.
    """
    monkeypatch.setattr(local_semantic, "MODEL_PATH", tmp_path / "m.pkl")
    local_semantic.invalidate()
    first = local_semantic.fit(CORPUS, 256)

    local_semantic.invalidate()
    second = local_semantic.fit(CORPUS + ["An entirely new document about visa rules"] * 6, 256)
    assert first and second and first != second
