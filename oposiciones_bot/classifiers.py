from __future__ import annotations

import re
from datetime import date
from dataclasses import dataclass

from .config import AppConfig
from .models import AccessType, Candidate, Compatibility, FilterDecision, ProcessStatus
from .normalization import normalize_text


STRONG_IT_TERMS = (
    "tecnico auxiliar de informatica",
    "tecnico informatico",
    "tecnico especialista en informatica",
    "tecnico especialista informatica",
    "tecnica informatica",
    "auxiliar de informatica",
    "auxiliar informatico",
    "ayudante de informatica",
    "ayudantes tecnicos informatica",
    "programador informatico",
    "programador",
    "operador informatico",
    "operador de sistemas",
    "tecnico de sistemas",
    "tecnico de sistemas informaticos",
    "soporte informatico",
    "soporte tic",
    "tecnico tic",
    "tecnologias de la informacion",
    "microinformatica",
    "administrador de sistemas",
    "administracion de sistemas informaticos",
    "desarrollo de aplicaciones",
    "desarrollo software",
    "c1 informatica",
    "c2 informatica",
    "c1 2003",
)

EMPLOYMENT_TERMS = (
    "convocatoria",
    "proceso selectivo",
    "oposicion",
    "concurso oposicion",
    "bolsa de empleo",
    "bolsa de trabajo",
    "personal laboral",
    "funcionario interino",
    "plaza",
    "lista de admitidos",
    "fecha de examen",
    "destinos",
    "nombramiento",
)

ACCEPTED_QUALIFICATIONS = (
    "bachiller",
    "tecnico superior",
    "fp de grado superior",
    "formacion profesional de grado superior",
    "ciclo formativo de grado superior",
    "desarrollo de aplicaciones multiplataforma",
    "desarrollo de aplicaciones web",
    "administracion de sistemas informaticos en red",
    "administracion de sistemas informaticos",
    "familia profesional informatica y comunicaciones",
    "educacion secundaria obligatoria",
    "graduado en eso",
    "titulo de tecnico",
)

UNIVERSITY_TERMS = (
    "grado en ingenieria informatica",
    "ingenieria informatica",
    "ingenieria tecnica informatica",
    "titulacion universitaria",
    "licenciatura",
    "diplomatura",
    "titulo universitario",
)


@dataclass(slots=True)
class Evidence:
    value: str
    text: str


def is_tai(text: str) -> bool:
    normalized = normalize_text(text)
    return bool(
        re.search(r"\bcuerpo de tecnicos auxiliares de informatica\b", normalized)
        or re.search(r"\btai\b", normalized)
    )


# Role words accept Spanish plurals and inclusive forms ("Ayudantes técnicos",
# "Técnico/a", "programadora/or"); the old singular-only pattern excluded the
# Junta "C1.2003 Ayudantes técnicos - Opción Informática" process.
_ROLE_WORDS = (
    r"tecnic[oa]s?|auxiliar(?:es)?|ayudantes?|operador(?:a|es|as)?|"
    r"programador(?:a|es|as)?|analistas?|administrador(?:a|es|as)?|soporte|"
    r"desarrollador(?:a|es|as)?|especialistas?|opcion|especialidad"
)
_IT_WORDS = (
    r"informatic[oa]s?|sistemas|tic|software|aplicaciones|programador(?:a|es|as)?|"
    r"microinformatica|redes|ciberseguridad|tecnologias de la informacion"
)
_IT_ROLE_RE = re.compile(rf"\b(?:{_ROLE_WORDS})\b.*\b(?:{_IT_WORDS})\b")
# Official corps codes such as "B1.2000", "C1.2003" or "A2.2012" followed by an
# IT denomination ("Tecnologías de la Información y Comunicación").
_CORPS_IT_RE = re.compile(
    r"\b(?:a1|a2|b1|c1|c2)\s?\d{4}\b.*\b(?:informatic[oa]s?|tecnologias de la informacion|tic)\b"
)


def is_it_role_title(title: str) -> bool:
    """Whether a title names an IT post (not just mentions computers)."""
    role = normalize_text(title)
    return bool(
        _IT_ROLE_RE.search(role)
        or _CORPS_IT_RE.search(role)
        or re.search(r"\banalistas? programador", role)
        or is_tai(role)
    )


def _position_text(candidate: Candidate) -> str:
    """Return text that describes the role, without procedural boilerplate.

    Official documents often mention an ``informatic system`` only to explain
    how applications are submitted.  That must not turn an unrelated vacancy
    into an IT job.
    """
    return normalize_text(
        " ".join(
            part
            for part in (
                candidate.title,
                candidate.summary,
                candidate.group,
                candidate.qualification_text,
            )
            if part
        )
    )


