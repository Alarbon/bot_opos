from datetime import date

import pytest

from oposiciones_bot.catalog import build_catalog


def test_catalog_follow_is_explicit_and_persistent(store, candidate_factory, included_decision, app_config):
    result = store.ingest(candidate_factory(), included_decision)
    catalog = build_catalog(store, app_config, date(2026, 10, 6))
    assert catalog["processes"][0]["category"] == "OPEN"
    assert not catalog["processes"][0]["followed"]
    store.follow(result.process_id[:8])
    assert build_catalog(store, app_config, date(2026, 10, 6))["processes"][0]["followed"]
    store.follow(result.process_id[:8], False)
    assert not store.is_followed(result.process_id)


def test_missing_fields_visible_as_review_but_a2_excluded(store, candidate_factory, included_decision, app_config):
    app_config.data["eligibility"] = {"allowed_groups": ["B", "C1"], "require_confirmed_qualification": True}
    store.ingest(candidate_factory(group="", qualification_text="", source_id="unknown", reference="unknown"), included_decision)
    data = build_catalog(store, app_config, date(2026, 10, 6))
    assert data["processes"][0]["category"] == "REVIEW"
    assert data["processes"][0]["compatibility"] == "REVISAR"
    assert "raw" not in data["processes"][0]
    assert "full_text" not in data["processes"][0]
    app_config.data["eligibility"]["allowed_groups"] = ["B"]
    # Known C1 is excluded; unknown group still available for manual review.
    store.ingest(candidate_factory(group="A2", source_id="a2", reference="a2", title="Convocatoria analista informático"), included_decision)
    assert all(p["group"] != "A2" for p in build_catalog(store, app_config, date(2026, 10, 6))["processes"])


def test_invalid_follow_id(store):
    with pytest.raises(ValueError):
        store.follow("'; DROP TABLE processes")
