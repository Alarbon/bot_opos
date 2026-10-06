from __future__ import annotations

import re

from .models import AccessType, Candidate
from .normalization import clean_text, normalize_reference, normalize_text, parse_date


def _context(text: str, needle_pattern: str, before: int = 80, after: int = 420) -> str:
    match = re.search(needle_pattern, text, flags=re.IGNORECASE)
    if not match:
        return ""
    start = max(0, match.start() - before)
    end = min(len(text), match.end() + after)
    return clean_text(text[start:end])


def extract_qualification(text: str) -> str:
    return _context(
        text,
        r"requisitos?\s+(?:de\s+)?titulaci[oó]n|titulaci[oó]n\s+requerida|"
        r"estar en posesi[oó]n|se requiere|deber[aá] poseer",
        before=50,
        after=650,
    )


def extract_group(text: str) -> str:
    normalized = normalize_text(text)
    match = re.search(r"\b(?:subgrupo|grupo)\s*(a1|a2|b|c1|c2|e)\b", normalized)
    if match:
        return match.group(1).upper()
    match = re.search(r"\b(a1|a2|c1|c2)\s*[./-]\s*\d{3,5}\b", normalized)
    return match.group(1).upper() if match else ""


def extract_positions(text: str) -> int | None:
    normalized = normalize_text(text)
    patterns = (
        r"\b(\d{1,5})\s+plazas?\b",
        r"\bplazas?\s*[:.-]?\s*(\d{1,5})\b",
        r"\b(una|dos|tres|cuatro|cinco)\s+plazas?\b",
    )
    words = {"una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            value = match.group(1)
            return words.get(value, int(value) if value.isdigit() else None)
    return None


def extract_deadline(text: str) -> tuple[str | None, bool]:
    normalized = clean_text(text)
    range_match = re.search(
        r"(?:plazo de presentaci[oó]n|solicitudes?)[^.\n]{0,100}?"
        r"(?:del|desde)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})\s*"
        r"(?:al|hasta(?:\s+el)?)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})(?!\d)",
        normalized,
        re.IGNORECASE,
    )
    if range_match:
        return parse_date(range_match.group(2)), True
    patterns = (
        r"(?:fin(?:al)?\s+de(?:l)?\s+plazo|hasta el|plazo de presentaci[oó]n)"
        r"[^.\n]{0,120}?(?<!\d)(\d{1,2}[/-]\d{1,2}[/-]\d{4})(?!\d)",
        r"solicitudes?[^.\n]{0,100}?(?<!\d)"
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})(?!\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            return parse_date(match.group(1)), True
    return None, False


def extract_exam_date(text: str) -> str | None:
    patterns = (
        r"(?:fecha|celebraci[oó]n)[^.\n]{0,80}?(?:examen|ejercicio|prueba)"
        r"[^.\n]{0,100}?(?<!\d)(\d{1,2}[/-]\d{1,2}[/-]\d{4})(?!\d)",
        r"(?:examen|ejercicio|prueba)[^.\n]{0,100}?"
        r"(?<!\d)(\d{1,2}[/-]\d{1,2}[/-]\d{4})(?!\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return parse_date(match.group(1))
    return None


def extract_exam_time(text: str) -> str | None:
    marker = re.search(r"examen|ejercicio|prueba", text, re.IGNORECASE)
    if not marker:
        return None
    context = text[max(0, marker.start() - 100) : marker.end() + 500]
    match = re.search(
        r"(?:a\s+las|hora(?:\s+de\s+inicio)?\s*[:.-]?)\s*"
        r"([01]?\d|2[0-3])[:.]([0-5]\d)",
        context,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\s*h?\b", context)
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else None


def extract_exam_place(text: str) -> str:
    marker = re.search(r"examen|ejercicio|prueba", text, re.IGNORECASE)
    if not marker:
        return ""
    context = clean_text(text[max(0, marker.start() - 100) : marker.end() + 500])
    explicit = re.search(
        r"(?:lugar|sede)(?:\s+de\s+celebraci[oó]n)?\s*[:.-]\s*"
        r"(.{2,120}?)(?=\s+(?:a\s+las\s+)?\d{1,2}[:.]\d{2}|[.;]|$)",
        context,
        re.IGNORECASE,
    )
    if explicit:
        return clean_text(explicit.group(1))
    classroom = re.search(
        r"\b(aula\s+[\w-]+(?:\s+[\w-]+){0,5}?)"
        r"(?=\s+(?:a\s+las\s+)?\d{1,2}[:.]\d{2}|[.;]|$)",
        context,
        re.IGNORECASE,
    )
    return clean_text(classroom.group(1)) if classroom else ""


def extract_official_references(text: str) -> list[str]:
    patterns = (
        r"\bBOE-[AB]-\d{4}-\d+\b",
        r"\bBOP[- ](?:\d{4})[- ]\d+\b",
    )
    references: list[str] = []
    for pattern in patterns:
        for value in re.findall(pattern, text or "", re.IGNORECASE):
            normalized = normalize_reference(value)
            if normalized and normalized not in references:
                references.append(normalized)
    return references


def enrich_candidate(candidate: Candidate) -> Candidate:
    # Each layer can hold unique evidence: listings often put the role and
    # number of places in the title while a detail endpoint returns only the
    # current milestone.  Choosing just the first non-empty layer loses data.
    text = clean_text(
        " ".join(
            part
            for part in (candidate.title, candidate.summary, candidate.full_text)
            if part
        )
    )
    if not candidate.qualification_text:
        # A role named "Tecnico Superior" is not evidence that the applicant's
        # required credential is FP Superior. Search only the descriptive
        # material and only around explicit requirement markers.
        qualification_source = clean_text(
            " ".join(
                part for part in (candidate.summary, candidate.full_text) if part
            )
        )
        candidate.qualification_text = extract_qualification(qualification_source)
    if not candidate.group:
        candidate.group = extract_group(text)
    if candidate.positions is None:
        candidate.positions = extract_positions(text)
    if candidate.deadline is None:
        candidate.deadline, candidate.deadline_confirmed = extract_deadline(text)
    if candidate.exam_date is None:
        candidate.exam_date = extract_exam_date(text)
    if candidate.exam_time is None:
        candidate.exam_time = extract_exam_time(text)
    if not candidate.exam_place:
        candidate.exam_place = extract_exam_place(text)
    if candidate.access is AccessType.UNKNOWN:
        # Keep access semantics in one place so negated mentions such as
        # ``no es de promocion interna`` are not treated as exclusions.
        from .classifiers import classify_access

        candidate.access = classify_access(text)
    discovered_refs = extract_official_references(text)
    for reference in discovered_refs:
        cited = candidate.raw.setdefault("cited_references", [])
        if reference not in cited:
            cited.append(reference)
    identity_references: list[str] = []
    for reference in (candidate.reference, *candidate.official_references):
        normalized_ref = normalize_reference(reference)
        if normalized_ref and normalized_ref not in identity_references:
            identity_references.append(normalized_ref)
    candidate.official_references = identity_references
    return candidate