def is_junta_it(candidate: Candidate) -> bool:
    position = _position_text(candidate)
    organisation = normalize_text(candidate.organisation)
    # BOJA also publishes municipal, provincial and university notices.  The
    # publication channel alone is not evidence that the post is from Junta.
    junta_owner = candidate.source == "iaap" or any(
        marker in organisation
        for marker in (
            "junta de andalucia",
            "administracion general de la junta",
            "consejeria",
            "servicio andaluz de salud",
            "instituto andaluz",
            "agencia digital de andalucia",
        )
    )
    return junta_owner and any(
        term in position for term in STRONG_IT_TERMS + ("opcion informatica",)
    )


def classify_relevance(candidate: Candidate) -> bool:
    text = normalize_text(candidate.searchable_text)
    position = _position_text(candidate)
    # A qualification or application portal is not the advertised job.
    role = normalize_text(candidate.title)
    if any(term in role for term in ("administrativo", "gestion administrativa", "gestion de funcion administrativa")):
        if not any(term in role for term in ("opcion informatica", "especialidad informatica", "sistemas informaticos")):
            return False
    if is_tai(position) or is_junta_it(candidate):
        return True
    strong = any(term in position for term in STRONG_IT_TERMS)
    employment = any(term in text for term in EMPLOYMENT_TERMS)
    if strong and employment:
        return True
    weak_hits = sum(
        term in position
        for term in ("informatica", "sistemas informaticos", "tic", "aplicaciones")
    )
    return weak_hits >= 2 and employment


def classify_qualification(text: str, *, allow_standalone: bool = True) -> Evidence:
    normalized = normalize_text(text)
    if not normalized:
        return Evidence(Compatibility.REVIEW.value, "No se encontro titulacion verificable")

    mandatory_sentences = []
    for raw_sentence in re.split(r"[.;\n]", text):
        sentence = normalize_text(raw_sentence)
        if any(
            marker in sentence
            for marker in (
                "requisito",
                "se requiere",
                "estar en posesion",
                "deben estar en posesion",
                "deberan estar en posesion",
                "se exige",
                "debera poseer",
                "titulacion requerida",
                "como minimo",
            )
        ):
            mandatory_sentences.append(sentence)
    # Judge each requirement sentence on its own.  Joining every sentence of a
    # document made a multi-post call (an A2 post asking for a degree and an
    # administrative C1 post asking for Bachiller) look like "degree *or*
    # Bachiller" and therefore compatible.
    compatible_terms: list[str] = []
    university_only: list[str] = []
    mixed_without_alternative = False
    for sentence in mandatory_sentences:
        accepted_here = [term for term in ACCEPTED_QUALIFICATIONS if term in sentence]
        university_here = [term for term in UNIVERSITY_TERMS if term in sentence]
        if accepted_here and not university_here:
            compatible_terms.extend(accepted_here)
        elif accepted_here and university_here:
            alternative = any(
                marker in sentence
                for marker in (" o ", "alternativamente", "equivalente", "cualquiera de")
            )
            if alternative:
                compatible_terms.extend(accepted_here)
            else:
                mixed_without_alternative = True
        elif university_here:
            university_only.extend(university_here)
    compatible_terms = list(dict.fromkeys(compatible_terms))
    university_only = list(dict.fromkeys(university_only))
    if compatible_terms and university_only:
        return Evidence(
            Compatibility.REVIEW.value,
            "El documento exige titulaciones distintas en distintos requisitos "
            "(posible convocatoria de varias plazas); comprueba la de este puesto",
        )
    if compatible_terms:
        return Evidence(Compatibility.COMPATIBLE.value, ", ".join(compatible_terms[:3]))
    if mixed_without_alternative:
        return Evidence(
            Compatibility.REVIEW.value,
            "El requisito obligatorio mezcla titulaciones universitarias y de FP sin alternativa clara",
        )
    if university_only:
        return Evidence(Compatibility.NOT_COMPATIBLE.value, ", ".join(university_only[:3]))

    non_requirement_markers = (
        "merito",
        "se valorara",
        "valoracion",
        "baremo",
        "temario",
        "curso de",
    )
    evidentiary_text = " ".join(
        sentence
        for sentence in (normalize_text(value) for value in re.split(r"[.;\n]", text))
        if not any(marker in sentence for marker in non_requirement_markers)
    )
    accepted = [term for term in ACCEPTED_QUALIFICATIONS if term in evidentiary_text]
    if accepted and allow_standalone:
        return Evidence(Compatibility.COMPATIBLE.value, ", ".join(accepted[:3]))

    if any(term in normalized for term in UNIVERSITY_TERMS):
        return Evidence(
            Compatibility.REVIEW.value,
            "La titulacion universitaria aparece fuera de un requisito obligatorio claro",
        )
    return Evidence(Compatibility.REVIEW.value, "Titulacion ambigua o remitida a otras bases")


