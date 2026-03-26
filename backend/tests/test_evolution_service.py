"""Unit tests for evolution helpers (no DB)."""

from app.services.evolution_service import shallow_merge_details, suggest_next_catalog_version


def test_shallow_merge_nested() -> None:
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    patch = {"nested": {"y": 9, "z": 3}, "b": 2}
    out = shallow_merge_details(base, patch)
    assert out["a"] == 1
    assert out["b"] == 2
    assert out["nested"]["x"] == 1
    assert out["nested"]["y"] == 9
    assert out["nested"]["z"] == 3


def test_suggest_next_semver() -> None:
    assert suggest_next_catalog_version("1.0.0") == "1.0.1"
    assert suggest_next_catalog_version("2.3.9") == "2.3.10"


def test_suggest_next_fallback() -> None:
    assert suggest_next_catalog_version("v3") == "v4"
    assert suggest_next_catalog_version("custom") == "custom-evo"
