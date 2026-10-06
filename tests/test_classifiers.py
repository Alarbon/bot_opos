from __future__ import annotations

import pytest

from oposiciones_bot.classifiers import (
    classify_access,
    classify_qualification,
    classify_relevance,
    evaluate_candidate,
)
from oposiciones_bot.config import AppConfig
from oposiciones_bot.models import AccessType, Candidate, Compatibility


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Se requiere Bachiller o Técnico", Compatibility.COMPATIBLE),
        (
            "Técnico Superior de la familia profesional de Informática y Comunicaciones",
            Compatibility.COMPATIBLE,
        ),
        (
            "Requisito: estar en posesión del Grado en Ingeniería Informática",
            Compatibility.NOT_COMPATIBLE,
        ),
        (
            "Se valorará como mérito el Grado en Ingeniería Informática",
            Compatibility.REVIEW,
        ),
        ("Titulación conforme al artículo 76 del TREBEP", Compatibility.REVIEW),
        ("", Compatibility.REVIEW),
    ],
)
def test_qualification_classification(text: str, expected: Compatibility) -> None:
    assert classify_qualification(text).value == expected.value


def test_university_requirement_is_not_made_compatible_by_unrelated_fp_mention() -> None:
    text = (
        "Requisito: estar en posesión del Grado en Ingeniería Informática. "
        "Se valorará como mérito adicional el título de Técnico Superior."
    )

    assert classify_qualification(text).value == Compatibility.NOT_COMPATIBLE.value


def test_fp_mentioned_only_as_merit_does_not_prove_eligibility() -> None:
    text = (
        "Méritos: se valorará Técnico Superior en Informática. "
        "La titulación requerida figura en el anexo."
    )

    assert classify_qualification(text).value == Compatibility.REVIEW.value


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Convocatoria por turno libre", AccessType.OPEN),
        ("Una plaza por el sistema de oposición libre", AccessType.OPEN),
        ("Tres plazas de acceso libre y dos de promoción interna", AccessType.MIXED),
        ("Proceso exclusivo de promoción interna", AccessType.INTERNAL_ONLY),
        (
            "La plaza no es de promoción interna; está abierta a cualquier aspirante",
            AccessType.OPEN,
        ),
        ("Se excluyen las plazas reservadas a promoción interna", AccessType.UNKNOWN),
        ("Convocatoria de una plaza", AccessType.UNKNOWN),
    ],
)
def test_access_classification(text: str, expected: AccessType) -> None:
    assert classify_access(text) is expected


def test_relevance_requires_it_role_and_public_employment_signal(
    candidate_factory,
) -> None:
    relevant = candidate_factory(
        title="Convocatoria de Técnico de Sistemas Informáticos",
        summary="Proceso selectivo para una plaza",
    )
    generic_tic = candidate_factory(
        title="Convocatoria de subvenciones para modernización TIC",
        organisation="Diputación Provincial de Jaén",
        summary="Ayudas para empresas",
        full_text="",
        qualification_text="",
    )
    role_without_process = candidate_factory(
        title="Curso para Técnico Informático",
        summary="Formación no vinculada a empleo público",
        full_text="",
    )

    assert classify_relevance(relevant)
    assert not classify_relevance(generic_tic)
    assert not classify_relevance(role_without_process)


def test_tai_is_kept_without_a_known_destination(
    candidate_factory,
    app_config: AppConfig,
) -> None:
    candidate = candidate_factory(
        title="Convocatoria del Cuerpo de Técnicos Auxiliares de Informática",
        organisation="Administración General del Estado",
        locality="",
        province="",
        scope="ESTATAL",
        full_text="Convocatoria por ingreso libre.",
    )

    decision = evaluate_candidate(candidate, app_config)

    assert candidate.special_process == "TAI_ESTADO"
    assert decision.geographic_match
    assert decision.include
    assert decision.priority == 5


def test_junta_it_is_kept_without_a_province(
    candidate_factory,
    app_config: AppConfig,
) -> None:
    candidate = candidate_factory(
        source="boja",
        source_id="BOJA-2026-99",
        title="Convocatoria C1.2003, opción Informática",
        organisation="Junta de Andalucía",
        locality="",
        province="",
        scope="AUTONOMICO",
    )

    decision = evaluate_candidate(candidate, app_config)

    assert candidate.special_process == "JUNTA_INFORMATICA"
    assert decision.geographic_match
    assert decision.include


