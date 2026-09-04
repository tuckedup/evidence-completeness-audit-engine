from __future__ import annotations

import random
from collections.abc import Iterable
from typing import Any


def agreement_metrics(reference: Iterable[bool], predicted: Iterable[bool]) -> dict[str, Any]:
    pairs = list(zip(reference, predicted, strict=True))
    if not pairs:
        raise ValueError("at least one label pair is required")
    tp = sum(r and p for r, p in pairs)
    tn = sum(not r and not p for r, p in pairs)
    fp = sum(not r and p for r, p in pairs)
    fn = sum(r and not p for r, p in pairs)
    n = len(pairs)
    observed = (tp + tn) / n
    p_ref = (tp + fn) / n
    p_pred = (tp + fp) / n
    expected_kappa = p_ref * p_pred + (1 - p_ref) * (1 - p_pred)
    kappa = (observed - expected_kappa) / (1 - expected_kappa) if expected_kappa < 1 else 1.0
    mean_positive = (p_ref + p_pred) / 2
    expected_ac1 = 2 * mean_positive * (1 - mean_positive)
    ac1 = (observed - expected_ac1) / (1 - expected_ac1) if expected_ac1 < 1 else 1.0
    return {
        "n": n,
        "confusion_matrix": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
        "raw_agreement": observed,
        "cohens_kappa": kappa,
        "gwets_ac1": ac1,
        "reference_positive_rate": p_ref,
        "predicted_positive_rate": p_pred,
    }


def bootstrap_intervals(
    reference: list[bool], predicted: list[bool], *, replicates: int = 2000, seed: int = 20250314
) -> dict[str, list[float]]:
    if len(reference) != len(predicted) or not reference:
        raise ValueError("non-empty label arrays must have equal lengths")
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {"raw_agreement": [], "cohens_kappa": [], "gwets_ac1": []}
    for _ in range(replicates):
        indexes = [rng.randrange(len(reference)) for _ in reference]
        metric = agreement_metrics([reference[i] for i in indexes], [predicted[i] for i in indexes])
        for name in samples:
            samples[name].append(metric[name])
    result: dict[str, list[float]] = {}
    for name, values in samples.items():
        values.sort()
        result[name] = [values[int(0.025 * replicates)], values[min(replicates - 1, int(0.975 * replicates))]]
    return result

