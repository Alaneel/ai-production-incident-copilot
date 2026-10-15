import pytest

from incident_copilot.cause_family import check_cause_family
from incident_copilot.incident_profiles import PROFILES


@pytest.mark.parametrize("key", PROFILES)
def test_canonical_profile_names_have_no_default_family_or_causal_upgrade(key):
    result = check_cause_family(PROFILES[key].cause, key)
    assert result["status"] == "recognized_family_match"
    assert result["recognized_families"] == [key]
    assert not result["explicit_mismatch"] and not result["semantic_entailment_verified"]


@pytest.mark.parametrize("cause", ["CPU saturation", "cpu overload", "ＣＰＵ saturation", "C\u200bPU saturation", "CPU 饱和"])
def test_explicit_different_family_is_rejected_across_normalized_wording(cause):
    result = check_cause_family(cause, "database_connection_pool_exhaustion")
    assert result["explicit_mismatch"] and result["status"] == "explicit_family_mismatch"


@pytest.mark.parametrize("cause,status", [
    ("Database resource contention", "unrecognized"),
    ("No CPU saturation is observed", "unresolved_negation"),
    ("不是 CPU 饱和，而是连接池耗尽", "unresolved_negation"),
    ("Pool exhaustion plus CPU saturation", "multiple_families_unresolved"),
])
def test_unrecognized_negation_and_multiple_families_are_not_invented_semantic_matches(cause, status):
    result = check_cause_family(cause, "database_connection_pool_exhaustion")
    assert result["status"] == status and not result["explicit_mismatch"]
    assert not result["semantic_entailment_verified"]