def test_special_junta_exception_can_be_disabled(
    candidate_factory,
    app_config: AppConfig,
) -> None:
    app_config.data["special_processes"] = {"junta_informatica": False}
    candidate = candidate_factory(
        source="boja",
        title="Convocatoria C1.2003, opción Informática",
        organisation="Junta de Andalucía",
        locality="",
        province="",
        scope="AUTONOMICO",
    )

    decision = evaluate_candidate(candidate, app_config)

    assert candidate.special_process == ""
    assert not decision.geographic_match
    assert not decision.include


def test_boja_local_entity_is_not_treated_as_junta_it(
    candidate_factory,
    app_config: AppConfig,
) -> None:
    candidate = candidate_factory(
        source="boja",
        source_id="BOJA-2026-LOCAL",
        title="Convocatoria de una plaza de Técnico Informático",
        organisation="Diputaciones",
        summary="Oferta de empleo de una entidad de Sevilla",
        full_text="Convocatoria por acceso libre para Prodetur, Sevilla.",
        locality="Sevilla",
        province="Sevilla",
        scope="AUTONOMICO",
    )

    decision = evaluate_candidate(candidate, app_config)

    assert candidate.special_process == ""
    assert not decision.geographic_match
    assert not decision.include


def test_incidental_computer_system_in_full_text_is_not_an_it_job(
    candidate_factory,
    app_config: AppConfig,
) -> None:
    candidate = candidate_factory(
        source="boja",
        source_id="BOJA-2026-SAS",
        title="Convocatoria de Jefe de Equipo de Gestión y Servicios",
        organisation="Consejería de Salud",
        summary="Proceso selectivo para un centro sanitario de Andújar",
        full_text=(
            "Las solicitudes se presentan mediante el sistema informático. "
            "Convocatoria por acceso libre."
        ),
        qualification_text="Bachiller o título de Técnico",
        locality="Andújar",
        province="Jaén",
        scope="AUTONOMICO",
    )

    decision = evaluate_candidate(candidate, app_config)

    assert candidate.special_process == ""
    assert not decision.relevant
    assert not decision.include


def test_internal_only_process_is_excluded(
    candidate_factory,
    app_config: AppConfig,
) -> None:
    candidate = candidate_factory(
        access=AccessType.UNKNOWN,
        full_text=(
            "Convocatoria de Técnico Informático exclusivamente por promoción interna."
        ),
    )

    decision = evaluate_candidate(candidate, app_config)

    assert decision.access is AccessType.INTERNAL_ONLY
    assert not decision.include
    assert "promocion interna" in decision.reason


def test_mandatory_university_degree_is_excluded(
    candidate_factory,
    app_config: AppConfig,
) -> None:
    candidate = candidate_factory(
        qualification_text=(
            "Requisito: estar en posesión del Grado en Ingeniería Informática"
        )
    )

    decision = evaluate_candidate(candidate, app_config)

    assert decision.compatibility is Compatibility.NOT_COMPATIBLE
    assert not decision.include


def test_job_title_does_not_count_as_fp_qualification(
    candidate_factory,
    app_config: AppConfig,
) -> None:
    candidate = candidate_factory(
        title="Convocatoria de Técnico Superior de Informática",
        summary="Proceso selectivo para una plaza",
        full_text="Las bases completas se publicarán en el tablón.",
        qualification_text="",
    )

    decision = evaluate_candidate(candidate, app_config)

    assert decision.compatibility is Compatibility.REVIEW


def test_late_mandatory_university_requirement_overrides_job_title(
    candidate_factory,
    app_config: AppConfig,
) -> None:
    candidate = candidate_factory(
        title="Convocatoria de Técnico Superior de Informática",
        summary="Proceso selectivo para una plaza",
        full_text=(
            "Descripción del puesto. " + ("contenido administrativo " * 80)
            + "Requisito: estar en posesión del Grado en Ingeniería Informática."
        ),
        qualification_text="",
    )

    decision = evaluate_candidate(candidate, app_config)

    assert decision.compatibility is Compatibility.NOT_COMPATIBLE
    assert not decision.include
