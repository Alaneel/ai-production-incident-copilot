"""Binary relevance metrics; duplicates consume ranks but earn no extra gain."""
from __future__ import annotations

import math

METRICS_VERSION = "binary-retrieval-v2-log2-unique-gain"


def _check_k(k: int) -> None:
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError("k must be a positive integer")


def recall_at_k(retrieved: list[str], relevant: set[str], k: int = 5) -> float:
    _check_k(k)
    return len(set(retrieved[:k]) & relevant) / len(relevant) if relevant else 0.0


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int = 5) -> float:
    _check_k(k)
    seen: set[str] = set()
    dcg = 0.0
    for rank, item in enumerate(retrieved[:k], start=1):
        if item in relevant and item not in seen:
            dcg += 1.0 / math.log2(rank + 1)
        seen.add(item)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(k, len(relevant)) + 1))
    return dcg / idcg if idcg else 0.0
