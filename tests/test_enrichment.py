from __future__ import annotations

import pytest

from oposiciones_bot.enrichment import (
    enrich_candidate,
    extract_deadline,
    extract_exam_date,
    extract_exam_place,
    extract_exam_time,
    extract_official_references,
)
from oposiciones_bot.models import AccessType


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("El plazo de presentación finaliza el 21/10/2026.", "2026-10-21"),
        ("Las solicitudes podrán presentarse hasta el 15/11/2026.", "2026-11-15"),
        ("Fin del plazo: 3-1-2027", "2027-01-03"),
        (
            "El plazo de presentación de solicitudes será del 01/10/2026 al 20/10/2026.",
            "2026-10-20",
        ),
    ],
)
def test_extract_deadline_preserves_the_complete_day(text: str, expected: str) -> None:
    assert extract_deadline(text) == (expected, True)


def test_extract_exam_date_preserves_the_complete_day() -> None:
    text = "La fecha de celebración del primer ejercicio será el 27/11/2026."

    assert extract_exam_date(text) == "2026-11-27"


@pytest.mark.parametrize(
    "text",
    [
        "El examen tendrá lugar el día 20/11/2026.",
        "El primer ejercicio se celebrará el 20/11/2026.",
    ],
)
def test_extract_exam_date_accepts_common_official_wording(text: str) -> None:
    assert extract_exam_date(text) == "2026-11-20"


def test_extract_exam_time_and_place() -> None:
    text = "El examen será el 20/11/2026 en Aula 9 a las 12:00."

    assert extract_exam_time(text) == "12:00"
    assert extract_exam_place(text) == "Aula 9"


def test_enrichment_combines_title_summary_and_detail(candidate_factory) -> None:
    candidate = candidate_factory(
        title=(
            "Anuncio para la provisión de una plaza de analista programador "
            "mediante oposición libre"
        ),
        summary="Puntuaciones del segundo ejercicio",
        full_text="Se abre un plazo de alegaciones.",
        positions=None,
        access=AccessType.UNKNOWN,
        qualification_text="",
    )

    enrich_candidate(candidate)

    assert candidate.positions == 1
    assert candidate.access is AccessType.OPEN


def test_corps_codes_and_weak_bulletin_citations_are_not_identity_references() -> None:
    text = (
        "Cuerpo C1.2003; conforme al BOJA núm. 50 de 2023 y a "
        "BOE-A-2026-1234."
    )

    assert extract_official_references(text) == ["BOEA20261234"]


def test_enrichment_keeps_cited_reference_separate_from_primary_identity(
    candidate_factory,
) -> None:
    candidate = candidate_factory(
        reference="BOE-A-2026-9999",
        official_references=["BOE-A-2026-9999"],
        full_text="Este anuncio cita el BOE-A-2025-1234 como antecedente.",
    )

    enrich_candidate(candidate)

    assert candidate.official_references == ["BOEA20269999"]
    assert candidate.raw["cited_references"] == ["BOEA20251234"]
