from ecsae.evaluation import agreement_metrics, bootstrap_intervals


def test_agreement_metrics_known_matrix() -> None:
    result = agreement_metrics([True, True, False, False], [True, False, True, False])
    assert result["confusion_matrix"] == {"tp": 1, "tn": 1, "fp": 1, "fn": 1}
    assert result["raw_agreement"] == 0.5
    assert result["cohens_kappa"] == 0.0
    assert result["gwets_ac1"] == 0.0


def test_bootstrap_is_reproducible() -> None:
    ref = [True, False] * 10
    pred = [True, False] * 9 + [False, True]
    assert bootstrap_intervals(ref, pred, replicates=100, seed=7) == bootstrap_intervals(ref, pred, replicates=100, seed=7)