def classify_access(text: str) -> AccessType:
    normalized = normalize_text(text)
    has_open = any(
        term in normalized
        for term in (
            "acceso libre",
            "turno libre",
            "plazas libres",
            "ingreso libre",
            "oposicion libre",
            "libre concurrencia",
            "abierta a cualquier aspirante",
            "abierto a cualquier aspirante",
        )
    )
    negated_internal = any(
        phrase in normalized
        for phrase in (
            "no es de promocion interna",
            "no son de promocion interna",
            "no sera de promocion interna",
            "no seran de promocion interna",
            "sin promocion interna",
            "se excluyen las plazas reservadas a promocion interna",
            "se excluye la plaza reservada a promocion interna",
        )
    )
    has_internal = "promocion interna" in normalized and not negated_internal
    if has_open and has_internal:
        return AccessType.MIXED
    if has_open:
        return AccessType.OPEN
    if has_internal:
        return AccessType.INTERNAL_ONLY
    return AccessType.UNKNOWN


def infer_status(text: str) -> ProcessStatus:
    normalized = normalize_text(text)
    rules = (
        (("anulacion", "anulada", "dejar sin efecto"), ProcessStatus.CANCELLED),
        (("suspension", "suspendido", "suspendida"), ProcessStatus.SUSPENDED),
        (("reapertura", "nuevo plazo"), ProcessStatus.REOPENED),
        (("propuesta de nombramiento",), ProcessStatus.APPOINTMENT_PROPOSED),
        (("eleccion de destinos", "destinos adjudicados"), ProcessStatus.DESTINATIONS),
        (("calificaciones", "relacion de aprobados", "notas definitivas", "notas segundo examen", "notas del segundo", "notas primer examen"), ProcessStatus.MARKS),
        (
            (
                "fecha de examen",
                "lugar de examen",
                "examen tendra lugar",
                "ejercicio se celebrara",
                "fecha del ejercicio",
            ),
            ProcessStatus.EXAM_ANNOUNCED,
        ),
        (("lista definitiva de admitidos", "admitidos definitivos"), ProcessStatus.FINAL_ADMITTED),
        (("lista provisional de admitidos", "admitidos provisionales"), ProcessStatus.PROVISIONAL_ADMITTED),
        (("plazo de presentacion", "plazo de solicitudes"), ProcessStatus.OPEN),
        (("convoca proceso selectivo", "convocatoria"), ProcessStatus.ANNOUNCED),
    )
    for needles, status in rules:
        if any(needle in normalized for needle in needles):
            return status
    return ProcessStatus.DETECTED


def geographic_match(candidate: Candidate, config: AppConfig) -> bool:
    text = normalize_text(candidate.searchable_text)
    if candidate.special_process in {"TAI_ESTADO", "JUNTA_INFORMATICA"}:
        return True
    locations = config.get("locations.priority", []) + config.get("locations.nearby", [])
    if any(normalize_text(location) in text for location in locations):
        return True
    return "jaen" in normalize_text(f"{candidate.province} {candidate.locality} {text}")


def calculate_priority(candidate: Candidate, config: AppConfig) -> int:
    if candidate.special_process in {"TAI_ESTADO", "JUNTA_INFORMATICA"}:
        return 5
    text = normalize_text(candidate.searchable_text)
    if "martos" in text:
        return 5
    priority_places = [normalize_text(x) for x in config.get("locations.priority", [])]
    if any(place in text for place in priority_places):
        return 4
    if "jaen" in text:
        return 3
    return 2


