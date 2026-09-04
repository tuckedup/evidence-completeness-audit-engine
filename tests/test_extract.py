from ecsae.extract import RegexBiomedicalExtractor
from ecsae.extract.offline import GLiNERBiomedicalExtractor

import pytest


def test_transformer_requires_local_model_directory() -> None:
    with pytest.raises(ValueError, match="local, pre-downloaded"):
        GLiNERBiomedicalExtractor("missing-model", revision="0" * 40)


def test_transformer_requires_commit_sha_revision() -> None:
    with pytest.raises(ValueError, match="40-character model revision SHA"):
        GLiNERBiomedicalExtractor("missing-model", revision="model-tag")


def test_numeric_extraction() -> None:
    text = "We randomized N=100. Arm control: n=50; group treatment: n=50. t(98)=2.35, p=0.021."
    result = RegexBiomedicalExtractor().extract(text, study_id="T1")
    assert result["randomized_n"] == 100
    assert result["arm_ns"] == {"control": 50, "treatment": 50}
    assert result["reported_stats"][0]["stat_type"] == "t"

