from __future__ import annotations

import re
from typing import Any

from rapidfuzz.fuzz import token_set_ratio

from .models import Candidate
from .normalization import extract_year, normalize_reference, normalize_text, stable_hash


GENERIC_TITLE_WORDS = re.compile(
    r"\b(resolucion|orden|anuncio|decreto|publica|publicacion|por la que|se convoca|"
    r"convocatoria|correccion|errores?|lista|provisional|definitiva|fecha|examen|"
    r"admitidos?|excluidos?|bases?)\b"
)


def normalized_references(candidate: Candidate) -> list[str]:
    # Only the publication's own primary reference is a global identity key.
    # References merely cited in the body (laws, earlier bulletins, corps codes)
    # are evidence, not identity, and can legitimately occur in many processes.
    values = [candidate.reference] if candidate.reference else candidate.official_references[:1]
    result: list[str] = []
    for value in values:
        normalized = normalize_reference(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def title_signature(title: str) -> str:
    normalized = normalize_text(title)
    normalized = GENERIC_TITLE_WORDS.sub(" ", normalized)
    normalized = re.sub(r"\b(?:19|20)\d{2}\b", " ", normalized)
    normalized = re.sub(r"\b\d{1,5}\b", " ", normalized)
    return " ".join(normalized.split())


def canonical_key(candidate: Candidate) -> str:
    year = extract_year(candidate.publication_date or "") or extract_year(candidate.title)
    data = {
        "organisation": normalize_text(candidate.organisation),
        "position": title_signature(candidate.title),
        "year": year,
        "access": candidate.access.value,
        "group": normalize_text(candidate.group),
    }
    return stable_hash(data)


def semantic_snapshot(candidate: Candidate) -> dict[str, Any]:
    return {
        "positions": candidate.positions,
        "access": candidate.access.value,
        "qualification": normalize_text(candidate.qualification_text),
        "compatibility": candidate.compatibility.value,
        "deadline": candidate.deadline,
        "deadline_confirmed": bool(candidate.deadline_confirmed),
        "status": candidate.status.value,
        "exam_date": candidate.exam_date,
        "exam_time": candidate.exam_time,
        "exam_place": normalize_text(candidate.exam_place),
        "group": normalize_text(candidate.group),
        "locality": normalize_text(candidate.locality),
        "province": normalize_text(candidate.province),
        "priority": candidate.priority,
    }


def changed_fields(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    labels = {
        "positions": "numero de plazas",
        "access": "via de acceso",
        "qualification": "titulacion",
        "compatibility": "compatibilidad con FP",
        "deadline": "fecha limite",
        "deadline_confirmed": "confirmacion del plazo",
        "status": "estado del proceso",
        "exam_date": "fecha de examen",
        "exam_time": "hora de examen",
        "exam_place": "lugar de examen",
        "group": "grupo",
        "locality": "localidad",
        "province": "provincia",
        "priority": "prioridad",
    }
    return [labels.get(key, key) for key in new if old.get(key) != new.get(key)]


def fuzzy_match_score(candidate: Candidate, process: dict[str, Any]) -> float:
    candidate_year = extract_year(candidate.publication_date or "") or extract_year(
        candidate.title
    )
    process_year = extract_year(str(process.get("publication_date") or "")) or extract_year(
        str(process.get("title") or "")
    )
    if candidate_year and process_year and candidate_year != process_year:
        return 0.0
    if normalize_text(candidate.organisation) != process.get("normalised_organisation", ""):
        return 0.0
    title_score = token_set_ratio(
        title_signature(candidate.title),
        process.get("title_signature", ""),
    )
    if candidate.group and process.get("group_name"):
        if normalize_text(candidate.group) != normalize_text(process["group_name"]):
            return 0.0
    if candidate.positions is not None and process.get("positions") is not None:
        if int(candidate.positions) != int(process["positions"]):
            return 0.0
    if candidate.access.value not in {"REVISAR", process.get("access")} and process.get("access") != "REVISAR":
        return 0.0
    return title_score / 100.0


def explicit_relation_score(candidate: Candidate, process: dict[str, Any]) -> float:
    """Score a notice that explicitly cites a tracked primary publication.

    Corrected fields such as the number of places must not block the relation;
    issuer, year, role, group and access remain hard guards.
    """
    candidate_year = extract_year(candidate.publication_date or "") or extract_year(
        candidate.title
    )
    process_year = extract_year(str(process.get("publication_date") or "")) or extract_year(
        str(process.get("title") or "")
    )
    if candidate_year and process_year and candidate_year != process_year:
        return 0.0
    if normalize_text(candidate.organisation) != process.get("normalised_organisation", ""):
        return 0.0
    if candidate.group and process.get("group_name"):
        if normalize_text(candidate.group) != normalize_text(process["group_name"]):
            return 0.0
    if candidate.access.value not in {"REVISAR", process.get("access")}:
        if process.get("access") != "REVISAR":
            return 0.0
    return token_set_ratio(
        title_signature(candidate.title),
        process.get("title_signature", ""),
    ) / 100.0