def evaluate_candidate(candidate: Candidate, config: AppConfig) -> FilterDecision:
    text = candidate.searchable_text
    position = _position_text(candidate)
    candidate.special_process = (
        "TAI_ESTADO"
        if config.get("special_processes.tai_estado", True) and is_tai(position)
        else "JUNTA_INFORMATICA"
        if config.get("special_processes.junta_informatica", True)
        and is_junta_it(candidate)
        else candidate.special_process
    )
    relevant = classify_relevance(candidate)
    if candidate.access is AccessType.UNKNOWN:
        candidate.access = classify_access(text)
    qualification_parts = [
        part
        for part in (candidate.qualification_text, candidate.full_text)
        if part
    ]
    evidence = classify_qualification(
        "\n".join(qualification_parts),
        allow_standalone=bool(candidate.qualification_text),
    )
    candidate.compatibility = Compatibility(evidence.value)
    milestone_text = candidate.raw.get("current_milestone_text") or " ".join([text, *(link.label.replace("_", " ") for link in candidate.links)])
    candidate.status = infer_status(milestone_text)
    geo = geographic_match(candidate, config)
    candidate.priority = calculate_priority(candidate, config)

    include = (
        relevant
        and geo
        and candidate.compatibility is not Compatibility.NOT_COMPATIBLE
        and candidate.access is not AccessType.INTERNAL_ONLY
    )
    reasons = []
    if config.get("eligibility.require_it_qualification", False):
        qualification = normalize_text(candidate.qualification_text)
        family_terms = (
            "desarrollo de aplicaciones multiplataforma",
            "desarrollo de aplicaciones web",
            "administracion de sistemas informaticos en red",
            "administracion de sistemas informaticos",
            "desarrollo de aplicaciones informaticas",
            "familia profesional informatica",
            "familia profesional de informatica",
            "familia de informatica",
            "rama informatica",
        )
        family_confirmed = any(term in qualification for term in family_terms) or bool(re.search(r"\b(?:dam|daw|asir)\b", qualification))
        if not family_confirmed:
            include = False
            reasons.append("las bases no confirman DAM/DAW/ASIR o familia informatica")
    if config.get("eligibility.require_it_role_in_title", False):
        if not is_it_role_title(candidate.title):
            include = False
            reasons.append("el titulo no identifica un puesto informatico del perfil")
    allowed_groups = config.get("eligibility.allowed_groups", [])
    if allowed_groups and candidate.group.upper().strip() not in allowed_groups:
        include = False
        reasons.append("grupo no permitido o no confirmado (solo B/C1)")
    if config.get("eligibility.require_confirmed_qualification", False) and candidate.compatibility is not Compatibility.COMPATIBLE:
        include = False
        reasons.append("titulacion compatible no confirmada")
    if not relevant:
        reasons.append("sin senal suficiente de puesto informatico")
    if not geo:
        reasons.append("fuera del ambito geografico")
    if candidate.compatibility is Compatibility.NOT_COMPATIBLE:
        reasons.append("titulacion universitaria obligatoria")
    if candidate.access is AccessType.INTERNAL_ONLY:
        reasons.append("promocion interna exclusiva")
    return FilterDecision(
        include=include,
        relevant=relevant,
        geographic_match=geo,
        reason="; ".join(reasons) if reasons else "compatible o requiere revision",
        compatibility=candidate.compatibility,
        access=candidate.access,
        priority=candidate.priority,
    )


def can_apply(candidate: Candidate, today: date) -> bool:
    """Never turn an exam/marks notice into a new enrolment opportunity."""
    if candidate.status not in {ProcessStatus.OPEN, ProcessStatus.REOPENED, ProcessStatus.ANNOUNCED, ProcessStatus.DETECTED}:
        return False
    if candidate.access not in {AccessType.OPEN, AccessType.MIXED}:
        return False
    if not candidate.deadline_confirmed or not candidate.deadline:
        return False
    try:
        if date.fromisoformat(candidate.deadline) < today:
            return False
        if candidate.publication_date and date.fromisoformat(candidate.publication_date) > today:
            return False
    except ValueError:
        return False
    return True


_SOURCE_BULLETIN = {"boe": "BOE", "boja": "BOJA", "bop_jaen": "BOP"}


def deadline_window_open(candidate: Candidate, today: date) -> bool:
    """Whether an official *relative* deadline may still be running.

    Calls usually say "20 días hábiles desde la publicación del extracto en el
    BOE" instead of a date.  This is an estimate used only to raise a REVISAR
    notice (never a confirmed opportunity): working days are approximated as
    7/5 calendar days plus holidays, with a wider margin when the period starts
    with a different, later bulletin.
    """
    relative = candidate.raw.get("relative_deadline") if isinstance(candidate.raw, dict) else None
    if not relative or candidate.deadline_confirmed:
        return False
    if candidate.status not in {ProcessStatus.OPEN, ProcessStatus.REOPENED, ProcessStatus.ANNOUNCED, ProcessStatus.DETECTED}:
        return False
    if candidate.access is AccessType.INTERNAL_ONLY or not candidate.publication_date:
        return False
    try:
        published = date.fromisoformat(candidate.publication_date)
        days = int(relative.get("days") or 0)
    except (TypeError, ValueError):
        return False
    if published > today or days <= 0:
        return False
    calendar_days = days if relative.get("kind") == "naturales" else -(-days * 7 // 5) + 4
    starts_after = relative.get("starts_after") or ""
    own_bulletin = _SOURCE_BULLETIN.get(candidate.source, "")
    margin = 3 if starts_after and starts_after == own_bulletin else 20
    return (today - published).days <= calendar_days + margin


def reviewable(candidate: Candidate, config: AppConfig) -> bool:
    """Keep uncertain IT posts, never confirmed excluded groups/university posts."""
    from copy import deepcopy
    relaxed = deepcopy(config.data)
    rules = relaxed.setdefault("eligibility", {})
    rules["require_it_qualification"] = False
    rules["require_confirmed_qualification"] = False
    if not candidate.group:
        rules["allowed_groups"] = []
    probe = Candidate.from_dict(candidate.to_dict())
    return evaluate_candidate(probe, AppConfig(relaxed, config.path)).include
