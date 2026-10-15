"""Detect explicit known-family contradictions, never semantic entailment."""
import re
import unicodedata


PATTERNS = {
    "database_connection_pool_exhaustion": r"\b(?:connection\s+)?pool\s+(?:exhaust\w*|saturat\w*)\b|连接池.{0,12}(?:耗尽|饱和|用尽)",
    "cpu_saturation": r"\bcpu\s+(?:saturat\w*|overload\w*|contention|throttl\w*)\b|cpu.{0,12}(?:饱和|过载|争用)",
    "memory_leak": r"\bmemory\s+leak\w*\b|内存泄漏",
    "deployment_regression": r"\b(?:release|deployment|rollout)\s+regress\w*\b|(?:发布|部署)回归",
    "downstream_timeout": r"\bdownstream(?:\s+dependency)?\s+timeout\b|下游.{0,12}超时",
    "kafka_consumer_lag": r"\b(?:kafka\s+)?consumer\s+(?:lag|processing\s+slowdown)\b|(?:消费者|消费).{0,12}(?:积压|延迟)",
    "retry_storm": r"\bretry(?:\s+amplification)?\s+storm\b|重试(?:放大)?风暴",
    "cache_penetration": r"\bcache\s+penetration\b|缓存穿透",
}
_PATTERNS = {key: re.compile(pattern, re.I) for key, pattern in PATTERNS.items()}
_NEGATION = re.compile(r"\b(?:not|no|without|unlikely|ruled?\s+out)\b|不是|并非|排除|没有", re.I)


def check_cause_family(cause: str, selected: str) -> dict:
    inspected = unicodedata.normalize("NFKC", cause)
    inspected = "".join(char for char in inspected if unicodedata.category(char) != "Cf")
    inspected = " ".join(inspected.split())
    candidates = [key for key, pattern in _PATTERNS.items() if pattern.search(inspected)]
    if _NEGATION.search(inspected):
        status = "unresolved_negation"
    elif not candidates:
        status = "unrecognized"
    elif len(candidates) > 1:
        status = "multiple_families_unresolved"
    elif candidates[0] != selected:
        status = "explicit_family_mismatch"
    else:
        status = "recognized_family_match"
    return {"status": status, "recognized_families": candidates,
            "explicit_mismatch": status == "explicit_family_mismatch",
            "semantic_entailment_verified": False,
            "limitation": "Bounded phrase detector; negation, multi-cause text and unrecognized wording are unresolved, not validated."}
