# -*- coding: utf-8 -*-
"""Statistics used in the paper that are not provided by scikit-learn.

- weighted_kappa: Cohen's kappa with quadratic weights over the label order
  (meaningful only for an ordinal variable, here the contamination class);
- mcnemar_exact_p: two-sided exact (binomial) McNemar test, no SciPy needed;
- wilson: Wilson score interval for a proportion.
"""
from __future__ import annotations

import math
from math import comb
from typing import Optional, Sequence, Tuple

import numpy as np

Z95 = 1.959964


def weighted_kappa(y_true, y_pred, labels: Optional[Sequence] = None, W=None) -> float:
    """Weighted Cohen's kappa.

    By default the weights are quadratic in the distance between label
    indices: W[i, j] = (i - j)^2 / (K - 1)^2. The order of `labels` therefore
    matters and must follow the ordinal scale.
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    assert y_true.shape == y_pred.shape

    if labels is None:
        labels = sorted(set(y_true) | set(y_pred))
    label_to_idx = {lab: i for i, lab in enumerate(labels)}
    K = len(labels)

    C = np.zeros((K, K), dtype=float)
    for t, p in zip(y_true, y_pred):
        C[label_to_idx[t], label_to_idx[p]] += 1.0

    n = C.sum()
    if n == 0:
        return np.nan

    O = C / n
    r = O.sum(axis=1, keepdims=True)
    c = O.sum(axis=0, keepdims=True)
    E = r @ c

    if W is None:
        ii, jj = np.meshgrid(np.arange(K), np.arange(K), indexing="ij")
        if K == 1:
            return 1.0
        W = ((ii - jj) ** 2) / ((K - 1) ** 2)
    else:
        W = np.asarray(W, dtype=float)
        assert W.shape == (K, K)

    num = (W * O).sum()
    den = (W * E).sum()
    if den == 0:
        return 1.0 if num == 0 else np.nan
    return 1.0 - num / den


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from the two discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def wilson(p: float, n: int, z: float = Z95) -> Tuple[float, float]:
    """Wilson score interval for a proportion p observed on n trials."""
    if not n or (isinstance(p, float) and math.isnan(p)):
        return (float("nan"), float("nan"))
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))
    return centre - half, centre + half


def fmt_ci(p: float, n: int) -> str:
    lo, hi = wilson(p, n)
    return f"{p:.3f} (95% CI {lo:.3f}-{hi:.3f})"
