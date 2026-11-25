"""
A local semantic embedding model: TF-IDF followed by truncated SVD (latent semantic
analysis), fitted on the platform's own corpus.

Why this exists. The previous local fallback was a hashing vectoriser, and hashing has no
notion of meaning at all: "tuition" and "fees" land in unrelated buckets, so a student
asking about fees could not reach a chunk that says tuition. It was a keyword matcher
wearing a vector's clothes, and it was the active backend in production.

LSA is not a transformer, and it will not match a hosted embedding model. What it does do
is learn from co-occurrence in *this* corpus that tuition, fees, semester contribution and
cost belong together - which is most of the value, for no API key, no network call and no
per-query cost.

Design notes:

* The model is fitted at reindex time on the full corpus and persisted. Queries then
  transform against the frozen model; a query must never refit, or stored vectors and
  query vectors would come from different spaces and every score would be meaningless.
* Output is padded with zeros to the configured width. Cosine similarity is unaffected by
  trailing zeros, so this keeps one pgvector column width across every backend.
* `fingerprint()` changes whenever the fitted model changes, so `active_model_name()` can
  expose it and the existing drift detection will flag chunks embedded under an older fit.
"""
from __future__ import annotations

import hashlib
import logging
import pickle
import threading
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

log = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).resolve().parents[1] / "data" / "local_semantic_model.pkl"
_lock = threading.Lock()
_cache: Optional["_Model"] = None

# Below this many documents SVD has nothing to learn and the result is noise; the caller
# falls back to hashing instead.
MIN_DOCUMENTS = 25

# Share of variance to retain. Chosen empirically on this corpus - see _build().
VARIANCE_TARGET = 0.90

# A variance target alone can retain the full effective rank when a corpus contains many
# repeated documents. At full rank SVD reproduces TF-IDF exactly: terms that never share a
# document (for example "bursary" and "scholarship") remain orthogonal, so no semantic
# bridge is learned. Keeping at most half the effective rank forces the factorisation to
# generalise from co-occurrence instead of copying the term matrix. Half is the standard
# LSA low-rank heuristic; a higher share still passes some probes but leaves the bridge to
# floating-point noise (measured: at 6/8 components the target won by 0.06, at 4/8 by 0.19).
MAX_RANK_SHARE = 0.5


class _Model:
    def __init__(self, vectorizer, svd, components: int, documents: int) -> None:
        self.vectorizer = vectorizer
        self.svd = svd
        self.components = components
        self.documents = documents

    @property
    def fingerprint(self) -> str:
        """Identifies this particular fit, so vectors from different fits are detectable."""
        vocab = getattr(self.vectorizer, "vocabulary_", {}) or {}
        basis = f"{len(vocab)}:{self.components}:{self.documents}"
        return hashlib.blake2b(basis.encode(), digest_size=6).hexdigest()


def _build(texts: Sequence[str], width: int) -> Optional[_Model]:
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer

    usable = [t for t in texts if (t or "").strip()]
    if len(usable) < MIN_DOCUMENTS:
        return None

    vectorizer = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        # Word unigrams and bigrams carry the domain phrases that matter here -
        # "semester contribution", "tuition fee", "research master".
        ngram_range=(1, 2),
        min_df=1,
        max_df=0.85,
        sublinear_tf=True,
        stop_words="english",
    )
    matrix = vectorizer.fit_transform(usable)

    # Fit as far as the data allows, then keep only the components that carry real
    # signal. Taking the maximum is a trap: at full rank SVD reproduces TF-IDF exactly,
    # so there is no dimensionality reduction and therefore no latent semantics - the
    # whole point of doing this. Measured on this corpus, full rank (316 components)
    # answered 6 of 8 synonym probes correctly while ~92% variance answered 7, so the
    # target is a share of variance, not a component count.
    ceiling = max(2, min(width, matrix.shape[0] - 1, matrix.shape[1] - 1))
    svd = TruncatedSVD(n_components=ceiling, random_state=0, algorithm="randomized")
    svd.fit(matrix)

    ratios = np.asarray(svd.explained_variance_ratio_)
    effective_rank = max(
        1,
        int(np.count_nonzero(ratios > max(1e-12, float(ratios.max()) * 1e-6))),
    )
    cumulative = np.cumsum(ratios)
    keep = int(np.searchsorted(cumulative, VARIANCE_TARGET) + 1)
    keep = max(2, min(keep, ceiling))
    if effective_rank >= 3:
        rank_cap = max(
            2,
            min(effective_rank - 1, int(np.ceil(effective_rank * MAX_RANK_SHARE))),
        )
        keep = min(keep, rank_cap)
    # TruncatedSVD.transform is X @ components_.T, so trimming the rows trims the output
    # dimensionality without refitting.
    svd.components_ = svd.components_[:keep]
    if hasattr(svd, "explained_variance_ratio_"):
        svd.explained_variance_ratio_ = svd.explained_variance_ratio_[:keep]
    return _Model(vectorizer, svd, keep, matrix.shape[0])


def fit(texts: Sequence[str], width: int) -> Optional[str]:
    """Fit on the corpus and persist. Returns the fingerprint, or None if unfittable."""
    model = _build(texts, width)
    if model is None:
        log.info("local semantic: only %d documents, keeping the hashing fallback", len(texts))
        return None

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with open(MODEL_PATH, "wb") as fh:
            pickle.dump(model, fh, protocol=pickle.HIGHEST_PROTOCOL)
        global _cache
        _cache = model
    log.info("local semantic: fitted %d components over %d documents",
             model.components, model.documents)
    return model.fingerprint


def _load() -> Optional[_Model]:
    global _cache
    if _cache is not None:
        return _cache
    if not MODEL_PATH.exists():
        return None
    try:
        with _lock:
            with open(MODEL_PATH, "rb") as fh:
                _cache = pickle.load(fh)
        return _cache
    except Exception as exc:                                        # noqa: BLE001
        # A corrupt or stale pickle must not take the search down; hashing still works.
        log.warning("local semantic: could not load the model (%s), using hashing", exc)
        return None


def is_fitted() -> bool:
    return _load() is not None


def fingerprint() -> Optional[str]:
    model = _load()
    return model.fingerprint if model else None


def info() -> dict:
    model = _load()
    if not model:
        return {"fitted": False}
    return {"fitted": True, "components": model.components,
            "documents": model.documents,
            "vocabulary": len(getattr(model.vectorizer, "vocabulary_", {}) or {}),
            "fingerprint": model.fingerprint}


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def transform(texts: Sequence[str], width: int) -> Optional[List[List[float]]]:
    """Embed with the fitted model, L2-normalised and zero-padded to `width`."""
    texts = list(texts)
    if not texts:
        # TfidfTransformer rejects an empty batch; an empty result is the honest answer.
        return []
    model = _load()
    if model is None:
        return None
    try:
        reduced = model.svd.transform(model.vectorizer.transform(texts))
    except Exception as exc:                                        # noqa: BLE001
        log.warning("local semantic: transform failed (%s), using hashing", exc)
        return None

    reduced = np.asarray(reduced, dtype=float)
    norms = np.linalg.norm(reduced, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    reduced = reduced / norms

    if reduced.shape[1] < width:
        # Zero padding leaves cosine similarity unchanged and keeps one column width.
        reduced = np.hstack([reduced, np.zeros((reduced.shape[0], width - reduced.shape[1]))])
    elif reduced.shape[1] > width:
        reduced = reduced[:, :width]
    return reduced.tolist()
