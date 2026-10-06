from __future__ import annotations

import logging
import math
import re
from datetime import timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..enrichment import enrich_candidate
from ..models import Candidate, SourceLink
from ..normalization import clean_text, parse_date
from ..parsers import html_to_text
from .base import FetchContext, SourceAdapter, has_it_signal


LOGGER = logging.getLogger(__name__)


class PAGSource(SourceAdapter):
    name = "pag"
    base_url = "https://administracion.gob.es"
    endpoint = (
        "https://administracion.gob.es/content/pag-home/es/empleopublico/"
        "resultadosEmpleo/jcr:content/root/container/containerSpace/"
        "pag_front_formulario.list.html"
    )

    def fetch(self, context: FetchContext) -> list[Candidate]:
        start = context.today - timedelta(days=context.lookback_days)
        params = {
            "pag_viaGrupo": "2",
            "pag_fecha": "intervalo",
            "fechaDesde": start.strftime("%d-%m-%Y"),
            "fechaHasta": context.today.strftime("%d-%m-%Y"),
            "pag_sort": "desc",
            "numRegistrosMostrar": "10",
        }
        max_pages = int(context.source_config.get("max_pages", 30))
        candidates: list[Candidate] = []
        seen_ids: set[str] = set()
        total_pages = 1
        for page in range(1, max_pages + 1):
            if page > total_pages:
                break
            page_params = dict(params, p=str(page))
            html = self.client.get_text(self.endpoint, params=page_params)
            soup = BeautifulSoup(html, "lxml")
            content = soup.select_one(".dnt-load-content")
            if content:
                total = int(content.get("data-load-total", "0") or 0)
                size = int(content.get("data-load-size", "10") or 10)
                total_pages = max(1, math.ceil(total / max(size, 1)))
            cards = soup.select(".pag-card-convo")
            if not cards:
                break
            for card in cards:
                anchor = card.select_one(".pag-title a[href]")
                if not anchor:
                    continue
                href = urljoin(self.base_url, anchor.get("href", ""))
                match = re.search(r"selectorget=(\d+)", href)
                source_id = match.group(1) if match else href
                if source_id in seen_ids:
                    continue
                seen_ids.add(source_id)
                title = clean_text(anchor.get_text(" ", strip=True))
                card_text = clean_text(card.get_text(" ", strip=True))
                if not self._potentially_relevant(f"{title} {card_text}"):
                    continue
                full_text = ""
                try:
                    full_text = html_to_text(self.client.get_text(href))
                except Exception as exc:
                    LOGGER.warning("PAG: no se pudo ampliar %s: %s", source_id, exc)
                organisation = self._field(card_text, "Órgano convocante:")
                qualification = self._field(card_text, "Titulación:")
                location = self._field(card_text, "Ubicación:")
                deadline = parse_date(self._field(card_text, "Fin de plazo:"))
                candidate = Candidate(
                    source=self.name,
                    source_id=source_id,
                    reference=source_id,
                    official_references=[f"PAG{source_id}"],
                    title=title,
                    organisation=organisation or "Punto de Acceso General",
                    url=href,
                    summary=card_text,
                    full_text=full_text,
                    qualification_text=qualification,
                    locality=location,
                    deadline=deadline,
                    deadline_confirmed=bool(deadline),
                    links=[SourceLink(self.name, href, source_id, "Punto de Acceso General")],
                    raw={"card": card_text},
                )
                pub_match = re.search(
                    r"Fecha de publicaci[oó]n\s*[:*]?\s*(\d{1,2}/\d{1,2}/\d{4})",
                    full_text,
                    re.IGNORECASE,
                )
                if pub_match:
                    candidate.publication_date = parse_date(pub_match.group(1))
                candidates.append(enrich_candidate(candidate))
        return candidates

    @staticmethod
    def _field(text: str, label: str) -> str:
        labels = (
            "Ref.:",
            "Fin de plazo:",
            "Titulación:",
            "Ubicación:",
            "Órgano convocante:",
            "Plazas:",
        )
        start = text.find(label)
        if start < 0:
            return ""
        start += len(label)
        end = len(text)
        for next_label in labels:
            position = text.find(next_label, start)
            if position >= 0:
                end = min(end, position)
        return clean_text(text[start:end])

    @staticmethod
    def _potentially_relevant(text: str) -> bool:
        return has_it_signal(text)

