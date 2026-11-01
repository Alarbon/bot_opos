"""Read process fields, excluding unrelated announcements and site navigation."""
from __future__ import annotations

import re
from bs4 import BeautifulSoup

from ..classifiers import classify_access
from ..enrichment import extract_group
from ..models import Candidate
from ..normalization import normalize_text, parse_date


def apply_process_fields(candidate: Candidate, soup: BeautifulSoup) -> bool:
    def field(name: str) -> str:
        node = soup.select_one(f".field--name-field-{name} .field__item")
        return node.get_text(" ", strip=True) if node else ""

    access = field("tipo-de-acceso")
    if not access:
        return False
    candidate.access = classify_access(access)
    total = field("plazas-totales")
    candidate.positions = int(total) if re.fullmatch(r"\d+", total) else None
    candidate.group = extract_group(candidate.title)
    candidate.organisation = "Junta de Andalucía — Dirección General de Recursos Humanos y Función Pública"
    # Listing/update times and the first date of a range are not publication
    # and closing dates. Only an explicitly labelled application period counts.
    candidate.publication_date = None
    candidate.deadline = None
    candidate.deadline_confirmed = False
    for row in soup.select("tr"):
        label = row.select_one("td.views-field-field-tipo-de-plazo")
        if label and normalize_text(label.get_text(" ", strip=True)) == "presentacion de solicitudes de participacion":
            dates = row.select("time[datetime]")
            if len(dates) == 2:
                candidate.deadline = parse_date(str(dates[-1]["datetime"])[:10])
                candidate.deadline_confirmed = bool(candidate.deadline)
    state = field("estado")
    candidate.raw["current_milestone_text"] = state
    candidate.raw["structured_evidence"] = {
        "url": candidate.url, "access": access, "positions": total,
        "state": state, "deadline": candidate.deadline,
    }
    return True
