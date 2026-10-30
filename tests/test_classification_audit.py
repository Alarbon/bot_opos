"""Regression tests for the classification audit (October 2026)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from oposiciones_bot.classifiers import (
    classify_qualification,
    deadline_window_open,
    evaluate_candidate,
    is_it_role_title,
    reviewable,
)
from oposiciones_bot.config import load_config
from oposiciones_bot.enrichment import (
    enrich_candidate,
    exam_date_is_provisional,
    extract_deadline,
    extract_exam_date,
    extract_group,
    extract_relative_deadline,
)
from oposiciones_bot.models import AccessType, Candidate, Compatibility, ProcessStatus

CONFIG = load_config(Path(__file__).resolve().parents[1] / "config.yaml")


def iaap_candidate(title: str, **overrides) -> Candidate:
    values = dict(
        source="iaap", source_id=title, title=title, organisation="Iaap",
        url="https://portalempleopublico.juntadeandalucia.es/x", scope="Andalucia",
        summary=f"{title} En curso. Proceso selectivo acceso libre.",
    )
    values.update(overrides)
    return enrich_candidate(Candidate(**values))


# --- Real Junta processes that were hidden from the catalogue ------------------


@pytest.mark.parametrize(
    ("title", "group"),
    [
        ("B1.2000 Tecnologías de la Información y Comunicación", "B"),
        ("C1.2003 Ayudantes técnicos - Opción Informática", "C1"),
    ],
)
def test_junta_fp_superior_it_corps_are_kept_for_review(title, group):
    candidate = iaap_candidate(title)
    assert candidate.group == group
    decision = evaluate_candidate(candidate, CONFIG)
    assert "el titulo no identifica un puesto informatico" not in decision.reason
    # The requirement text is not available yet: REVISAR, never COMPATIBLE.
    assert reviewable(candidate, CONFIG)
    assert candidate.compatibility is not Compatibility.COMPATIBLE


@pytest.mark.parametrize(
    "title",
    ["A1.2019 Tecnologías de la Información y Comunicación", "A2.2012 Tecnologías de la Información y Comunicación"],
)
def test_university_groups_of_the_same_family_stay_excluded(title):
    candidate = iaap_candidate(title)
    assert not evaluate_candidate(candidate, CONFIG).include
    assert not reviewable(candidate, CONFIG)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Técnico/a de Informática", True),
        ("Ayudantes técnicos - Opción Informática", True),
        ("Analista programadora/or", True),
        ("Operadores de sistemas informáticos", True),
        ("Cuerpo de Técnicos Auxiliares de Informática", True),
        ("Auxiliar administrativo", False),
        ("Administrativo con conocimientos informáticos de ofimática", False),
        ("Peón de jardinería", False),
    ],
)
def test_it_role_title(title, expected):
    assert is_it_role_title(title) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Cuerpo B1.2000", "B"),
        ("Grupo C, Subgrupo C1", "C1"),
        ("Acreditar nivel B1 de inglés", ""),
        ("Subgrupo A2 de la escala técnica", "A2"),
    ],
)
def test_group_extraction(text, expected):
    assert extract_group(text) == expected


# --- Requirements are judged per sentence ------------------------------------


def test_multi_post_document_is_not_compatible_by_mixing_requirements():
    text = (
        "Requisitos de la plaza de Técnico de Sistemas: estar en posesión del Grado en Ingeniería Informática. "
        "Requisitos de la plaza de Auxiliar Administrativo: estar en posesión del título de Bachiller o equivalente."
    )
    evidence = classify_qualification(text)
    assert evidence.value == Compatibility.REVIEW.value
    assert "varias plazas" in evidence.text


def test_explicit_alternative_in_the_same_requirement_is_compatible():
    text = "Requisito: estar en posesión del título de Técnico Superior en ASIR o Grado en Ingeniería Informática."
    assert classify_qualification(text).value == Compatibility.COMPATIBLE.value


def test_university_only_requirement_is_not_compatible():
    text = "Se requiere estar en posesión del título de Grado en Ingeniería Informática o equivalente."
    assert classify_qualification(text).value == Compatibility.NOT_COMPATIBLE.value


def test_it_title_without_requirements_is_review_never_compatible(candidate_factory):
    candidate = candidate_factory(qualification_text="", full_text="Se convoca una plaza de técnico informático.")
    evaluate_candidate(candidate, CONFIG)
    assert candidate.compatibility is Compatibility.REVIEW


# --- Dates are never invented ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Lista de admitidos de las pruebas selectivas convocadas por Resolución de 12/05/2026.",
        "Relación de aprobados del ejercicio publicada en el BOP de 02/10/2026.",
        "Examen. Bases aprobadas por Decreto de 03/03/2026.",
    ],
)
def test_act_or_bulletin_dates_are_not_exam_dates(text):
    assert extract_exam_date(text) is None


def test_genuine_exam_date_is_still_found():
    assert extract_exam_date("Los aspirantes convocados realizarán el examen el día 20/11/2026.") == "2026-11-20"


def test_bulletin_date_near_solicitudes_is_not_a_deadline():
    assert extract_deadline("Las solicitudes, según Resolución publicada en el BOP de 02/10/2026, se presentarán en el registro.") == (None, False)


def test_provisional_exam_date_is_flagged():
    text = "El primer ejercicio se celebrará previsiblemente el 20/11/2026; fecha provisional pendiente de confirmar."
    assert exam_date_is_provisional(text)
    candidate = enrich_candidate(Candidate(source="x", source_id="1", title="Técnico informático", organisation="Ayto", url="", full_text=text))
    assert candidate.exam_date == "2026-11-20"
    assert candidate.raw["exam_date_provisional"] is True
    assert not exam_date_is_provisional("El examen será el 20/11/2026 en el aula 3.")


# --- Relative deadlines --------------------------------------------------------

RELATIVE = (
    "El plazo de presentación de solicitudes será de veinte días hábiles contados a partir del día "
    "siguiente al de la publicación del extracto de esta convocatoria en el Boletín Oficial del Estado."
)


def test_relative_deadline_is_detected_but_not_confirmed():
    relative = extract_relative_deadline(RELATIVE)
    assert relative == {"days": 20, "kind": "habiles", "starts_after": "BOE", "text": relative["text"]}
    candidate = enrich_candidate(Candidate(source="bop_jaen", source_id="1", title="Técnico informático", organisation="Ayto", url="", full_text=RELATIVE))
    assert candidate.deadline is None and candidate.deadline_confirmed is False
    assert candidate.raw["relative_deadline"]["days"] == 20


def relative_candidate(source: str, published: str, **overrides) -> Candidate:
    values = dict(
        source=source, source_id="1", title="Técnico informático", organisation="Ayto", url="",
        publication_date=published, access=AccessType.OPEN, status=ProcessStatus.ANNOUNCED,
        raw={"relative_deadline": extract_relative_deadline(RELATIVE)},
    )
    values.update(overrides)
    return Candidate(**values)


@pytest.mark.parametrize(
    ("source", "published", "expected"),
    [
        ("boe", "2026-10-01", True),     # 29 calendar days after a BOE extract
        ("boe", "2026-09-20", False),    # far beyond 20 working days
        ("bop_jaen", "2026-09-20", True),  # BOP bases: the BOE extract comes later
        ("bop_jaen", "2026-08-01", False),
    ],
)
def test_deadline_window_estimate(source, published, expected):
    assert deadline_window_open(relative_candidate(source, published), date(2026, 10, 30)) is expected


@pytest.mark.parametrize(
    "override",
    [
        {"access": AccessType.INTERNAL_ONLY},
        {"status": ProcessStatus.MARKS},
        {"publication_date": None},
        {"deadline_confirmed": True, "deadline": "2026-11-01"},
        {"raw": {}},
    ],
)
def test_deadline_window_never_applies_without_evidence(override):
    assert not deadline_window_open(relative_candidate("boe", "2026-10-20", **override), date(2026, 10, 30))
