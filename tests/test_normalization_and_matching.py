from __future__ import annotations

from oposiciones_bot.matching import canonical_key, semantic_snapshot, title_signature
from oposiciones_bot.models import AccessType, ProcessStatus
from oposiciones_bot.normalization import canonical_json, normalize_reference, stable_hash


def test_stable_hash_is_independent_of_mapping_insertion_order() -> None:
    first = {"b": [3, 2, 1], "a": {"z": True, "á": "Jaén"}}
    second = {"a": {"á": "Jaén", "z": True}, "b": [3, 2, 1]}

    assert canonical_json(first) == canonical_json(second)
    assert stable_hash(first) == stable_hash(second)
    assert len(stable_hash(first)) == 64


def test_reference_normalization_is_stable() -> None:
    assert normalize_reference(" BOE-A-2026 / 1.000 ") == "BOEA20261000"
    assert normalize_reference("boe-a-2026-1000") == "BOEA20261000"


def test_canonical_key_ignores_mutable_fields(candidate_factory) -> None:
    original = candidate_factory()
    changed = candidate_factory(
        url="https://sede.example/otra-url",
        deadline="2026-11-05",
        deadline_confirmed=False,
        positions=20,
        status=ProcessStatus.EXAM_ANNOUNCED,
        exam_date="2026-12-01",
        summary="Texto completamente distinto",
    )

    assert canonical_key(original) == canonical_key(changed)


def test_canonical_key_separates_year_and_access_route(candidate_factory) -> None:
    original = candidate_factory()
    next_year = candidate_factory(
        source_id="BOE-A-2027-1000",
        title="Convocatoria de Técnico Informático 2027",
        publication_date="2027-02-01",
        reference="BOE-A-2027-1000",
        official_references=["BOE-A-2027-1000"],
    )
    internal = candidate_factory(access=AccessType.INTERNAL_ONLY)

    assert canonical_key(original) != canonical_key(next_year)
    assert canonical_key(original) != canonical_key(internal)


def test_title_signature_removes_publication_noise() -> None:
    signature = title_signature(
        "Resolución 123/2026 por la que se convoca una plaza de Técnico Informático"
    )

    assert "resolucion" not in signature
    assert "convoca" not in signature
    assert "123" not in signature
    assert "2026" not in signature
    assert signature.endswith("tecnico informatico")


def test_semantic_snapshot_ignores_url_and_summary_but_tracks_relevant_change(
    candidate_factory,
) -> None:
    original = candidate_factory()
    editorial_change = candidate_factory(
        url="https://example.test/espejo",
        summary="Nuevo resumen editorial sin cambios materiales",
    )
    deadline_change = candidate_factory(deadline="2026-10-31")

    assert stable_hash(semantic_snapshot(original)) == stable_hash(
        semantic_snapshot(editorial_change)
    )
    assert stable_hash(semantic_snapshot(original)) != stable_hash(
        semantic_snapshot(deadline_change)
    )
